"""Reducer-test fixtures — synchronous; no DB needed for most tests."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

import pytest

from email_intel.config import Settings
from email_intel.db.models import (
    CompletionKind,
    ConversationBucket,
    ConversationState,
)
from email_intel.schemas.events import Evidence
from email_intel.schemas.reducer import ManualOverride, ReducerInput
from email_intel.schemas.snapshot import (
    CanonicalMessage,
    ThreadSnapshot,
    UserRecipientPosition,
)

NOW = datetime(2026, 4, 13, 12, 0, 0, tzinfo=timezone.utc)


@pytest.fixture
def settings() -> Settings:
    return Settings()


@pytest.fixture
def now() -> datetime:
    return NOW


def make_msg(
    *,
    idx: int = 0,
    is_from_user: bool = False,
    subject: str = "",
    body: str = "",
    user_position: UserRecipientPosition = UserRecipientPosition.TO,
    headers: dict[str, Any] | None = None,
    ts: datetime | None = None,
    from_address: str | None = "counter@example.com",
) -> CanonicalMessage:
    ts = ts or (NOW - timedelta(minutes=60 - idx))
    return CanonicalMessage(
        graph_message_id=f"m{idx}",
        graph_conversation_id="g1",
        from_address=from_address if not is_from_user else "user@example.com",
        sender_address=from_address if not is_from_user else "user@example.com",
        to_addresses=["user@example.com"] if not is_from_user else ["counter@example.com"],
        cc_addresses=[],
        received_at=ts if not is_from_user else None,
        sent_at=ts if is_from_user else None,
        is_from_user=is_from_user,
        subject=subject,
        body_text=body,
        body_preview=body[:80],
        user_position=user_position if not is_from_user else UserRecipientPosition.NONE,
        headers=headers or {},
    )


def make_snapshot(
    *,
    conversation_id: int = 1,
    messages: list[CanonicalMessage] | None = None,
    user_sent_last: bool = False,
    latest_inbound_ts: datetime | None = None,
    latest_outbound_ts: datetime | None = None,
    sent_items_cursor_ts: datetime | None = None,
    classifications: list[dict[str, Any]] | None = None,
    prior_task_id: int | None = None,
    prior_completion_kind: CompletionKind | None = None,
    prior_soft_complete_until: datetime | None = None,
    prior_bucket: ConversationBucket | None = None,
    deferred_until: datetime | None = None,
    latest_due_at: datetime | None = None,
    unresolved_asks: list[str] | None = None,
) -> ThreadSnapshot:
    msgs = messages or []
    return ThreadSnapshot(
        conversation_id=conversation_id,
        graph_conversation_id=f"g{conversation_id}",
        messages=msgs,
        user_sent_last=user_sent_last,
        latest_inbound_ts=latest_inbound_ts,
        latest_outbound_ts=latest_outbound_ts,
        sent_items_cursor_ts=sent_items_cursor_ts,
        classifications_json=classifications or [],
        prior_task_id=prior_task_id,
        prior_completion_kind=prior_completion_kind,
        prior_soft_complete_until=prior_soft_complete_until,
        prior_bucket=prior_bucket,
        deferred_until=deferred_until,
        latest_due_at=latest_due_at,
        unresolved_asks=unresolved_asks or [],
    )


def make_input(
    *,
    snapshot: ThreadSnapshot | None = None,
    prior_state: ConversationState = ConversationState.none,
    prior_bucket: ConversationBucket | None = None,
    evidence: set[Evidence] | frozenset[Evidence] = frozenset(),
    manual_override: ManualOverride | None = None,
    now: datetime = NOW,
) -> ReducerInput:
    return ReducerInput(
        snapshot=snapshot or make_snapshot(),
        prior_state=prior_state,
        prior_bucket=prior_bucket,
        now=now,
        evidence_set=frozenset(evidence),
        manual_override=manual_override,
    )


@pytest.fixture
def make_msg_fx():
    return make_msg


@pytest.fixture
def make_snapshot_fx():
    return make_snapshot


@pytest.fixture
def make_input_fx():
    return make_input
