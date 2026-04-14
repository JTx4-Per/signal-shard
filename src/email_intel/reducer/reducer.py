"""Pure reducer entry point — reducer-spec §1 G1.

`reduce(inp, settings) -> ReducerResult`. No I/O, no wall-clock reads,
no RNG. Deterministic (G9).
"""

from __future__ import annotations

from typing import Any

import structlog

from email_intel.config import Settings
from email_intel.db.models import (
    ConversationBucket,
    ConversationEventType,
    ConversationState,
)
from email_intel.reducer.evidence import detect_evidence
from email_intel.reducer.guards import GUARDS
from email_intel.reducer.priority import PriorityResolution, resolve
from email_intel.reducer.transitions import (
    POLICY,
    PRESERVE,
    TRANSITION_BY_ID,
    TRANSITIONS,
    TransitionRow,
)
from email_intel.schemas.events import Evidence
from email_intel.schemas.intents import (
    CategoryIntent,
    CategoryIntentKind,
    ReviewFlag,
    TaskIntent,
    TaskIntentKind,
)
from email_intel.schemas.reducer import (
    ReducerEventRecord,
    ReducerInput,
    ReducerResult,
)
from email_intel.writeback.operation_keys import task_create_key, writeback_key

__all__ = ["reduce", "ReducerContractError"]

_log = structlog.get_logger(__name__)


class ReducerContractError(RuntimeError):
    """Raised when a transition produces an invalid (state, bucket) pair (G5)."""


# Allowed (state, bucket) pairs per project-plan §9.
_ALLOWED_PAIRS: set[tuple[ConversationState, ConversationBucket | None]] = {
    (ConversationState.none, None),
    (ConversationState.act_open, ConversationBucket.Act),
    (ConversationState.respond_open, ConversationBucket.Respond),
    (ConversationState.delegate_open, ConversationBucket.Delegate),
    (ConversationState.deferred, ConversationBucket.Defer),
    (ConversationState.waiting_on, ConversationBucket.WaitingOn),
    (ConversationState.fyi_context, ConversationBucket.FYI),
    (ConversationState.noise_transient, ConversationBucket.DeleteOrUnsubscribe),
    (ConversationState.needs_review, ConversationBucket.Act),
    (ConversationState.needs_review, ConversationBucket.Respond),
    (ConversationState.needs_review, ConversationBucket.Delegate),
    (ConversationState.needs_review, ConversationBucket.Defer),
    (ConversationState.needs_review, ConversationBucket.WaitingOn),
    (ConversationState.needs_review, ConversationBucket.FYI),
    (ConversationState.needs_review, ConversationBucket.DeleteOrUnsubscribe),
    (ConversationState.needs_review, None),
    # done preserves prior bucket — any bucket allowed
    *[(ConversationState.done, b) for b in ConversationBucket],
    (ConversationState.done, None),
}


# Map each tier-winning evidence to the "category bucket" it implies when a
# preserve sentinel needs to be resolved from evidence (e.g. T070).
_EVIDENCE_TO_NEW_STATE: dict[Evidence, tuple[ConversationState, ConversationBucket]] = {
    Evidence.NEW_INBOUND_ASK_DELIVERABLE: (ConversationState.act_open, ConversationBucket.Act),
    Evidence.NEW_INBOUND_ASK_REPLY: (ConversationState.respond_open, ConversationBucket.Respond),
}


def _resolve_done_category(
    settings: Settings, prior_bucket: ConversationBucket | None
) -> tuple[CategoryIntentKind, ConversationBucket | None]:
    policy = settings.DONE_CATEGORY_POLICY
    if policy == "clear":
        return CategoryIntentKind.clear, None
    if policy == "fyi":
        return CategoryIntentKind.apply, ConversationBucket.FYI
    # preserve
    return CategoryIntentKind.preserve, prior_bucket


def _conv_id_str(inp: ReducerInput) -> str:
    return str(inp.snapshot.conversation_id)


