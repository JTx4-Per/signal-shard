"""Dashboard rendering tests."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession

from tests.review.conftest import (
    Classification,
    ConversationBucket,
    ConversationEventType,
    ConversationState,
    MailFolder,
    Message,
    ReviewStatus,
    User,
    make_conversation,
    seed_event,
)


@pytest.mark.asyncio
async def test_dashboard_empty(client: TestClient, user: User) -> None:
    resp = client.get("/review/")
    assert resp.status_code == 200
    body = resp.text
    assert "State-level review queue" in body
    assert "Classification-level review queue" in body
    assert "Recent activity" in body
    assert "No conversations are blocked on state review." in body
    assert "No classifications awaiting review." in body
    assert "No activity yet." in body


@pytest.mark.asyncio
async def test_dashboard_populated(
    client: TestClient,
    session: AsyncSession,
    user: User,
    folder: MailFolder,
) -> None:
    # 2 state-review conversations
    c1 = make_conversation(
        user.id,
        "Needs review A",
        state=ConversationState.needs_review,
        state_review_reason="reducer says ambiguous",
        idx=1,
    )
    c2 = make_conversation(
        user.id,
        "Needs review B",
        state=ConversationState.needs_review,
        state_review_reason="another reason",
        idx=2,
    )
    # 1 normal conversation for classification-review rows to attach to
    c3 = make_conversation(user.id, "Normal", idx=3)
    session.add_all([c1, c2, c3])
    await session.flush()

    # Seed a message for each classification (FK requirement)
    def _mk_msg(gid: str, conv_id: str) -> Message:
        return Message(
            user_id=user.id,
            graph_message_id=gid,
            graph_conversation_id=conv_id,
            folder_id=folder.id,
            subject="x",
        )

    msgs = [_mk_msg(f"m-{i}", c3.graph_conversation_id) for i in range(3)]
    session.add_all(msgs)
    await session.flush()

    for i, m in enumerate(msgs):
        session.add(
            Classification(
                conversation_id=c3.id,
                message_id=m.id,
                primary_bucket=ConversationBucket.Act,
                confidence=0.4 + i * 0.1,
                classification_review_reason=f"low confidence {i}",
                review_status=ReviewStatus.pending,
                reason_short="short",
            )
        )
    await session.flush()

    # 10 events
    for i in range(10):
        await seed_event(
            session,
            c3,
            event_type=ConversationEventType.classified,
            before=ConversationState.none,
            after=ConversationState.act_open,
            occurred_at=datetime.now(timezone.utc),
        )
    await session.commit()

    resp = client.get("/review/")
    assert resp.status_code == 200
    body = resp.text
    assert "Needs review A" in body
    assert "Needs review B" in body
    assert "reducer says ambiguous" in body
    assert "low confidence 0" in body
    # count badges should reflect seeded rows
    assert "2 blocking writeback" in body
    assert "3 pending" in body
    assert "classified" in body
