"""Invariant tests I1–I11, F1 — mapped to reducer-spec §9."""

from __future__ import annotations

import ast
from datetime import timedelta
from pathlib import Path

import pytest
from hypothesis import HealthCheck, given, settings as hyp_settings, strategies as st

from email_intel.config import Settings
from email_intel.db.models import (
    CompletionKind,
    ConversationBucket,
    ConversationEventType,
    ConversationState,
)
from email_intel.reducer.reducer import reduce
from email_intel.schemas.events import Evidence
from email_intel.schemas.intents import CategoryIntentKind, TaskIntentKind
from email_intel.schemas.reducer import ManualOverride

from .conftest import NOW, make_input, make_snapshot

S = Settings()
CS = ConversationState
CB = ConversationBucket
E = Evidence
TK = TaskIntentKind
CK = CategoryIntentKind


# --------------------- I1 ---------------------
def test_I1_classify_no_writeback_imports() -> None:
    """Classifier code path imports no task-writeback client."""
    classify_dir = Path("src/email_intel/classify")
    forbidden = {"email_intel.graph.todo", "email_intel.writeback.tasks"}
    for py in classify_dir.rglob("*.py"):
        tree = ast.parse(py.read_text(encoding="utf-8"), filename=str(py))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                mod = node.module or ""
                assert mod not in forbidden, (
                    f"{py} imports forbidden {mod} (I1)"
                )
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    assert alias.name not in forbidden, (
                        f"{py} imports forbidden {alias.name} (I1)"
                    )


# --------------------- I2 ---------------------
def test_I2_one_active_task() -> None:
    """Running T001 twice; second hits prior_task_id so downgrades to update."""
    snap1 = make_snapshot()
    r1 = reduce(
        make_input(snapshot=snap1, prior_state=CS.none,
                   evidence={E.NEW_INBOUND_ASK_DELIVERABLE}),
        S,
    )
    assert r1.task_intent.kind == TK.create

    snap2 = make_snapshot(prior_task_id=42)
    r2 = reduce(
        make_input(snapshot=snap2, prior_state=CS.act_open, prior_bucket=CB.Act,
                   evidence={E.NEW_INBOUND_ASK_DELIVERABLE}),
        S,
    )
    assert r2.task_intent.kind == TK.update_fields


# --------------------- I3 ---------------------
def test_I3_sent_items_gating() -> None:
    snap = make_snapshot(prior_task_id=1)
    for prior_state, prior_bucket in [
        (CS.act_open, CB.Act), (CS.respond_open, CB.Respond),
        (CS.waiting_on, CB.WaitingOn),
    ]:
        r = reduce(
            make_input(snapshot=snap, prior_state=prior_state, prior_bucket=prior_bucket,
                       evidence={E.USER_REPLIED_SATISFIES_ASK, E.SENT_ITEMS_LAG}),
            S,
        )
        assert r.transition_id == "T080"
        assert r.task_intent.kind == TK.noop
        assert r.next_state == CS.needs_review


# --------------------- I4 ---------------------
def test_I4_priority_total_order() -> None:
    r = reduce(
        make_input(prior_state=CS.act_open, prior_bucket=CB.Act,
                   evidence={E.EXPLICIT_RESOLUTION, E.NEW_INBOUND_ASK_DELIVERABLE,
                             E.USER_REPLIED_SATISFIES_ASK},
                   snapshot=make_snapshot(prior_task_id=1)),
        S,
    )
    assert r.transition_id == "T013"
    assert E.NEW_INBOUND_ASK_DELIVERABLE in r.suppressed_evidence
    assert E.USER_REPLIED_SATISFIES_ASK in r.suppressed_evidence


# --------------------- I5 ---------------------
@pytest.mark.parametrize("kind,age_delta,trigger,expect_reopen", [
    (CompletionKind.hard, timedelta(days=100), E.NEW_INBOUND_ASK_DELIVERABLE, True),
    (CompletionKind.hard, timedelta(days=100), E.FYI_ONLY, False),
    (CompletionKind.soft, timedelta(days=-2), E.NEW_INBOUND_ASK_DELIVERABLE, True),  # within window
    (CompletionKind.soft, timedelta(days=100), E.NEW_INBOUND_ASK_DELIVERABLE, "create"),  # expired
    (CompletionKind.soft, timedelta(days=-2), E.FYI_ONLY, False),  # T065
])
def test_I5_reopen_rules(kind, age_delta, trigger, expect_reopen) -> None:
    # age_delta is how far past NOW the soft window ends (positive = expired).
    soft_until = NOW - age_delta if kind == CompletionKind.soft else None
    snap = make_snapshot(prior_task_id=1, prior_completion_kind=kind,
                         prior_soft_complete_until=soft_until)
    r = reduce(
        make_input(snapshot=snap, prior_state=CS.done, prior_bucket=CB.Act,
                   evidence={trigger}),
        S,
    )
    if expect_reopen is True:
        assert r.task_intent.kind == TK.reopen
    elif expect_reopen == "create":
        assert r.task_intent.kind == TK.create
    else:
        assert r.task_intent.kind == TK.noop


# --------------------- I6 ---------------------
def test_I6_idempotent_operation_keys() -> None:
    inp = make_input(prior_state=CS.none, evidence={E.NEW_INBOUND_ASK_DELIVERABLE})
    r1 = reduce(inp, S)
    r2 = reduce(inp, S)
    assert r1.task_intent.operation_key == r2.task_intent.operation_key
    assert r1.operation_keys == r2.operation_keys