def _build_task_intent(
    row: TransitionRow,
    inp: ReducerInput,
    resolved_next_bucket: ConversationBucket | None,
    resolved_next_state: ConversationState,
) -> TaskIntent:
    kind = row.task_intent_kind

    # T102/I2 guardrail: if prior had an active task and row says `create`,
    # downgrade to `update_fields` to maintain one-active-task invariant.
    if kind == TaskIntentKind.create and inp.snapshot.prior_task_id is not None:
        # Spec T062 explicitly wants `create` (new task after soft-expired
        # window). Detect by guard_name.
        if row.guard_name != "soft_expired_window":
            kind = TaskIntentKind.update_fields

    # T040/T041 fallback: row says reopen but no task exists → create.
    if kind == TaskIntentKind.reopen and inp.snapshot.prior_task_id is None:
        kind = TaskIntentKind.create

    target_bucket = row.task_intent_target_bucket
    if target_bucket is None:
        target_bucket = resolved_next_bucket

    conv_id = _conv_id_str(inp)
    op_key = ""
    if kind == TaskIntentKind.noop:
        op_key = ""
    elif kind == TaskIntentKind.create:
        op_key = task_create_key(conv_id, "primary", resolved_next_state.value)
    else:
        op_key = writeback_key(conv_id, kind.value, resolved_next_state.value, "primary")

    fields: dict[str, object] = {}
    return TaskIntent(
        kind=kind, target_bucket=target_bucket, operation_key=op_key, fields=fields
    )


def _build_category_intent(
    row: TransitionRow,
    inp: ReducerInput,
    settings: Settings,
    resolved_next_bucket: ConversationBucket | None,
    winning: Evidence | None,
) -> CategoryIntent:
    kind = row.category_intent_kind
    target = row.category_intent_target

    if kind == CategoryIntentKind.noop or kind == CategoryIntentKind.clear or kind == CategoryIntentKind.preserve:
        return CategoryIntent(kind=kind, target_bucket=None, operation_key="")

    # apply — resolve target sentinel
    resolved: ConversationBucket | None
    if target == POLICY:
        policy_kind, policy_bucket = _resolve_done_category(settings, inp.prior_bucket)
        return CategoryIntent(
            kind=policy_kind,
            target_bucket=policy_bucket,
            operation_key=writeback_key(
                _conv_id_str(inp),
                f"category_{policy_kind.value}",
                (policy_bucket.value if policy_bucket else "none"),
                "primary",
            ),
        )
    if target == PRESERVE:
        # Derive from resolved_next_bucket or evidence
        resolved = resolved_next_bucket
        if resolved is None and winning is not None:
            mapping = _EVIDENCE_TO_NEW_STATE.get(winning)
            if mapping is not None:
                resolved = mapping[1]
    elif isinstance(target, ConversationBucket):
        resolved = target
    else:
        resolved = resolved_next_bucket

    op_key = writeback_key(
        _conv_id_str(inp),
        "category_apply",
        resolved.value if resolved else "none",
        "primary",
    )
    return CategoryIntent(kind=kind, target_bucket=resolved, operation_key=op_key)


def _build_events(
    row: TransitionRow,
    suppressed: tuple[Evidence, ...],
    non_tier: tuple[Evidence, ...],
    winning: Evidence | None,
    inp: ReducerInput,
    extra_payload: dict[str, Any] | None = None,
) -> list[ReducerEventRecord]:
    base_payload: dict[str, Any] = {
        "transition_id": row.id,
        "winning_evidence": winning.value if winning else None,
        "suppressed_evidence": sorted(e.value for e in suppressed),
        "non_tier_modifiers": sorted(e.value for e in non_tier),
        "prior_state": inp.prior_state.value,
    }
    if extra_payload:
        base_payload.update(extra_payload)
    return [
        ReducerEventRecord(event_type=et, payload=dict(base_payload))
        for et in row.events_emitted
    ]


