"""Tests for ingestion.snapshot_builder."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from email_intel.db.models import (
    Conversation,
    ConversationBucket,
    ConversationState,
    MailFolder,
    Message,
    User,
)
from email_intel.ingestion.snapshot_builder import build_snapshot
from email_intel.schemas.snapshot import UserRecipientPosition


async def _seed(session: AsyncSession) -> tuple[User, MailFolder, Conversation]:
    u = User(graph_user_id="u1", email="me@example.com")
    session.add(u)
    await session.flush()
    folder = MailFolder(
        user_id=u.id, graph_folder_id="f1", well_known_name="inbox", delta_token=None
    )
    session.add(folder)
    await session.flush()
    conv = Conversation(
        user_id=u.id,
        graph_conversation_id="CONV-A",
        canonical_subject="Project X",
        open_action_state=ConversationState.respond_open,
        open_action_bucket=ConversationBucket.Respond,
    )
    session.add(conv)
    await session.flush()
    return u, folder, conv


def _make_msg(
    user: User,
    folder: MailFolder,
    graph_conv: str,
    gmid: str,
    received_at: datetime,
    from_address: str,
    to: list[str],
    body: str = "",
) -> Message:
    return Message(
        user_id=user.id,
        graph_message_id=gmid,
        graph_conversation_id=graph_conv,
        folder_id=folder.id,
        subject="Re: Project X",
        from_address=from_address.lower(),
        sender_address=from_address.lower(),
        to_recipients_json=[{"address": a, "name": a} for a in to],
        cc_recipients_json=[],
        received_at=received_at,
        sent_at=received_at,
        is_read=False,
        has_attachments=False,
        body_text=body,
        body_preview=body[:100],
        is_deleted=False,
    )


@pytest.mark.asyncio
async def test_build_snapshot_mixed_thread(session: AsyncSession) -> None:
    u, folder, conv = await _seed(session)

    t0 = datetime(2026, 4, 10, 9, 0, tzinfo=timezone.utc)
    # Inbound -> user responded -> inbound again
    session.add_all([
        _make_msg(
            u, folder, conv.graph_conversation_id, "M1",
            received_at=t0,
            from_address="alice@example.com",
            to=["me@example.com"],
            body="Could you send the report? Any updates?",
        ),
        _make_msg(
            u, folder, conv.graph_conversation_id, "M2",
            received_at=t0 + timedelta(hours=1),
            from_address="me@example.com",
            to=["alice@example.com"],
            body="Working on it.",
        ),
        _make_msg(
            u, folder, conv.graph_conversation_id, "M3",
            received_at=t0 + timedelta(hours=2),
            from_address="alice@example.com",
            to=["me@example.com"],
            body="Thanks! When will it be done?",
        ),
    ])
    await session.flush()

    sent_cursor = t0 + timedelta(hours=3)
    snap = await build_snapshot(
        session,
        conversation_id=conv.id,
        sent_items_cursor_ts=sent_cursor,
        user_address="me@example.com",
    )

    assert snap.conversation_id == conv.id
    assert snap.graph_conversation_id == "CONV-A"
    assert len(snap.messages) == 3
    assert snap.latest_inbound_ts is not None
    assert snap.latest_inbound_ts.replace(tzinfo=timezone.utc) == t0 + timedelta(hours=2)
    assert snap.latest_outbound_ts is not None
    assert snap.latest_outbound_ts.replace(tzinfo=timezone.utc) == t0 + timedelta(hours=1)
    assert snap.user_sent_last is False  # M3 from Alice
    assert snap.user_position_on_latest == UserRecipientPosition.TO
    assert any("?" in a for a in snap.unresolved_asks)
    assert snap.sent_items_cursor_ts == sent_cursor
    assert snap.prior_state == ConversationState.respond_open
    assert snap.prior_bucket == ConversationBucket.Respond


@pytest.mark.asyncio
async def test_sent_lag_carried_through(session: AsyncSession) -> None:
    u, folder, conv = await _seed(session)

    t0 = datetime(2026, 4, 11, 12, 0, tzinfo=timezone.utc)
    session.add(
        _make_msg(
            u, folder, conv.graph_conversation_id, "M1",
            received_at=t0,
            from_address="bob@example.com",
            to=["me@example.com"],
            body="Need a status update.",
        ),
    )
    await session.flush()

    # Sent cursor lags behind latest inbound
    lag_cursor = t0 - timedelta(hours=1)

    snap = await build_snapshot(
        session,
        conversation_id=conv.id,
        sent_items_cursor_ts=lag_cursor,
        user_address="me@example.com",
    )

    # Reducer is responsible for reacting; builder just passes it through.
    assert snap.sent_items_cursor_ts == lag_cursor
    assert snap.latest_inbound_ts is not None
    assert snap.latest_inbound_ts.replace(tzinfo=timezone.utc) == t0
    # Cursor lag: stored naive inbound < aware cursor (compare naive)
    assert lag_cursor.replace(tzinfo=None) < snap.latest_inbound_ts
    assert snap.user_sent_last is False
