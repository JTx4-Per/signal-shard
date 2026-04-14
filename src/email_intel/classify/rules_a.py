"""Stage A · deterministic pre-classifier rules.

See project-plan §12.1 Stage A and §12.4 (rules-only bootstrap).

This module produces a ``StageAResult`` carrying a *provisional* bucket,
confidence, extracted signals, and a ``model_needed`` flag. It is pure and
deterministic: no wall-clock reads, no network calls.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any

import structlog

from email_intel.db.models import ConversationBucket
from email_intel.schemas.snapshot import (
    CanonicalMessage,
    ThreadSnapshot,
    UserRecipientPosition,
)

__all__ = ["StageAResult", "run_stage_a"]

log = structlog.get_logger(__name__)

# ---------------- regexes & constants ----------------

_NOREPLY_RE = re.compile(
    r"^(noreply|no-reply|donotreply|do-not-reply|mailer-daemon|postmaster)@",
    re.IGNORECASE,
)
_BULK_LOCALPART_RE = re.compile(
    r"^(newsletter|newsletters|marketing|deals|updates|promotions|news|offers|"
    r"notifications|notification)@",
    re.IGNORECASE,
)
_CALENDAR_SUBJECT_RE = re.compile(
    r"^\s*(accepted|declined|tentative)\s*:", re.IGNORECASE
)
_MEETING_INVITE_RE = re.compile(r"^\s*invitation\s*:", re.IGNORECASE)

_AUTOMATION_DOMAINS: frozenset[str] = frozenset(
    {
        "github.com",
        "gitlab.com",
        "atlassian.com",
        "atlassian.net",
        "jira.com",
        "bitbucket.org",
        "circleci.com",
        "travis-ci.com",
        "sentry.io",
        "pagerduty.com",
        "datadoghq.com",
        "statuspage.io",
    }
)
_AUTOMATION_MARKERS: tuple[str, ...] = (
    "view it on github",
    "you are receiving this because",
    "assigned to you",
    "pull request",
    "merge request",
    "build #",
    "pipeline",
    "ticket ",
    "issue #",
    "alert triggered",
    "incident",
    "automated",
)

_IMPERATIVE_PATTERNS: tuple[str, ...] = (
    r"\bplease\s+send\b",
    r"\bplease\s+review\b",
    r"\bplease\s+prepare\b",
    r"\bplease\s+draft\b",
    r"\bplease\s+provide\b",
    r"\bplease\s+complete\b",
    r"\bcan\s+you\s+(please\s+)?(send|prepare|review|draft|provide|complete|"
    r"update|confirm|share)\b",
    r"\bcould\s+you\s+(please\s+)?(send|prepare|review|draft|provide|complete|"
    r"update|confirm|share)\b",
    r"\bneed\s+you\s+to\b",
    r"\bi\s+need\s+you\s+to\b",
    r"\bwould\s+you\s+(please\s+)?(send|prepare|review|draft|provide)\b",
)
_IMPERATIVE_RE = re.compile("|".join(_IMPERATIVE_PATTERNS), re.IGNORECASE)

_WEEKDAYS: dict[str, int] = {
    "monday": 0,
    "mon": 0,
    "tuesday": 1,
    "tue": 1,
    "tues": 1,
    "wednesday": 2,
    "wed": 2,
    "thursday": 3,
    "thu": 3,
    "thurs": 3,
    "friday": 4,
    "fri": 4,
    "saturday": 5,
    "sat": 5,
    "sunday": 6,
    "sun": 6,
}

_BY_WEEKDAY_RE = re.compile(
    r"\bby\s+(next\s+)?("
    r"monday|mon|tuesday|tue|tues|wednesday|wed|thursday|thu|thurs|"
    r"friday|fri|saturday|sat|sunday|sun)\b",
    re.IGNORECASE,
)
_BY_NEXT_WEEK_RE = re.compile(r"\bby\s+next\s+week\b", re.IGNORECASE)
_BY_EOD_RE = re.compile(r"\bby\s+eo(d|w|m)\b", re.IGNORECASE)
_BY_ISO_DATE_RE = re.compile(
    r"\b(?:by|due|before)\s+(\d{4}-\d{2}-\d{2})\b", re.IGNORECASE
)
_BY_US_DATE_RE = re.compile(
    r"\b(?:by|due|before)\s+(\d{1,2}/\d{1,2}/\d{2,4})\b", re.IGNORECASE
)

_DEFER_PATTERNS: tuple[str, ...] = (
    r"\bnext\s+week\b",
    r"\bafter\s+the\s+\w+\s+meeting\b",
    r"\bQ[1-4]\b",
    r"\blater\s+this\s+(month|quarter|week)\b",
    r"\blet['\u2019]?s\s+revisit\b",
    r"\bcircle\s+back\b",
)
_DEFER_RE = re.compile("|".join(_DEFER_PATTERNS), re.IGNORECASE)


@dataclass(frozen=True)
class StageAResult:
    """Output of Stage A (deterministic pre-classifier)."""

    provisional_bucket: ConversationBucket | None
    confidence: float
    extracted_signals: dict[str, Any] = field(default_factory=dict)
    model_needed: bool = True
    matched_rules: list[str] = field(default_factory=list)
    reason_short: str = ""


# ---------------- helpers ----------------


def _latest_inbound(snapshot: ThreadSnapshot) -> CanonicalMessage | None:
    for msg in reversed(snapshot.messages):
        if not msg.is_from_user:
            return msg
    return None


def _sender_addr(msg: CanonicalMessage) -> str:
    return (msg.from_address or msg.sender_address or "").strip()


def _sender_domain(msg: CanonicalMessage) -> str:
    addr = _sender_addr(msg)
    if "@" not in addr:
        return ""
    return addr.rsplit("@", 1)[-1].lower()


def _has_list_unsubscribe(msg: CanonicalMessage) -> bool:
    headers = msg.headers or {}
    for k in headers:
        if k.lower() == "list-unsubscribe":
            return True
    return False


def _body_text(msg: CanonicalMessage) -> str:
    return (msg.body_text or msg.body_preview or "") or ""


def _parse_iso_date(value: str) -> datetime | None:
    try:
        return datetime.fromisoformat(value).replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def _next_weekday_from(base: datetime, target_dow: int, next_flag: bool) -> datetime:
    days_ahead = (target_dow - base.weekday()) % 7
    if days_ahead == 0:
        days_ahead = 7
    if next_flag and days_ahead < 7:
        days_ahead += 7
    return base + timedelta(days=days_ahead)


def _extract_due_at(
    text: str, anchor: datetime | None
) -> tuple[datetime | None, bool]:
    """Return (due_at, matched). Best-effort, deterministic given anchor."""
    m = _BY_ISO_DATE_RE.search(text)
    if m:
        dt = _parse_iso_date(m.group(1))
        if dt is not None:
            return dt, True

    m = _BY_US_DATE_RE.search(text)
    if m:
        parts = m.group(1).split("/")
        try:
            month = int(parts[0])
            day = int(parts[1])
            year_opt: int | None = int(parts[2]) if len(parts) > 2 else None
            if year_opt is not None and year_opt < 100:
                year_opt += 2000
            if year_opt is None and anchor is not None:
                year_opt = anchor.year
            if year_opt is not None:
                return (
                    datetime(year_opt, month, day, tzinfo=timezone.utc),
                    True,
                )
        except (ValueError, IndexError):
            pass

    if anchor is None:
        if (
            _BY_WEEKDAY_RE.search(text)
            or _BY_NEXT_WEEK_RE.search(text)
            or _BY_EOD_RE.search(text)
        ):
            return None, True
        return None, False

    m = _BY_WEEKDAY_RE.search(text)
    if m:
        next_flag = bool(m.group(1))
        dow = _WEEKDAYS[m.group(2).lower()]
        return _next_weekday_from(anchor, dow, next_flag), True

    if _BY_NEXT_WEEK_RE.search(text):
        return (anchor + timedelta(days=7)), True

    m = _BY_EOD_RE.search(text)
    if m:
        suffix = m.group(1).lower()
        if suffix == "d":
            return anchor.replace(hour=23, minute=59, second=0, microsecond=0), True
        if suffix == "w":
            days = (4 - anchor.weekday()) % 7
            return (
                (anchor + timedelta(days=days)).replace(
                    hour=23, minute=59, second=0, microsecond=0
                ),
                True,
            )
        if suffix == "m":
            return (anchor + timedelta(days=30)), True
    return None, False


# ---------------- main ----------------


def run_stage_a(
    snapshot: ThreadSnapshot,
    rule_version: str,
    write_threshold: float = 0.75,
) -> StageAResult:
    """Evaluate Stage A rules against ``snapshot``.

    ``model_needed`` is True when final confidence is below ``write_threshold``.
    """
    signals: dict[str, Any] = {
        "due_at": None,
        "defer_until": None,
        "waiting_on": None,
        "action_owner": None,
        "newsletter": False,
        "bulk": False,
        "automated": False,
        "unsubscribe_candidate": False,
        "delete_candidate": False,
    }
    matched: list[str] = []
    reasons: list[str] = []
    bucket: ConversationBucket | None = None
    confidence = 0.0

    latest_inbound = _latest_inbound(snapshot)

    if latest_inbound is None:
        if snapshot.user_sent_last:
            bucket = ConversationBucket.WaitingOn
            confidence = 0.7
            matched.append("A-user-sent-last")
            reasons.append("A-user-sent-last")
        return StageAResult(
            provisional_bucket=bucket,
            confidence=confidence,
            extracted_signals=signals,
            model_needed=confidence < write_threshold,
            matched_rules=matched,
            reason_short="; ".join(reasons) if reasons else "no-rules-fired",
        )

    sender = _sender_addr(latest_inbound).lower()
    sender_domain = _sender_domain(latest_inbound)
    has_list_unsub = _has_list_unsubscribe(latest_inbound)
    subject = latest_inbound.subject or ""
    body = _body_text(latest_inbound)
    body_head = body[:500]

    # ---- A-noreply-sender ----
    if _NOREPLY_RE.search(sender):
        bucket = ConversationBucket.FYI
        confidence = max(confidence, 0.95)
        matched.append("A-noreply-sender")
        reasons.append("A-noreply-sender")
        signals["automated"] = True
        if has_list_unsub:
            signals["unsubscribe_candidate"] = True
            signals["delete_candidate"] = True

    # ---- A-list-unsubscribe ----
    if has_list_unsub:
        signals["unsubscribe_candidate"] = True
        signals["delete_candidate"] = True
        signals["bulk"] = True
        if bucket is None or bucket == ConversationBucket.FYI:
            bucket = ConversationBucket.DeleteOrUnsubscribe
            confidence = max(confidence, 0.9)
        matched.append("A-list-unsubscribe")
        reasons.append("A-list-unsubscribe")

    # ---- A-bulk-sender ----
    if _BULK_LOCALPART_RE.search(sender):
        signals["newsletter"] = True
        signals["bulk"] = True
        if bucket is None:
            bucket = ConversationBucket.DeleteOrUnsubscribe
            confidence = max(confidence, 0.8)
        matched.append("A-bulk-sender")
        reasons.append("A-bulk-sender")

    # ---- A-calendar-accept ----
    if _CALENDAR_SUBJECT_RE.search(subject):
        bucket = ConversationBucket.FYI
        confidence = max(confidence, 0.9)
        matched.append("A-calendar-accept")
        reasons.append("A-calendar-accept")

    # ---- A-automation-system ----
    automation_marker_hit = any(
        marker in body.lower() for marker in _AUTOMATION_MARKERS
    )
    domain_is_automation = sender_domain in _AUTOMATION_DOMAINS or any(
        sender_domain.endswith("." + d) for d in _AUTOMATION_DOMAINS
    )
    if domain_is_automation and automation_marker_hit:
        signals["automated"] = True
        if bucket is None:
            bucket = ConversationBucket.FYI
            confidence = max(confidence, 0.7)
        matched.append("A-automation-system")
        reasons.append("A-automation-system")

    # ---- A-direct-ask-question ----
    user_is_to = latest_inbound.user_position == UserRecipientPosition.TO
    stripped = body.rstrip()
    ends_with_q = stripped.endswith("?")
    if ends_with_q and user_is_to:
        if bucket is None or confidence < 0.7:
            bucket = ConversationBucket.Respond
            confidence = max(confidence, 0.7)
        matched.append("A-direct-ask-question")
        reasons.append("A-direct-ask-question")

    # ---- A-direct-ask-verb ----
    if _IMPERATIVE_RE.search(body_head):
        if bucket is None or confidence < 0.7:
            bucket = ConversationBucket.Act
            confidence = max(confidence, 0.7)
        matched.append("A-direct-ask-verb")
        reasons.append("A-direct-ask-verb")

    # ---- A-due-date-phrase ----
    anchor = latest_inbound.received_at or latest_inbound.sent_at
    due_at, due_matched = _extract_due_at(body, anchor)
    if due_matched:
        signals["due_at"] = due_at
        matched.append("A-due-date-phrase")
        reasons.append("A-due-date-phrase")

    # ---- A-defer-phrase ----
    if _DEFER_RE.search(body):
        matched.append("A-defer-phrase")
        reasons.append("A-defer-phrase")

    # ---- A-user-sent-last ----
    if snapshot.user_sent_last:
        if bucket is None or bucket == ConversationBucket.Respond:
            bucket = ConversationBucket.WaitingOn
            confidence = max(confidence, 0.7)
        matched.append("A-user-sent-last")
        reasons.append("A-user-sent-last")

    # ---- A-cc-only-no-ask ----
    user_is_cc = latest_inbound.user_position == UserRecipientPosition.CC
    direct_ask_fired = (
        "A-direct-ask-question" in matched or "A-direct-ask-verb" in matched
    )
    if user_is_cc and not direct_ask_fired:
        if bucket is None:
            bucket = ConversationBucket.FYI
            confidence = max(confidence, 0.6)
        matched.append("A-cc-only-no-ask")
        reasons.append("A-cc-only-no-ask")

    # ---- A-meeting-invite-rsvp ----
    has_ics = any(
        (k.lower() == "content-type" and "text/calendar" in str(v).lower())
        for k, v in (latest_inbound.headers or {}).items()
    )
    if _MEETING_INVITE_RE.search(subject) or has_ics:
        if bucket is None or confidence < 0.6:
            bucket = ConversationBucket.Respond
            confidence = max(confidence, 0.6)
        matched.append("A-meeting-invite-rsvp")
        reasons.append("A-meeting-invite-rsvp")

    model_needed = confidence < write_threshold
    reason_short = "; ".join(reasons) if reasons else "no-rules-fired"

    log.debug(
        "stage_a.eval",
        rule_version=rule_version,
        matched_rules=matched,
        provisional_bucket=bucket.value if bucket else None,
        confidence=confidence,
        model_needed=model_needed,
    )

    return StageAResult(
        provisional_bucket=bucket,
        confidence=confidence,
        extracted_signals=signals,
        model_needed=model_needed,
        matched_rules=matched,
        reason_short=reason_short,
    )