def _resolve_next_state_and_bucket(
    row: TransitionRow,
    inp: ReducerInput,
    winning: Evidence | None,
    override_target_state: ConversationState | None = None,
    override_target_bucket: ConversationBucket | None = None,
) -> tuple[ConversationState, ConversationBucket | None]:
    # Resolve next_state
    if override_target_state is not None:
        next_state = override_target_state
    elif row.next_state_expr == PRESERVE:
        # T062 / T070: new-ask rows from done/fyi — resolve from winning evidence
        if winning in _EVIDENCE_TO_NEW_STATE:
            next_state = _EVIDENCE_TO_NEW_STATE[winning][0]
        else:
            next_state = inp.prior_state
    elif isinstance(row.next_state_expr, ConversationState):
        next_state = row.next_state_expr
    else:
        next_state = inp.prior_state

    # Resolve next_bucket
    if override_target_bucket is not None:
        next_bucket: ConversationBucket | None = override_target_bucket
    elif row.next_bucket_expr == PRESERVE:
        if winning in _EVIDENCE_TO_NEW_STATE:
            next_bucket = _EVIDENCE_TO_NEW_STATE[winning][1]
        else:
            next_bucket = inp.prior_bucket
    elif isinstance(row.next_bucket_expr, ConversationBucket):
        next_bucket = row.next_bucket_expr
    else:
        next_bucket = None

    return next_state, next_bucket


def _find_transition(
    inp: ReducerInput, winning: Evidence | None
) -> TransitionRow | None:
    if winning is None:
        return None
    for row in TRANSITIONS:
        if inp.prior_state not in row.prior_states:
            continue
        if row.trigger_evidence and winning not in row.trigger_evidence:
            continue
        if not row.trigger_evidence and row.guard_name is None:
            # No trigger + no guard → would match everything; skip to be safe.
            continue
        if row.guard_name is not None:
            guard = GUARDS.get(row.guard_name)
            if guard is None:
                continue
            if not guard(inp, inp.now):
                continue
        else:
            # Some rows have no guard; still filter ones that are explicitly
            # guard-only (e.g. T031 has a guard, skipped above).
            pass
        return row
    return None


def _noop_result(
    inp: ReducerInput,
    transition_id: str,
    suppressed: tuple[Evidence, ...] = (),
    non_tier: tuple[Evidence, ...] = (),
    winning: Evidence | None = None,
    review_flag: ReviewFlag = ReviewFlag.none,
) -> ReducerResult:
    task_intent = TaskIntent(kind=TaskIntentKind.noop, target_bucket=None, operation_key="")
    cat_intent = CategoryIntent(kind=CategoryIntentKind.noop, target_bucket=None, operation_key="")
    payload = {
        "transition_id": transition_id,
        "winning_evidence": winning.value if winning else None,
        "suppressed_evidence": sorted(e.value for e in suppressed),
        "non_tier_modifiers": sorted(e.value for e in non_tier),
        "prior_state": inp.prior_state.value,
    }
    return ReducerResult(
        next_state=inp.prior_state,
        next_bucket=inp.prior_bucket,
        task_intent=task_intent,
        category_intent=cat_intent,
        events=[ReducerEventRecord(event_type=ConversationEventType.reducer_ran_noop, payload=payload)],
        operation_keys=[],
        review_flag=review_flag,
        transition_id=transition_id,
        suppressed_evidence=list(suppressed),
    )


