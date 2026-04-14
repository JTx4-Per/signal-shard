"""Conversation detail view tests."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

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
    TaskStatus,
    TodoTask,
    User,
    make_conversation,
    seed_event,
)


@pytest.mark.asyncio
async def test_detail_404_unknown(client: TestClient, user: User) -> None:
    resp = client.get("/review/conversations/999")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_detail_renders_all_sections(
    client: TestClient,
    session: AsyncSession,
    user: User,
    folder: MailFolder,
) -> None:
    conv = make_conversation(
        user.id,
        "Quarterly numbers please",
        state=ConversationState.act_open,
        bucket=ConversationBucket.Act,
        idx=1,
    )
    session.add(conv)
    await session.flush()

    msg = Message(
        user_id=user.id,
        graph_message_id="m-1",
        graph_conversation_id=conv.graph_conversation_id,
        folder_id=folder.id,
        subject="Quarterly numbers please",
    )
    session.add(msg)
    await session.flush()

    classification = Classification(
        conversation_id=conv.id,
        message_id=msg.id,
        primary_bucket=ConversationBucket.Act,
        confidence=0.92,
        reason_short="asked for numbers",
        rule_version="r1",
        model_version="gpt-4.1-nano",
        review_status=ReviewStatus.none,
    )
    session.add(classification)

    await seed_event(
        session,
        conv,
        event_type=ConversationEventType.state_changed,
        before=ConversationState.none,
        after=ConversationState.act_open,
    )

    task = TodoTask(
        user_id=user.id,
        conversation_id=conv.id,
        graph_todo_task_id="graph-task-1",
        graph_todo_list_id="list-1",
        title="Send quarterly numbers",
        status=TaskStatus.notStarted,
        due_at=datetime.now(timezone.utc) + timedelta(days=2),
    )
    session.add(task)

    await session.commit()

    resp = client.get(f"/review/conversations/{conv.id}")
    assert resp.status_code == 200
    body = resp.text
    assert "Quarterly numbers please" in body
    assert "act_open" in body
    assert "Send quarterly numbers" in body  # active task title
    assert "asked for numbers" in body  # classification reason
    assert "state_changed" in body  # event timeline
    assert "Override controls" in body
    assert "gpt-4.1-nano" in body
