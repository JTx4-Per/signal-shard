"""Evidence detector — maps snapshot state to Layer-1 outcomes.

reducer-spec §3 rows E01–E15. Pure, no I/O. Small named predicates so a
spec-to-code audit is row-by-row.
"""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any

from email_intel.config import Settings
from email_intel.db.models import CompletionKind, ConversationBucket, ConversationState
from email_intel.schemas.events import Evidence, tier_for
from email_intel.schemas.snapshot import (
    CanonicalMessage,
    ThreadSnapshot,
    UserRecipientPosition,
)

__all__ = ["detect_evidence"]


# ---------- regex constants (reducer-spec §3) ----------
EXPLICIT_RESOLUTION_RE = re.compile(
    r"\b(done|closed|resolved|signed|cancell?ed|completed|wrapped up)\b",
    re.IGNORECASE,
)
SOFT_RESOLUTION_RE = re.compile(
    r"\b(sounds? (like )?we(\'re| are) good|looks fine|ok,?\s*(that works|looks fine)|"
    r"no further action|nothing else needed)\b",
    re.IGNORECASE,
)
FUTURE_DATE_RE = re.compile(
    r"\b(next (week|month|quarter)|after (the )?\w+\s+(meeting|call|review)|"
    r"q[1-4]\b|later this (week|month)|in (a|the) (few|couple of) (days|weeks))\b",
    re.IGNORECASE,
)
IMPERATIVE_VERB_RE = re.compile(
    r"\b(send|prepare|review|complete|submit|draft|write|update|fix|"
    r"schedule|book|confirm|finalize)\b.{0,60}",
    re.IGNORECASE,
)
DIRECT_QUESTION_RE = re.compile(
    r"(\bcan you\b|\bcould you\b|\bwould you\b|\bwill you\b|\bdo you\b|"
    r"\bplease (let|confirm|advise)\b|\?)",
    re.IGNORECASE,
)
HANDOFF_LANGUAGE_RE = re.compile(
    r"\b(forwarding (this )?to|will own this|please take (this|over)|"
    r"handing (this )?off|over to you|you'?ll own|assigning (this )?to)\b",
    re.IGNORECASE,
)
MEETING_INVITE_RE = re.compile(
    r"\b(meeting (invite|invitation)|rsvp|accepted|tentative|declined|"
    r"calendar invite)\b",
    re.IGNORECASE,
)
BULK_SENDER_RE = re.compile(
    r"(noreply|no-reply|newsletter|mailer|notifications?@|marketing@|info@)",
    re.IGNORECASE,
)


def _latest_classification(snapshot: ThreadSnapshot) -> dict[str, Any] | None:
    if not snapshot.classifications_json:
        return None
    return snapshot.classifications_json[-1]


def _latest_inbound(snapshot: ThreadSnapshot) -> CanonicalMessage | None:
    for msg in reversed(snapshot.messages):
        if not msg.is_from_user:
            return msg
    return None


def _latest_outbound(snapshot: ThreadSnapshot) -> CanonicalMessage | None:
    for msg in reversed(snapshot.messages):
        if msg.is_from_user:
            return msg
    return None


def _last_inbound_ask_ts(snapshot: ThreadSnapshot) -> datetime | None:
    msg = _latest_inbound(snapshot)
    return msg.received_at if msg else None


def _has_list_unsubscribe(msg: CanonicalMessage | None) -> bool:
    if msg is None:
        return False
    headers = {k.lower(): v for k, v in msg.headers.items()}
    return "list-unsubscribe" in headers


def _text_of(msg: CanonicalMessage | None) -> str:
    if msg is None:
        return ""
    parts = [msg.subject or "", msg.body_text or "", msg.body_preview or ""]
    return " \n ".join(parts)


# ---------- E01 ----------
def _e01_new_inbound_ask_deliverable(
    snapshot: ThreadSnapshot, cls: dict[str, Any] | None
) -> bool:
    if cls is not None:
        pb = cls.get("primary_bucket")
        if pb == ConversationBucket.Act.value and cls.get("should_create_task"):
            return True
    inbound = _latest_inbound(snapshot)
    if inbound is None:
        return False
    # Rule fallback: imperative verb aimed at the user + inbound to/cc includes user.
    if snapshot.user_position_on_latest in (
        UserRecipientPosition.TO,
        UserRecipientPosition.CC,
    ):
        body = _text_of(inbound)
        if IMPERATIVE_VERB_RE.search(body):
            return True
    if snapshot.unresolved_asks and cls is None:
        # an unresolved ask recorded in snapshot counts as deliverable ask
        return True
    return False


# ---------- E02 ----------
def _e02_new_inbound_ask_reply(
    snapshot: ThreadSnapshot, cls: dict[str, Any] | None
) -> bool:
    if cls is not None:
        if cls.get("primary_bucket") == ConversationBucket.Respond.value:
            return True
    inbound = _latest_inbound(snapshot)
    if inbound is None:
        return False
    body = _text_of(inbound)
    if DIRECT_QUESTION_RE.search(body):
        return True
    if MEETING_INVITE_RE.search(body):
        return True
    return False


