"""Shared helpers to build minimal ThreadSnapshots for classifier tests."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from email_intel.schemas.snapshot import (
    CanonicalMessage,
    ThreadSnapshot,
    UserRecipientPosition,
)

_FIXED_RECEIVED = datetime(2026, 4, 13, 12, 0, 0, tzinfo=timezone.utc)


def make_message(
    *,
    graph_message_id: str = "m1",
    graph_conversation_id: str = "c1",
    from_address: str | None = "alice@example.com",
    to_addresses: list[str] | None = None,
    cc_addresses: list[str] | None = None,
    is_from_user: bool = False,
    subject: str | None = "Hello",
    body_text: str | None = "Hi there.",
    user_position: UserRecipientPosition = UserRecipientPosition.TO,
    received_at: datetime | None = _FIXED_RECEIVED,
    sent_at: datetime | None = None,
    headers: dict[str, Any] | None = None,
) -> CanonicalMessage:
    return CanonicalMessage(
        graph_message_id=graph_message_id,
        graph_conversation_id=graph_conversation_id,
        from_address=from_address,
        sender_address=from_address,
        to_addresses=list(to_addresses or ["me@example.com"]),
        cc_addresses=list(cc_addresses or []),
        received_at=received_at,
        sent_at=sent_at,
        is_from_user=is_from_user,
        subject=subject,
        body_text=body_text,
        body_preview=(body_text[:100] if body_text else None),
        user_position=user_position,
        has_attachments=False,
        categories=[],
        headers=dict(headers or {}),
    )


def make_snapshot(
    *,
    messages: list[CanonicalMessage] | None = None,
    user_sent_last: bool = False,
    user_position_on_latest: UserRecipientPosition = UserRecipientPosition.TO,
    conversation_id: int = 1,
    graph_conversation_id: str = "c1",
) -> ThreadSnapshot:
    msgs = messages if messages is not None else [make_message()]
    latest_inbound = next(
        (m.received_at for m in reversed(msgs) if not m.is_from_user), None
    )
    latest_outbound = next(
        (m.sent_at or m.received_at for m in reversed(msgs) if m.is_from_user),
        None,
    )
    return ThreadSnapshot(
        conversation_id=conversation_id,
        graph_conversation_id=graph_conversation_id,
        messages=msgs,
        latest_inbound_ts=latest_inbound,
        latest_outbound_ts=latest_outbound,
        user_sent_last=user_sent_last,
        user_position_on_latest=user_position_on_latest,
    )