def _needs_review_result(
    inp: ReducerInput,
    row_id: str,
    reason: str,
    task_kind: TaskIntentKind,
    suppressed: tuple[Evidence, ...] = (),
    non_tier: tuple[Evidence, ...] = (),
    winning: Evidence | None = None,
) -> ReducerResult:
    row = TRANSITION_BY_ID[row_id]
    next_state = ConversationState.needs_review
    next_bucket = inp.prior_bucket
    conv_id = _conv_id_str(inp)
    op_key = (
        writeback_key(conv_id, task_kind.value, next_state.value, "primary")
        if task_kind != TaskIntentKind.noop
        else ""
    )
    task_intent = TaskIntent(kind=task_kind, target_bucket=None, operation_key=op_key)
    cat_intent = CategoryIntent(kind=CategoryIntentKind.noop, target_bucket=None, operation_key="")
    payload = {
        "transition_id": row.id,
        "reason": reason,
        "winning_evidence": winning.value if winning else None,
        "suppressed_evidence": sorted(e.value for e in suppressed),
        "non_tier_modifiers": sorted(e.value for e in non_tier),
        "prior_state": inp.prior_state.value,
    }
    events = [
        ReducerEventRecord(event_type=et, payload=dict(payload))
        for et in row.events_emitted
    ]
    return ReducerResult(
        next_state=next_state,
        next_bucket=next_bucket,
        task_intent=task_intent,
        category_intent=cat_intent,
        events=events,
        operation_keys=[op_key] if op_key else [],
        review_flag=ReviewFlag.state,
        transition_id=row.id,
        suppressed_evidence=list(suppressed),
    )


def _validate_pair(state: ConversationState, bucket: ConversationBucket | None) -> None:
    if (state, bucket) not in _ALLOWED_PAIRS:
        raise ReducerContractError(f"Invalid (state, bucket) pair: ({state!r}, {bucket!r})")


def reduce(inp: ReducerInput, settings: Settings) -> ReducerResult:
    """Apply the transition matrix. Pure. See reducer-spec §1, §2, §4."""
    # 1. Evidence set — merge detected + input (input is authoritative override).
    detected = detect_evidence(
        inp.snapshot, inp.prior_state, inp.prior_bucket, inp.now, settings
    )
    evidence_set: set[Evidence] = set(inp.evidence_set) | set(detected)
    if inp.manual_override is not None:
        evidence_set.add(Evidence.MANUAL_OVERRIDE)

    # G7: needs_review blocks writeback. T103 / T085.
    if inp.prior_state == ConversationState.needs_review:
        # T083 resolves a review via manual_override — still honored.
        if inp.manual_override is not None:
            return _manual_override_path(inp, settings, evidence_set, row_id="T083")
        return _noop_result(
            inp,
            transition_id="T103" if evidence_set else "T085",
            suppressed=(),
            non_tier=tuple(e for e in evidence_set if e in {Evidence.SENT_ITEMS_LAG, Evidence.SIGNAL_CONFLICT, Evidence.WRITEBACK_FAILURE_THRESHOLD}),
        )

    # 2. Priority-tier resolution
    resolution: PriorityResolution = resolve(evidence_set)
    winning = resolution.winning
    suppressed = resolution.suppressed
    non_tier = resolution.non_tier_modifiers

    # 3. High-priority review bypasses
    if Evidence.SIGNAL_CONFLICT in evidence_set:
        return _needs_review_result(
            inp, "T081", "signal_conflict", TaskIntentKind.noop,
            suppressed=suppressed, non_tier=non_tier, winning=winning,
        )
    if Evidence.WRITEBACK_FAILURE_THRESHOLD in evidence_set:
        return _needs_review_result(
            inp, "T082", "writeback_dead_letter", TaskIntentKind.dead_letter,
            suppressed=suppressed, non_tier=non_tier, winning=winning,
        )
    if Evidence.SENT_ITEMS_LAG in evidence_set:
        return _needs_review_result(
            inp, "T080", "sent_items_lag", TaskIntentKind.noop,
            suppressed=suppressed, non_tier=non_tier, winning=winning,
        )

    # 4. Manual override bypasses
    if winning == Evidence.MANUAL_OVERRIDE:
        return _manual_override_path(inp, settings, evidence_set, row_id="T090")

    # 5. Nothing won → try non-tier evidence as driver (e.g. HANDOFF_CONFIRMED,
    # DUE_DATE_UPDATE for rows like T009, T017, T030).
    if winning is None:
        for candidate in sorted(non_tier, key=lambda e: list(Evidence).index(e)):
            row_try = _find_transition(inp, candidate)
            if row_try is not None:
                winning = candidate
                break
        if winning is None:
            return _noop_result(inp, transition_id="T000", suppressed=suppressed, non_tier=non_tier)

    # 6. Find transition row
    row = _find_transition(inp, winning)
    if row is None:
        _log.warning(
            "no_matching_transition",
            conversation_id=inp.snapshot.conversation_id,
            prior_state=inp.prior_state.value,
            winning=winning.value,
        )
        return _noop_result(inp, transition_id="T000", suppressed=suppressed, non_tier=non_tier, winning=winning)

    # 7. Resolve sentinels
    next_state, next_bucket = _resolve_next_state_and_bucket(row, inp, winning)
    _validate_pair(next_state, next_bucket)

    # 8. Task intent
    task_intent = _build_task_intent(row, inp, next_bucket, next_state)

    # 9. Category intent
    cat_intent = _build_category_intent(row, inp, settings, next_bucket, winning)

    # 10. Events
    events = _build_events(row, suppressed, non_tier, winning, inp)

    op_keys: list[str] = []
    if task_intent.operation_key:
        op_keys.append(task_intent.operation_key)
    if cat_intent.operation_key:
        op_keys.append(cat_intent.operation_key)

    _log.debug(
        "reducer_transition",
        conversation_id=inp.snapshot.conversation_id,
        transition_id=row.id,
        winning=winning.value,
    )

    return ReducerResult(
        next_state=next_state,
        next_bucket=next_bucket,
        task_intent=task_intent,
        category_intent=cat_intent,
        events=events,
        operation_keys=op_keys,
        review_flag=ReviewFlag.none,
        transition_id=row.id,
        suppressed_evidence=list(suppressed),
    )