# ---------- E03 ----------
def _e03_user_replied_satisfies_ask(
    snapshot: ThreadSnapshot, cls: dict[str, Any] | None
) -> bool:
    if not snapshot.user_sent_last:
        return False
    if snapshot.latest_outbound_ts is None or snapshot.latest_inbound_ts is None:
        # if no prior inbound ask, cannot "satisfy" it
        return snapshot.user_sent_last and snapshot.latest_outbound_ts is not None and snapshot.latest_inbound_ts is not None
    if snapshot.latest_outbound_ts <= snapshot.latest_inbound_ts:
        return False
    if cls is not None and cls.get("should_create_task"):
        return False
    return True


# ---------- E04 ----------
def _e04_explicit_resolution(
    snapshot: ThreadSnapshot, cls: dict[str, Any] | None
) -> bool:
    inbound = _latest_inbound(snapshot)
    if inbound is None:
        return False
    text = _text_of(inbound)
    if EXPLICIT_RESOLUTION_RE.search(text):
        return True
    # counterparty confirms completion of the active ask
    if cls is not None and cls.get("reason_short", "") and "confirm" in str(cls.get("reason_short", "")).lower():
        if "complete" in str(cls.get("reason_short", "")).lower() or "resolved" in str(cls.get("reason_short", "")).lower():
            return True
    return False


# ---------- E05 ----------
def _e05_soft_resolution(snapshot: ThreadSnapshot, cls: dict[str, Any] | None) -> bool:
    inbound = _latest_inbound(snapshot)
    if inbound is None:
        return False
    text = _text_of(inbound)
    if SOFT_RESOLUTION_RE.search(text):
        return True
    if cls is not None:
        reason = str(cls.get("reason_short", "")).lower()
        if "soft" in reason and ("resolve" in reason or "complete" in reason):
            return True
    return False


# ---------- E06 ----------
def _e06_explicit_defer(snapshot: ThreadSnapshot, cls: dict[str, Any] | None) -> bool:
    if cls is not None and cls.get("defer_until"):
        return True
    inbound = _latest_inbound(snapshot)
    if inbound is None:
        return False
    return bool(FUTURE_DATE_RE.search(_text_of(inbound)))


# ---------- E07 ----------
def _e07_defer_timer_fired(
    snapshot: ThreadSnapshot, prior_state: ConversationState, now: datetime
) -> bool:
    if prior_state != ConversationState.deferred:
        return False
    du = snapshot.deferred_until
    return du is not None and du <= now


# ---------- E08 ----------
def _e08_bulk_noise(snapshot: ThreadSnapshot, cls: dict[str, Any] | None) -> bool:
    if cls is not None:
        if cls.get("unsubscribe_candidate") or cls.get("delete_candidate"):
            return True
        if cls.get("primary_bucket") == ConversationBucket.DeleteOrUnsubscribe.value:
            return True
        if cls.get("newsletter") or cls.get("automated"):
            return True
    inbound = _latest_inbound(snapshot)
    if _has_list_unsubscribe(inbound):
        return True
    if inbound is not None and inbound.from_address:
        if BULK_SENDER_RE.search(inbound.from_address):
            return True
    return False


# ---------- E09 ----------
def _e09_fyi_only(snapshot: ThreadSnapshot, cls: dict[str, Any] | None) -> bool:
    inbound = _latest_inbound(snapshot)
    if cls is not None and cls.get("primary_bucket") == ConversationBucket.FYI.value:
        return True
    if inbound is None:
        return False
    if snapshot.user_position_on_latest == UserRecipientPosition.CC:
        body = _text_of(inbound)
        if not DIRECT_QUESTION_RE.search(body) and not _has_list_unsubscribe(inbound):
            return True
    return False


# ---------- E10 ----------
def _e10_handoff_confirmed(snapshot: ThreadSnapshot) -> bool:
    outbound = _latest_outbound(snapshot)
    if outbound is None:
        return False
    text = _text_of(outbound)
    return bool(HANDOFF_LANGUAGE_RE.search(text))


# ---------- E11 ----------
def _e11_due_date_update(snapshot: ThreadSnapshot, cls: dict[str, Any] | None) -> bool:
    if cls is None:
        return False
    new_due = cls.get("due_at")
    if new_due is None:
        return False
    return bool(new_due != snapshot.latest_due_at)


# ---------- E12 handled via ReducerInput.manual_override ----------


# ---------- E13 ----------
def _e13_sent_items_lag(snapshot: ThreadSnapshot, has_tier3_candidate: bool) -> bool:
    if not has_tier3_candidate:
        return False
    if snapshot.sent_items_cursor_ts is None or snapshot.latest_inbound_ts is None:
        return False
    return snapshot.sent_items_cursor_ts < snapshot.latest_inbound_ts


