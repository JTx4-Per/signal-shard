"""Authoritative transition table — reducer-spec.md §4.

Every row T001–T104 from the spec is encoded below. Wave 2 (`reducer.py`)
resolves `"preserve"` sentinels and looks up guard callables by name.

COPY-PASTE DISCIPLINE: if a row disagrees with reducer-spec.md §4, the spec
wins. Update the spec + bump reducer_version + fix this file. Do not drift.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from email_intel.db.models import ConversationBucket, ConversationEventType, ConversationState
from email_intel.schemas.events import Evidence
from email_intel.schemas.intents import CategoryIntentKind, TaskIntentKind

CS = ConversationState
CB = ConversationBucket
E = Evidence
TK = TaskIntentKind
CK = CategoryIntentKind
ET = ConversationEventType

# Sentinels (Wave 2 resolves these at runtime)
PRESERVE = "preserve"
POLICY = "policy"  # category target = `done_category_policy`

# Common state-set shortcuts
ANY_STATE: frozenset[CS] = frozenset(CS)
ALL_NON_REVIEW: frozenset[CS] = frozenset(s for s in CS if s != CS.needs_review)


@dataclass(frozen=True)
class TransitionRow:
    """A single row of the §4 transition matrix."""

    id: str
    prior_states: frozenset[CS]
    trigger_evidence: frozenset[E]
    next_state_expr: CS | str  # CS value or "preserve"
    next_bucket_expr: CB | str | None  # CB value, "preserve", or None (null)
    task_intent_kind: TK
    category_intent_kind: CK
    guard_name: str | None = None
    task_intent_target_bucket: CB | None = None
    category_intent_target: CB | str | None = None  # CB, "policy", or None
    events_emitted: tuple[ET, ...] = field(default_factory=tuple)


# ---------- §4.1 From `none` ----------
_ROWS: list[TransitionRow] = [
    TransitionRow(
        id="T001",
        prior_states=frozenset({CS.none}),
        trigger_evidence=frozenset({E.NEW_INBOUND_ASK_DELIVERABLE}),
        next_state_expr=CS.act_open,
        next_bucket_expr=CB.Act,
        task_intent_kind=TK.create,
        category_intent_kind=CK.apply,
        category_intent_target=CB.Act,
        events_emitted=(ET.state_changed, ET.task_created),
    ),
    TransitionRow(
        id="T002",
        prior_states=frozenset({CS.none}),
        trigger_evidence=frozenset({E.NEW_INBOUND_ASK_REPLY}),
        next_state_expr=CS.respond_open,
        next_bucket_expr=CB.Respond,
        task_intent_kind=TK.create,
        category_intent_kind=CK.apply,
        category_intent_target=CB.Respond,
        events_emitted=(ET.state_changed, ET.task_created),
    ),
    TransitionRow(
        id="T003",
        prior_states=frozenset({CS.none}),
        trigger_evidence=frozenset({E.USER_REPLIED_SATISFIES_ASK}),
        next_state_expr=CS.waiting_on,
        next_bucket_expr=CB.WaitingOn,
        task_intent_kind=TK.create,
        category_intent_kind=CK.apply,
        category_intent_target=CB.WaitingOn,
        events_emitted=(ET.state_changed, ET.task_created),
    ),
    TransitionRow(
        id="T004",
        prior_states=frozenset({CS.none}),
        trigger_evidence=frozenset({E.EXPLICIT_DEFER}),
        next_state_expr=CS.deferred,
        next_bucket_expr=CB.Defer,
        task_intent_kind=TK.noop,
        category_intent_kind=CK.apply,
        category_intent_target=CB.Defer,
        events_emitted=(ET.state_changed,),
    ),
    TransitionRow(
        id="T005",
        prior_states=frozenset({CS.none}),
        trigger_evidence=frozenset({E.BULK_NOISE}),
        next_state_expr=CS.noise_transient,
        next_bucket_expr=CB.DeleteOrUnsubscribe,
        task_intent_kind=TK.noop,
        category_intent_kind=CK.apply,
        category_intent_target=CB.DeleteOrUnsubscribe,
        events_emitted=(ET.state_changed,),
    ),
    TransitionRow(
        id="T006",
        prior_states=frozenset({CS.none}),
        trigger_evidence=frozenset({E.FYI_ONLY}),
        next_state_expr=CS.fyi_context,
        next_bucket_expr=CB.FYI,
        task_intent_kind=TK.noop,
        category_intent_kind=CK.apply,
        category_intent_target=CB.FYI,
        events_emitted=(ET.state_changed,),
    ),
    TransitionRow(
        id="T007",
        prior_states=frozenset({CS.none}),
        trigger_evidence=frozenset({E.EXPLICIT_RESOLUTION}),
        next_state_expr=CS.none,
        next_bucket_expr=None,
        task_intent_kind=TK.noop,
        category_intent_kind=CK.noop,
        events_emitted=(ET.reducer_ran_noop,),
    ),
    TransitionRow(
        id="T008",
        prior_states=frozenset({CS.none}),
        trigger_evidence=frozenset({E.SOFT_RESOLUTION}),
        next_state_expr=CS.none,
        next_bucket_expr=None,
        task_intent_kind=TK.noop,
        category_intent_kind=CK.noop,
        events_emitted=(ET.reducer_ran_noop,),
    ),
    TransitionRow(
        id="T009",
        prior_states=frozenset({CS.none}),
        trigger_evidence=frozenset({E.HANDOFF_CONFIRMED}),
        next_state_expr=CS.none,
        next_bucket_expr=None,
        task_intent_kind=TK.noop,
        category_intent_kind=CK.noop,
        events_emitted=(ET.reducer_ran_noop,),
    ),
    # ---------- §4.2 From `act_open` ----------
    TransitionRow(
        id="T010",
        prior_states=frozenset({CS.act_open}),
        trigger_evidence=frozenset({E.NEW_INBOUND_ASK_DELIVERABLE}),
        next_state_expr=CS.act_open,
        next_bucket_expr=CB.Act,
        task_intent_kind=TK.update_fields,
        category_intent_kind=CK.noop,
        events_emitted=(ET.task_updated,),
    ),
    TransitionRow(
        id="T011",
        prior_states=frozenset({CS.act_open}),
        trigger_evidence=frozenset({E.NEW_INBOUND_ASK_REPLY}),
        next_state_expr=CS.respond_open,
        next_bucket_expr=CB.Respond,
        task_intent_kind=TK.move_list,
        task_intent_target_bucket=CB.Respond,
        category_intent_kind=CK.apply,
        category_intent_target=CB.Respond,
        events_emitted=(ET.state_changed, ET.task_updated),
    ),
    TransitionRow(
        id="T012",
        prior_states=frozenset({CS.act_open}),
        trigger_evidence=frozenset({E.USER_REPLIED_SATISFIES_ASK}),
        next_state_expr=CS.waiting_on,
        next_bucket_expr=CB.WaitingOn,
        task_intent_kind=TK.move_list,
        task_intent_target_bucket=CB.WaitingOn,
        category_intent_kind=CK.apply,
        category_intent_target=CB.WaitingOn,
        events_emitted=(ET.state_changed, ET.task_updated),
    ),
    TransitionRow(
        id="T013",
        prior_states=frozenset({CS.act_open}),
        trigger_evidence=frozenset({E.EXPLICIT_RESOLUTION}),
        next_state_expr=CS.done,
        next_bucket_expr=PRESERVE,
        task_intent_kind=TK.hard_complete,
        category_intent_kind=CK.apply,  # resolved by policy at runtime
        category_intent_target=POLICY,
        events_emitted=(ET.state_changed, ET.task_hard_complete),
    ),
    TransitionRow(
        id="T014",
        prior_states=frozenset({CS.act_open}),
        trigger_evidence=frozenset({E.SOFT_RESOLUTION}),
        next_state_expr=CS.done,
        next_bucket_expr=PRESERVE,
        task_intent_kind=TK.soft_complete,
        category_intent_kind=CK.apply,
        category_intent_target=POLICY,
        events_emitted=(ET.state_changed, ET.task_soft_complete),
    ),
    TransitionRow(
        id="T015",
        prior_states=frozenset({CS.act_open}),
        trigger_evidence=frozenset({E.EXPLICIT_DEFER}),
        next_state_expr=CS.deferred,
        next_bucket_expr=CB.Defer,
        task_intent_kind=TK.update_fields,
        category_intent_kind=CK.apply,
        category_intent_target=CB.Defer,
        events_emitted=(ET.state_changed, ET.task_updated),
    ),
    TransitionRow(
        id="T016",
        prior_states=frozenset({CS.act_open}),
        trigger_evidence=frozenset({E.FYI_ONLY}),
        next_state_expr=CS.act_open,
        next_bucket_expr=CB.Act,
        task_intent_kind=TK.noop,
        category_intent_kind=CK.noop,
        events_emitted=(ET.reducer_ran_noop,),
    ),
    TransitionRow(
        id="T017",
        prior_states=ANY_STATE,
        trigger_evidence=frozenset({E.DUE_DATE_UPDATE}),
        next_state_expr=PRESERVE,
        next_bucket_expr=PRESERVE,
        task_intent_kind=TK.update_fields,
        category_intent_kind=CK.noop,
        events_emitted=(ET.task_updated,),
    ),
    # ---------- §4.3 From `respond_open` ----------
    TransitionRow(
        id="T020",
        prior_states=frozenset({CS.respond_open}),
        trigger_evidence=frozenset({E.NEW_INBOUND_ASK_REPLY}),
        next_state_expr=CS.respond_open,
        next_bucket_expr=CB.Respond,
        task_intent_kind=TK.update_fields,
        category_intent_kind=CK.noop,
        events_emitted=(ET.task_updated,),
    ),
    TransitionRow(
        id="T021",
        prior_states=frozenset({CS.respond_open}),
        trigger_evidence=frozenset({E.NEW_INBOUND_ASK_DELIVERABLE}),
        next_state_expr=CS.act_open,
        next_bucket_expr=CB.Act,
        task_intent_kind=TK.move_list,
        task_intent_target_bucket=CB.Act,
        category_intent_kind=CK.apply,
        category_intent_target=CB.Act,
        events_emitted=(ET.state_changed, ET.task_updated),
    ),
    TransitionRow(
        id="T022",
        prior_states=frozenset({CS.respond_open}),
        trigger_evidence=frozenset({E.USER_REPLIED_SATISFIES_ASK}),
        next_state_expr=CS.waiting_on,
        next_bucket_expr=CB.WaitingOn,
        task_intent_kind=TK.move_list,
        task_intent_target_bucket=CB.WaitingOn,
        category_intent_kind=CK.apply,
        category_intent_target=CB.WaitingOn,
        events_emitted=(ET.state_changed, ET.task_updated),
    ),
    TransitionRow(
        id="T023",
        prior_states=frozenset({CS.respond_open}),
        trigger_evidence=frozenset({E.EXPLICIT_RESOLUTION}),
        next_state_expr=CS.done,
        next_bucket_expr=PRESERVE,
        task_intent_kind=TK.hard_complete,
        category_intent_kind=CK.apply,
        category_intent_target=POLICY,
        events_emitted=(ET.state_changed, ET.task_hard_complete),
    ),
    TransitionRow(
        id="T024",
        prior_states=frozenset({CS.respond_open}),
        trigger_evidence=frozenset({E.SOFT_RESOLUTION}),
        next_state_expr=CS.done,
        next_bucket_expr=PRESERVE,
        task_intent_kind=TK.soft_complete,
        category_intent_kind=CK.apply,
        category_intent_target=POLICY,
        events_emitted=(ET.state_changed, ET.task_soft_complete),
    ),
    TransitionRow(
        id="T025",
        prior_states=frozenset({CS.respond_open}),
        trigger_evidence=frozenset({E.EXPLICIT_DEFER}),
        next_state_expr=CS.deferred,
        next_bucket_expr=CB.Defer,
        task_intent_kind=TK.update_fields,
        category_intent_kind=CK.apply,
        category_intent_target=CB.Defer,
        events_emitted=(ET.state_changed, ET.task_updated),
    ),
    TransitionRow(
        id="T026",
        prior_states=frozenset({CS.respond_open}),
        trigger_evidence=frozenset({E.FYI_ONLY}),
        next_state_expr=CS.respond_open,
        next_bucket_expr=CB.Respond,
        task_intent_kind=TK.noop,
        category_intent_kind=CK.noop,
        events_emitted=(ET.reducer_ran_noop,),
    ),
    # ---------- §4.4 From `delegate_open` ----------
    TransitionRow(
        id="T030",
        prior_states=frozenset({CS.delegate_open}),
        trigger_evidence=frozenset({E.HANDOFF_CONFIRMED}),
        next_state_expr=CS.waiting_on,
        next_bucket_expr=CB.WaitingOn,
        task_intent_kind=TK.move_list,
        task_intent_target_bucket=CB.WaitingOn,
        category_intent_kind=CK.apply,
        category_intent_target=CB.WaitingOn,
        events_emitted=(ET.state_changed, ET.task_updated),
    ),
    TransitionRow(
        id="T031",
        prior_states=frozenset({CS.delegate_open}),
        trigger_evidence=frozenset({E.USER_REPLIED_SATISFIES_ASK}),
        guard_name="not_handoff_confirmed",
        next_state_expr=CS.delegate_open,
        next_bucket_expr=CB.Delegate,
        task_intent_kind=TK.noop,
        category_intent_kind=CK.noop,
        events_emitted=(ET.reducer_ran_noop,),
    ),
    TransitionRow(
        id="T032",
        prior_states=frozenset({CS.delegate_open}),
        trigger_evidence=frozenset({E.NEW_INBOUND_ASK_DELIVERABLE}),
        next_state_expr=CS.act_open,
        next_bucket_expr=CB.Act,
        task_intent_kind=TK.move_list,
        task_intent_target_bucket=CB.Act,
        category_intent_kind=CK.apply,
        category_intent_target=CB.Act,
        events_emitted=(ET.state_changed, ET.task_updated),
    ),
    TransitionRow(
        id="T033",
        prior_states=frozenset({CS.delegate_open}),
        trigger_evidence=frozenset({E.EXPLICIT_RESOLUTION}),
        next_state_expr=CS.done,
        next_bucket_expr=PRESERVE,
        task_intent_kind=TK.hard_complete,
        category_intent_kind=CK.apply,
        category_intent_target=POLICY,
        events_emitted=(ET.state_changed, ET.task_hard_complete),
    ),
    TransitionRow(
        id="T034",
        prior_states=frozenset({CS.delegate_open}),
        trigger_evidence=frozenset({E.SOFT_RESOLUTION}),
        next_state_expr=CS.delegate_open,
        next_bucket_expr=CB.Delegate,
        task_intent_kind=TK.noop,
        category_intent_kind=CK.noop,
        events_emitted=(ET.reducer_ran_noop,),
    ),
    TransitionRow(
        id="T035",
        prior_states=frozenset({CS.delegate_open}),
        trigger_evidence=frozenset({E.EXPLICIT_DEFER}),
        next_state_expr=CS.deferred,
        next_bucket_expr=CB.Defer,
        task_intent_kind=TK.update_fields,
        category_intent_kind=CK.apply,
        category_intent_target=CB.Defer,
        events_emitted=(ET.state_changed, ET.task_updated),
    ),
    # ---------- §4.5 From `deferred` ----------
    TransitionRow(
        id="T040",
        prior_states=frozenset({CS.deferred}),
        trigger_evidence=frozenset({E.DEFER_TIMER_FIRED}),
        guard_name="original_ask_was_act",
        next_state_expr=CS.act_open,
        next_bucket_expr=CB.Act,
        task_intent_kind=TK.reopen,  # reducer falls back to `create` if no task exists
        category_intent_kind=CK.apply,
        category_intent_target=CB.Act,
        events_emitted=(ET.state_changed, ET.task_reopened),
    ),
    TransitionRow(
        id="T041",
        prior_states=frozenset({CS.deferred}),
        trigger_evidence=frozenset({E.DEFER_TIMER_FIRED}),
        guard_name="original_ask_was_respond",
        next_state_expr=CS.respond_open,
        next_bucket_expr=CB.Respond,
        task_intent_kind=TK.reopen,
        category_intent_kind=CK.apply,
        category_intent_target=CB.Respond,
        events_emitted=(ET.state_changed, ET.task_reopened),
    ),
    TransitionRow(
        id="T042",
        prior_states=frozenset({CS.deferred}),
        trigger_evidence=frozenset({E.NEW_INBOUND_ASK_DELIVERABLE}),
        next_state_expr=CS.act_open,
        next_bucket_expr=CB.Act,
        task_intent_kind=TK.reopen,
        category_intent_kind=CK.apply,
        category_intent_target=CB.Act,
        events_emitted=(ET.state_changed, ET.task_reopened),
    ),
    TransitionRow(
        id="T043",
        prior_states=frozenset({CS.deferred}),
        trigger_evidence=frozenset({E.NEW_INBOUND_ASK_REPLY}),
        next_state_expr=CS.respond_open,
        next_bucket_expr=CB.Respond,
        task_intent_kind=TK.reopen,
        category_intent_kind=CK.apply,
        category_intent_target=CB.Respond,
        events_emitted=(ET.state_changed, ET.task_reopened),
    ),
    TransitionRow(
        id="T044",
        prior_states=frozenset({CS.deferred}),
        trigger_evidence=frozenset({E.EXPLICIT_RESOLUTION}),
        next_state_expr=CS.done,
        next_bucket_expr=PRESERVE,
        task_intent_kind=TK.hard_complete,
        category_intent_kind=CK.apply,
        category_intent_target=POLICY,
        events_emitted=(ET.state_changed, ET.task_hard_complete),
    ),
    TransitionRow(
        id="T045",
        prior_states=frozenset({CS.deferred}),
        trigger_evidence=frozenset({E.FYI_ONLY}),
        next_state_expr=CS.deferred,
        next_bucket_expr=CB.Defer,
        task_intent_kind=TK.noop,
        category_intent_kind=CK.noop,
        events_emitted=(ET.reducer_ran_noop,),
    ),
    # ---------- §4.6 From `waiting_on` ----------
    TransitionRow(
        id="T050",
        prior_states=frozenset({CS.waiting_on}),
        trigger_evidence=frozenset({E.EXPLICIT_RESOLUTION}),
        next_state_expr=CS.done,
        next_bucket_expr=PRESERVE,
        task_intent_kind=TK.hard_complete,
        category_intent_kind=CK.apply,
        category_intent_target=POLICY,
        events_emitted=(ET.state_changed, ET.task_hard_complete),
    ),
    TransitionRow(
        id="T051",
        prior_states=frozenset({CS.waiting_on}),
        trigger_evidence=frozenset({E.SOFT_RESOLUTION}),
        next_state_expr=CS.done,
        next_bucket_expr=PRESERVE,
        task_intent_kind=TK.soft_complete,
        category_intent_kind=CK.apply,
        category_intent_target=POLICY,
        events_emitted=(ET.state_changed, ET.task_soft_complete),
    ),
    TransitionRow(
        id="T052",
        prior_states=frozenset({CS.waiting_on}),
        trigger_evidence=frozenset({E.NEW_INBOUND_ASK_DELIVERABLE}),
        next_state_expr=CS.act_open,
        next_bucket_expr=CB.Act,
        task_intent_kind=TK.move_list,
        task_intent_target_bucket=CB.Act,
        category_intent_kind=CK.apply,
        category_intent_target=CB.Act,
        events_emitted=(ET.state_changed, ET.task_updated),
    ),
    TransitionRow(
        id="T053",
        prior_states=frozenset({CS.waiting_on}),
        trigger_evidence=frozenset({E.NEW_INBOUND_ASK_REPLY}),
        next_state_expr=CS.respond_open,
        next_bucket_expr=CB.Respond,
        task_intent_kind=TK.move_list,
        task_intent_target_bucket=CB.Respond,
        category_intent_kind=CK.apply,
        category_intent_target=CB.Respond,
        events_emitted=(ET.state_changed, ET.task_updated),
    ),
    TransitionRow(
        id="T054",
        prior_states=frozenset({CS.waiting_on}),
        trigger_evidence=frozenset({E.USER_REPLIED_SATISFIES_ASK}),
        next_state_expr=CS.waiting_on,
        next_bucket_expr=CB.WaitingOn,
        task_intent_kind=TK.update_fields,
        category_intent_kind=CK.noop,
        events_emitted=(ET.task_updated,),
    ),
    TransitionRow(
        id="T055",
        prior_states=frozenset({CS.waiting_on}),
        trigger_evidence=frozenset({E.FYI_ONLY}),
        next_state_expr=CS.waiting_on,
        next_bucket_expr=CB.WaitingOn,
        task_intent_kind=TK.noop,
        category_intent_kind=CK.noop,
        events_emitted=(ET.reducer_ran_noop,),
    ),
    # ---------- §4.7 From `done` (reopen rules) ----------
    TransitionRow(
        id="T060",
        prior_states=frozenset({CS.done}),
        trigger_evidence=frozenset({E.NEW_INBOUND_ASK_DELIVERABLE}),
        guard_name="reopen_eligible_hard_or_soft_in_window",
        next_state_expr=CS.act_open,
        next_bucket_expr=CB.Act,
        task_intent_kind=TK.reopen,
        category_intent_kind=CK.apply,
        category_intent_target=CB.Act,
        events_emitted=(ET.state_changed, ET.task_reopened),
    ),
    TransitionRow(
        id="T061",
        prior_states=frozenset({CS.done}),
        trigger_evidence=frozenset({E.NEW_INBOUND_ASK_REPLY}),
        guard_name="reopen_eligible_hard_or_soft_in_window",
        next_state_expr=CS.respond_open,
        next_bucket_expr=CB.Respond,
        task_intent_kind=TK.reopen,
        category_intent_kind=CK.apply,
        category_intent_target=CB.Respond,
        events_emitted=(ET.state_changed, ET.task_reopened),
    ),
    TransitionRow(
        id="T062",
        prior_states=frozenset({CS.done}),
        trigger_evidence=frozenset({E.NEW_INBOUND_ASK_DELIVERABLE, E.NEW_INBOUND_ASK_REPLY}),
        guard_name="soft_expired_window",
        next_state_expr=PRESERVE,  # Wave 2: resolve to act_open/respond_open by evidence
        next_bucket_expr=PRESERVE,
        task_intent_kind=TK.create,
        category_intent_kind=CK.apply,
        category_intent_target=PRESERVE,  # bucket derived from evidence at runtime
        events_emitted=(ET.state_changed, ET.task_created),
    ),
    TransitionRow(
        id="T063",
        prior_states=frozenset({CS.done}),
        trigger_evidence=frozenset(),  # "any inbound within soft window, not new ask"
        guard_name="soft_window_open_continuation",
        next_state_expr=PRESERVE,  # prior kind restored
        next_bucket_expr=PRESERVE,
        task_intent_kind=TK.reopen,
        category_intent_kind=CK.apply,
        category_intent_target=PRESERVE,
        events_emitted=(ET.state_changed, ET.task_reopened),
    ),
    TransitionRow(
        id="T064",
        prior_states=frozenset({CS.done}),
        trigger_evidence=frozenset({E.FYI_ONLY}),
        guard_name="completion_kind_hard",
        next_state_expr=CS.done,
        next_bucket_expr=PRESERVE,
        task_intent_kind=TK.noop,
        category_intent_kind=CK.noop,
        events_emitted=(ET.reducer_ran_noop,),
    ),
    TransitionRow(
        id="T065",
        prior_states=frozenset({CS.done}),
        trigger_evidence=frozenset({E.FYI_ONLY}),
        guard_name="completion_kind_soft_in_window",
        next_state_expr=CS.done,
        next_bucket_expr=PRESERVE,
        task_intent_kind=TK.noop,
        category_intent_kind=CK.noop,
        events_emitted=(ET.reducer_ran_noop,),
    ),
    TransitionRow(
        id="T066",
        prior_states=frozenset({CS.done}),
        trigger_evidence=frozenset(),  # synthetic: archive window elapsed
        guard_name="archive_window_elapsed",
        next_state_expr=CS.none,
        next_bucket_expr=None,
        task_intent_kind=TK.noop,
        category_intent_kind=CK.clear,
        events_emitted=(ET.state_changed,),
    ),
    # ---------- §4.8 From `fyi_context` / `noise_transient` ----------
    TransitionRow(
        id="T070",
        prior_states=frozenset({CS.fyi_context}),
        trigger_evidence=frozenset({E.NEW_INBOUND_ASK_DELIVERABLE, E.NEW_INBOUND_ASK_REPLY}),
        next_state_expr=PRESERVE,  # act_open or respond_open by evidence
        next_bucket_expr=PRESERVE,
        task_intent_kind=TK.create,
        category_intent_kind=CK.apply,
        category_intent_target=PRESERVE,
        events_emitted=(ET.state_changed, ET.task_created),
    ),
    TransitionRow(
        id="T071",
        prior_states=frozenset({CS.fyi_context}),
        trigger_evidence=frozenset({E.FYI_ONLY}),
        next_state_expr=CS.fyi_context,
        next_bucket_expr=CB.FYI,
        task_intent_kind=TK.noop,
        category_intent_kind=CK.noop,
        events_emitted=(ET.reducer_ran_noop,),
    ),
    TransitionRow(
        id="T072",
        prior_states=frozenset({CS.noise_transient}),
        trigger_evidence=frozenset({E.NEW_INBOUND_ASK_DELIVERABLE, E.NEW_INBOUND_ASK_REPLY}),
        next_state_expr=CS.needs_review,
        next_bucket_expr=PRESERVE,
        task_intent_kind=TK.noop,
        category_intent_kind=CK.noop,
        events_emitted=(ET.needs_review_raised,),
    ),
    TransitionRow(
        id="T073",
        prior_states=frozenset({CS.noise_transient}),
        trigger_evidence=frozenset({E.BULK_NOISE}),
        next_state_expr=CS.noise_transient,
        next_bucket_expr=CB.DeleteOrUnsubscribe,
        task_intent_kind=TK.noop,
        category_intent_kind=CK.noop,
        events_emitted=(ET.reducer_ran_noop,),
    ),
    # ---------- §4.9 needs_review transitions ----------
    TransitionRow(
        id="T080",
        prior_states=ANY_STATE,
        trigger_evidence=frozenset({E.SENT_ITEMS_LAG}),
        next_state_expr=CS.needs_review,
        next_bucket_expr=PRESERVE,
        task_intent_kind=TK.noop,
        category_intent_kind=CK.noop,
        events_emitted=(ET.needs_review_raised,),
    ),
    TransitionRow(
        id="T081",
        prior_states=ANY_STATE,
        trigger_evidence=frozenset({E.SIGNAL_CONFLICT}),
        next_state_expr=CS.needs_review,
        next_bucket_expr=PRESERVE,
        task_intent_kind=TK.noop,
        category_intent_kind=CK.noop,
        events_emitted=(ET.needs_review_raised,),
    ),
    TransitionRow(
        id="T082",
        prior_states=ANY_STATE,
        trigger_evidence=frozenset({E.WRITEBACK_FAILURE_THRESHOLD}),
        next_state_expr=CS.needs_review,
        next_bucket_expr=PRESERVE,
        task_intent_kind=TK.dead_letter,
        category_intent_kind=CK.noop,
        events_emitted=(ET.needs_review_raised,),
    ),
    TransitionRow(
        id="T083",
        prior_states=frozenset({CS.needs_review}),
        trigger_evidence=frozenset({E.MANUAL_OVERRIDE}),
        guard_name="classifier_resolved_override",
        next_state_expr=PRESERVE,  # resolved to target state at runtime
        next_bucket_expr=PRESERVE,
        task_intent_kind=TK.update_fields,  # "per target"
        category_intent_kind=CK.apply,
        category_intent_target=PRESERVE,
        events_emitted=(ET.needs_review_resolved, ET.override_applied, ET.state_changed),
    ),
    TransitionRow(
        id="T084",
        prior_states=frozenset({CS.needs_review}),
        trigger_evidence=frozenset(),  # synthetic: disambiguating message
        guard_name="review_disambiguated",
        next_state_expr=PRESERVE,  # run normal reducer
        next_bucket_expr=PRESERVE,
        task_intent_kind=TK.noop,
        category_intent_kind=CK.noop,
        events_emitted=(ET.needs_review_resolved,),
    ),
    TransitionRow(
        id="T085",
        prior_states=frozenset({CS.needs_review}),
        # Placeholder: the buggy original used `ANY_STATE and frozenset(E)` which
        # resolved to a frozenset[ConversationState]. `_fix_t085()` below swaps in
        # the correct `frozenset(E)` at module load; we put an empty set here to
        # satisfy the type checker without changing runtime behavior.
        trigger_evidence=frozenset(),  # patched by _fix_t085() to frozenset(E)
        next_state_expr=CS.needs_review,
        next_bucket_expr=PRESERVE,
        task_intent_kind=TK.noop,
        category_intent_kind=CK.noop,
        events_emitted=(ET.reducer_ran_noop,),
    ),
    # ---------- §4.10 Manual override ----------
    TransitionRow(
        id="T090",
        prior_states=ANY_STATE,
        trigger_evidence=frozenset({E.MANUAL_OVERRIDE}),
        next_state_expr=PRESERVE,  # target state from override payload
        next_bucket_expr=PRESERVE,
        task_intent_kind=TK.update_fields,  # computed as if normal
        category_intent_kind=CK.apply,
        category_intent_target=PRESERVE,
        events_emitted=(ET.override_applied, ET.state_changed),
    ),
    # ---------- §4.11 Guarded no-ops (assertions) ----------
    TransitionRow(
        id="T100",
        prior_states=frozenset({CS.done}),
        trigger_evidence=frozenset({E.FYI_ONLY}),
        guard_name="completion_kind_hard",
        next_state_expr=CS.done,
        next_bucket_expr=PRESERVE,
        task_intent_kind=TK.noop,
        category_intent_kind=CK.noop,
        events_emitted=(ET.reducer_ran_noop,),
    ),
    TransitionRow(
        id="T101",
        prior_states=frozenset({CS.done}),
        trigger_evidence=frozenset({E.FYI_ONLY}),
        guard_name="completion_kind_soft_expired",
        next_state_expr=CS.done,
        next_bucket_expr=PRESERVE,
        task_intent_kind=TK.noop,
        category_intent_kind=CK.noop,
        events_emitted=(ET.reducer_ran_noop,),
    ),
    TransitionRow(
        id="T102",
        prior_states=frozenset({CS.waiting_on}),
        trigger_evidence=frozenset({E.USER_REPLIED_SATISFIES_ASK}),
        next_state_expr=CS.waiting_on,
        next_bucket_expr=CB.WaitingOn,
        task_intent_kind=TK.update_fields,  # never `create` (I2)
        category_intent_kind=CK.noop,
        events_emitted=(ET.task_updated,),
    ),
    TransitionRow(
        id="T103",
        prior_states=frozenset({CS.needs_review}),
        trigger_evidence=frozenset(E),  # any tier evidence
        next_state_expr=CS.needs_review,
        next_bucket_expr=PRESERVE,
        task_intent_kind=TK.noop,  # writeback noop (G7, I7)
        category_intent_kind=CK.noop,
        events_emitted=(ET.reducer_ran_noop,),
    ),
    TransitionRow(
        id="T104",
        prior_states=frozenset({CS.delegate_open}),
        trigger_evidence=frozenset(),  # classifier hint without E10 → no transition
        guard_name="classifier_hint_only_no_handoff",
        next_state_expr=CS.delegate_open,
        next_bucket_expr=CB.Delegate,
        task_intent_kind=TK.noop,
        category_intent_kind=CK.noop,
        events_emitted=(ET.reducer_ran_noop,),
    ),
]


# T085 above used a buggy "ANY_STATE and frozenset(E)" expr for trigger_evidence.
# Fix it to the intended semantics (match any evidence) at module-load time.
def _fix_t085() -> None:
    for i, row in enumerate(_ROWS):
        if row.id == "T085":
            _ROWS[i] = TransitionRow(
                id=row.id,
                prior_states=row.prior_states,
                trigger_evidence=frozenset(E),
                next_state_expr=row.next_state_expr,
                next_bucket_expr=row.next_bucket_expr,
                task_intent_kind=row.task_intent_kind,
                category_intent_kind=row.category_intent_kind,
                guard_name=row.guard_name,
                task_intent_target_bucket=row.task_intent_target_bucket,
                category_intent_target=row.category_intent_target,
                events_emitted=row.events_emitted,
            )
            return


_fix_t085()

TRANSITIONS: list[TransitionRow] = list(_ROWS)
TRANSITION_BY_ID: dict[str, TransitionRow] = {r.id: r for r in TRANSITIONS}

__all__ = [
    "TransitionRow",
    "TRANSITIONS",
    "TRANSITION_BY_ID",
    "PRESERVE",
    "POLICY",
]