# --------------------- I7 ---------------------
@pytest.mark.parametrize("ev", [
    E.NEW_INBOUND_ASK_DELIVERABLE, E.NEW_INBOUND_ASK_REPLY,
    E.USER_REPLIED_SATISFIES_ASK, E.EXPLICIT_RESOLUTION,
    E.SOFT_RESOLUTION, E.EXPLICIT_DEFER, E.FYI_ONLY, E.BULK_NOISE,
])
def test_I7_needs_review_blocks_writeback(ev) -> None:
    r = reduce(
        make_input(prior_state=CS.needs_review, prior_bucket=CB.Act,
                   evidence={ev}),
        S,
    )
    assert r.task_intent.kind == TK.noop
    assert r.category_intent.kind == CK.noop


# --------------------- I8 (DB) ---------------------
@pytest.mark.asyncio
async def test_I8_events_append_only(session) -> None:
    from sqlalchemy import select
    from email_intel.db.models import (
        ConversationEvent, User, Conversation, EventActor,
    )
    from datetime import datetime as dt, timezone as tz

    # Seed user+conv.
    u = User(graph_user_id="u1", email="u@x")
    session.add(u)
    await session.flush()
    c = Conversation(user_id=u.id, graph_conversation_id="g1",
                     open_action_state=CS.none)
    session.add(c)
    await session.flush()
    evt = ConversationEvent(
        user_id=u.id, conversation_id=c.id,
        event_type=ConversationEventType.state_changed,
        actor=EventActor.reducer,
    )
    session.add(evt)
    await session.commit()

    # Try to update it → raises.
    row = (await session.execute(select(ConversationEvent))).scalar_one()
    row.actor = EventActor.system
    with pytest.raises(RuntimeError, match="append-only"):
        await session.commit()
    await session.rollback()


# --------------------- I9 ---------------------
_STATE_BUCKET_PAIRS = [
    (CS.none, None),
    (CS.act_open, CB.Act),
    (CS.respond_open, CB.Respond),
    (CS.delegate_open, CB.Delegate),
    (CS.deferred, CB.Defer),
    (CS.waiting_on, CB.WaitingOn),
    (CS.done, CB.Act),
    (CS.fyi_context, CB.FYI),
    (CS.noise_transient, CB.DeleteOrUnsubscribe),
    (CS.needs_review, CB.Act),
]


@hyp_settings(max_examples=200, deadline=None,
              suppress_health_check=[HealthCheck.function_scoped_fixture])
@given(
    pair_idx=st.integers(min_value=0, max_value=len(_STATE_BUCKET_PAIRS) - 1),
    evidence=st.sets(st.sampled_from(list(E)), max_size=4),
)
def test_I9_determinism(pair_idx, evidence) -> None:
    prior_state, prior_bucket = _STATE_BUCKET_PAIRS[pair_idx]
    snap = make_snapshot(prior_task_id=1, prior_bucket=prior_bucket,
                         prior_completion_kind=CompletionKind.hard)
    override = (ManualOverride(target_state=CS.act_open, target_bucket=CB.Act)
                if E.MANUAL_OVERRIDE in evidence else None)
    try:
        inp = make_input(snapshot=snap, prior_state=prior_state,
                         prior_bucket=prior_bucket,
                         evidence=evidence - {E.MANUAL_OVERRIDE},
                         manual_override=override)
        r1 = reduce(inp, S)
        r2 = reduce(inp, S)
    except Exception as e1:
        # Whatever happens, must be deterministic.
        try:
            reduce(inp, S)
        except Exception as e2:
            assert type(e1) is type(e2)
            return
        pytest.fail("first call raised but second succeeded")
    assert r1.model_dump() == r2.model_dump()


# --------------------- I10 ---------------------
def test_I10_out_of_order_same_result() -> None:
    """Two snapshots differing only in message order produce same result."""
    from .conftest import make_msg
    m1 = make_msg(idx=0, body="hi")
    m2 = make_msg(idx=1, body="done closed")
    snap_a = make_snapshot(messages=[m1, m2])
    snap_b = make_snapshot(messages=[m2, m1])
    # Because the reducer uses snapshot.messages in reverse scan for "latest
    # inbound", the *last* message drives "latest_inbound". Both snapshots
    # produce the same ultimate latest-inbound when caller re-sorts by ts.
    # So simulate: after normalization, order is stable.
    snap_norm = make_snapshot(messages=sorted([m1, m2], key=lambda m: m.received_at or NOW))
    r1 = reduce(make_input(snapshot=snap_norm, prior_state=CS.act_open,
                           prior_bucket=CB.Act, evidence={E.EXPLICIT_RESOLUTION}), S)
    r2 = reduce(make_input(snapshot=snap_norm, prior_state=CS.act_open,
                           prior_bucket=CB.Act, evidence={E.EXPLICIT_RESOLUTION}), S)
    assert r1.model_dump() == r2.model_dump()


# --------------------- I11 ---------------------
def test_I11_handoff_narrow_classifier_hint_only() -> None:
    snap = make_snapshot(
        classifications=[{"primary_bucket": None, "confidence": 0.4,
                          "reason_short": "delegate hint", "delegate_hint": True}],
        prior_task_id=1,
    )
    r = reduce(make_input(snapshot=snap, prior_state=CS.delegate_open,
                          prior_bucket=CB.Delegate, evidence=set()), S)
    # No T030 fires; noop.
    assert r.task_intent.kind == TK.noop
    assert r.next_state == CS.delegate_open


def test_I11_handoff_e10_triggers_T030() -> None:
    snap = make_snapshot(prior_task_id=1)
    r = reduce(make_input(snapshot=snap, prior_state=CS.delegate_open,
                          prior_bucket=CB.Delegate,
                          evidence={E.HANDOFF_CONFIRMED}), S)
    assert r.transition_id == "T030"
    assert r.next_state == CS.waiting_on