# ---------- E14 ----------
def _e14_signal_conflict(snapshot: ThreadSnapshot) -> bool:
    # Inspect the last N classifications; if two are in the same tier with
    # high confidence but disagree on bucket → conflict.
    if len(snapshot.classifications_json) < 2:
        return False
    last_n = snapshot.classifications_json[-3:]
    high_conf: list[dict[str, Any]] = [
        c for c in last_n if float(c.get("confidence", 0.0)) >= 0.75 and c.get("primary_bucket")
    ]
    if len(high_conf) < 2:
        return False
    by_bucket: dict[str, str] = {}
    for c in high_conf:
        bucket = str(c["primary_bucket"])
        ev_for_bucket = _bucket_to_evidence(bucket)
        if ev_for_bucket is None:
            continue
        tier = tier_for(ev_for_bucket).value
        key = str(tier)
        if key in by_bucket and by_bucket[key] != bucket:
            return True
        by_bucket[key] = bucket
    return False


def _bucket_to_evidence(bucket_value: str) -> Evidence | None:
    mapping = {
        ConversationBucket.Act.value: Evidence.NEW_INBOUND_ASK_DELIVERABLE,
        ConversationBucket.Respond.value: Evidence.NEW_INBOUND_ASK_REPLY,
        ConversationBucket.FYI.value: Evidence.FYI_ONLY,
        ConversationBucket.DeleteOrUnsubscribe.value: Evidence.BULK_NOISE,
        ConversationBucket.Defer.value: Evidence.EXPLICIT_DEFER,
    }
    return mapping.get(bucket_value)


# ---------- E15 ----------
def _e15_writeback_failure_threshold(
    snapshot: ThreadSnapshot, threshold: int
) -> bool:
    # Snapshot may carry a counter field via classifications_json metadata.
    # Public API: callers who know the counter should inject E15 into the
    # evidence_set directly. Here we provide a conservative check that
    # looks for a `writeback_failure_count` entry in the latest classification.
    cls = _latest_classification(snapshot)
    if cls is None:
        return False
    count = cls.get("writeback_failure_count")
    try:
        return count is not None and int(count) >= threshold
    except (TypeError, ValueError):
        return False


# ---------- public entry point ----------
def detect_evidence(
    snapshot: ThreadSnapshot,
    prior_state: ConversationState,
    prior_bucket: ConversationBucket | None,
    now: datetime,
    settings: Settings,
) -> set[Evidence]:
    """Return every Evidence E01–E15 that applies to `snapshot` at `now`."""
    cls = _latest_classification(snapshot)
    out: set[Evidence] = set()

    if _e01_new_inbound_ask_deliverable(snapshot, cls):
        out.add(Evidence.NEW_INBOUND_ASK_DELIVERABLE)
    if _e02_new_inbound_ask_reply(snapshot, cls):
        out.add(Evidence.NEW_INBOUND_ASK_REPLY)

    tier3 = _e03_user_replied_satisfies_ask(snapshot, cls)
    if tier3:
        out.add(Evidence.USER_REPLIED_SATISFIES_ASK)

    if _e04_explicit_resolution(snapshot, cls):
        out.add(Evidence.EXPLICIT_RESOLUTION)
    if _e05_soft_resolution(snapshot, cls):
        out.add(Evidence.SOFT_RESOLUTION)
    if _e06_explicit_defer(snapshot, cls):
        out.add(Evidence.EXPLICIT_DEFER)
    if _e07_defer_timer_fired(snapshot, prior_state, now):
        out.add(Evidence.DEFER_TIMER_FIRED)
    if _e08_bulk_noise(snapshot, cls):
        out.add(Evidence.BULK_NOISE)
    if _e09_fyi_only(snapshot, cls):
        out.add(Evidence.FYI_ONLY)
    if _e10_handoff_confirmed(snapshot):
        out.add(Evidence.HANDOFF_CONFIRMED)
    if _e11_due_date_update(snapshot, cls):
        out.add(Evidence.DUE_DATE_UPDATE)
    if _e13_sent_items_lag(snapshot, tier3):
        out.add(Evidence.SENT_ITEMS_LAG)
    if _e14_signal_conflict(snapshot):
        out.add(Evidence.SIGNAL_CONFLICT)

    threshold = int(getattr(settings, "WRITEBACK_FAILURE_THRESHOLD", 5))
    if _e15_writeback_failure_threshold(snapshot, threshold):
        out.add(Evidence.WRITEBACK_FAILURE_THRESHOLD)

    # E12 is not detectable from snapshot alone — injected by reducer when
    # ReducerInput.manual_override is set.
    _ = prior_bucket  # reserved for future use
    _ = CompletionKind  # re-export hint for typing
    return out