def _manual_override_path(
    inp: ReducerInput,
    settings: Settings,
    evidence_set: set[Evidence],
    row_id: str,
) -> ReducerResult:
    assert inp.manual_override is not None
    row = TRANSITION_BY_ID[row_id]
    target_state = inp.manual_override.target_state
    target_bucket = inp.manual_override.target_bucket
    _validate_pair(target_state, target_bucket)

    conv_id = _conv_id_str(inp)
    # Task intent: update_fields (computed as if normal) — if no active task, noop.
    task_kind = TaskIntentKind.update_fields
    if inp.snapshot.prior_task_id is None:
        task_kind = TaskIntentKind.noop
    task_op = (
        writeback_key(conv_id, task_kind.value, target_state.value, "primary")
        if task_kind != TaskIntentKind.noop
        else ""
    )
    task_intent = TaskIntent(
        kind=task_kind, target_bucket=target_bucket, operation_key=task_op
    )

    if target_bucket is not None:
        cat_intent = CategoryIntent(
            kind=CategoryIntentKind.apply,
            target_bucket=target_bucket,
            operation_key=writeback_key(conv_id, "category_apply", target_bucket.value, "primary"),
        )
    else:
        cat_intent = CategoryIntent(
            kind=CategoryIntentKind.noop, target_bucket=None, operation_key=""
        )

    payload = {
        "transition_id": row.id,
        "winning_evidence": Evidence.MANUAL_OVERRIDE.value,
        "override_target_state": target_state.value,
        "override_target_bucket": target_bucket.value if target_bucket else None,
        "prior_state": inp.prior_state.value,
    }
    events = [
        ReducerEventRecord(event_type=et, payload=dict(payload))
        for et in row.events_emitted
    ]

    op_keys = [k for k in [task_intent.operation_key, cat_intent.operation_key] if k]
    return ReducerResult(
        next_state=target_state,
        next_bucket=target_bucket,
        task_intent=task_intent,
        category_intent=cat_intent,
        events=events,
        operation_keys=op_keys,
        review_flag=ReviewFlag.none,
        transition_id=row.id,
        suppressed_evidence=[],
    )
