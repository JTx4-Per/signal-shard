"""Guard registry — reducer-spec §4 `guard_name` values.

Each guard is a pure predicate of `(ReducerInput, now)`. Wave 1's
`transitions.py` references these by name string.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime

from email_intel.db.models import CompletionKind, ConversationBucket, ConversationState
from email_intel.schemas.events import Evidence
from email_intel.schemas.reducer import ReducerInput

__all__ = ["GUARDS", "Guard"]

Guard = Callable[[ReducerInput, datetime], bool]


# --------------------- individual guards ---------------------
def _not_handoff_confirmed(inp: ReducerInput, now: datetime) -> bool:
    return Evidence.HANDOFF_CONFIRMED not in inp.evidence_set


def _original_ask_was_act(inp: ReducerInput, now: datetime) -> bool:
    return inp.snapshot.prior_bucket == ConversationBucket.Act or inp.prior_bucket == ConversationBucket.Act


def _original_ask_was_respond(inp: ReducerInput, now: datetime) -> bool:
    return (
        inp.snapshot.prior_bucket == ConversationBucket.Respond
        or inp.prior_bucket == ConversationBucket.Respond
    )


def _reopen_eligible_hard_or_soft_in_window(inp: ReducerInput, now: datetime) -> bool:
    kind = inp.snapshot.prior_completion_kind
    if kind == CompletionKind.hard:
        return True
    if kind == CompletionKind.soft:
        until = inp.snapshot.prior_soft_complete_until
        return until is not None and now <= until
    return False


def _reopen_eligible_hard(inp: ReducerInput, now: datetime) -> bool:
    return inp.snapshot.prior_completion_kind == CompletionKind.hard


def _reopen_eligible_soft_within_window(inp: ReducerInput, now: datetime) -> bool:
    if inp.snapshot.prior_completion_kind != CompletionKind.soft:
        return False
    until = inp.snapshot.prior_soft_complete_until
    return until is not None and now <= until


def _reopen_eligible_soft_expired(inp: ReducerInput, now: datetime) -> bool:
    if inp.snapshot.prior_completion_kind != CompletionKind.soft:
        return False
    until = inp.snapshot.prior_soft_complete_until
    return until is not None and now > until


def _soft_expired_window(inp: ReducerInput, now: datetime) -> bool:
    return _reopen_eligible_soft_expired(inp, now)


def _soft_window_open_continuation(inp: ReducerInput, now: datetime) -> bool:
    # T063: soft window open + classifier says "not a new ask".
    if not _reopen_eligible_soft_within_window(inp, now):
        return False
    # If a new inbound ask is present, this row does NOT match (T060/T061 do).
    if Evidence.NEW_INBOUND_ASK_DELIVERABLE in inp.evidence_set:
        return False
    if Evidence.NEW_INBOUND_ASK_REPLY in inp.evidence_set:
        return False
    # FYI alone does not reopen (T065). Needs a content signal.
    if Evidence.FYI_ONLY in inp.evidence_set and len(inp.evidence_set - {Evidence.FYI_ONLY}) == 0:
        return False
    triggers = {
        Evidence.USER_REPLIED_SATISFIES_ASK,
        Evidence.DUE_DATE_UPDATE,
    }
    return bool(inp.evidence_set & triggers)


def _completion_kind_hard(inp: ReducerInput, now: datetime) -> bool:
    return inp.snapshot.prior_completion_kind == CompletionKind.hard


def _completion_kind_soft_in_window(inp: ReducerInput, now: datetime) -> bool:
    return _reopen_eligible_soft_within_window(inp, now)


def _completion_kind_soft_expired(inp: ReducerInput, now: datetime) -> bool:
    return _reopen_eligible_soft_expired(inp, now)


def _archive_window_elapsed(inp: ReducerInput, now: datetime) -> bool:
    # Synthetic — caller injects by adding no normal evidence and passing
    # `prior_state=done` with an age exceeding policy. Without an archive
    # timestamp on the snapshot we cannot auto-detect; so this guard only
    # fires when the caller pre-sets classifications_json hint.
    cls_list = inp.snapshot.classifications_json
    if not cls_list:
        return False
    return bool(cls_list[-1].get("archive_window_elapsed"))


def _classifier_resolved_override(inp: ReducerInput, now: datetime) -> bool:
    return inp.manual_override is not None


def _review_disambiguated(inp: ReducerInput, now: datetime) -> bool:
    cls_list = inp.snapshot.classifications_json
    if not cls_list:
        return False
    return bool(cls_list[-1].get("review_disambiguated"))


def _classifier_hint_only_no_handoff(inp: ReducerInput, now: datetime) -> bool:
    # T104: prior state delegate_open, no E10, classifier "looks delegated" hint.
    if Evidence.HANDOFF_CONFIRMED in inp.evidence_set:
        return False
    cls_list = inp.snapshot.classifications_json
    if not cls_list:
        return False
    return bool(cls_list[-1].get("delegate_hint"))


def _delegate_handoff_present(inp: ReducerInput, now: datetime) -> bool:
    return Evidence.HANDOFF_CONFIRMED in inp.evidence_set


def _sent_items_caught_up(inp: ReducerInput, now: datetime) -> bool:
    return Evidence.SENT_ITEMS_LAG not in inp.evidence_set


def _has_active_task(inp: ReducerInput, now: datetime) -> bool:
    return inp.snapshot.prior_task_id is not None


def _reopen_eligible_hard_evidence_present(inp: ReducerInput, now: datetime) -> bool:
    if inp.snapshot.prior_completion_kind != CompletionKind.hard:
        return False
    return bool(
        inp.evidence_set
        & {Evidence.NEW_INBOUND_ASK_DELIVERABLE, Evidence.NEW_INBOUND_ASK_REPLY}
    )


# --------------------- registry ---------------------
GUARDS: dict[str, Guard] = {
    "not_handoff_confirmed": _not_handoff_confirmed,
    "original_ask_was_act": _original_ask_was_act,
    "original_ask_was_respond": _original_ask_was_respond,
    "reopen_eligible_hard_or_soft_in_window": _reopen_eligible_hard_or_soft_in_window,
    "reopen_eligible_hard": _reopen_eligible_hard_evidence_present,
    "reopen_eligible_soft_within_window": _reopen_eligible_soft_within_window,
    "reopen_eligible_soft_expired": _reopen_eligible_soft_expired,
    "soft_expired_window": _soft_expired_window,
    "soft_window_open_continuation": _soft_window_open_continuation,
    "completion_kind_hard": _completion_kind_hard,
    "completion_kind_soft_in_window": _completion_kind_soft_in_window,
    "completion_kind_soft_expired": _completion_kind_soft_expired,
    "archive_window_elapsed": _archive_window_elapsed,
    "classifier_resolved_override": _classifier_resolved_override,
    "review_disambiguated": _review_disambiguated,
    "classifier_hint_only_no_handoff": _classifier_hint_only_no_handoff,
    "delegate_handoff_present": _delegate_handoff_present,
    "sent_items_caught_up": _sent_items_caught_up,
    "has_active_task": _has_active_task,
}

_ = ConversationState  # re-export hint
