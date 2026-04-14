"""Classification review resolution tests."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from tests.review.conftest import (
    Classification,
    ConversationBucket,
    ConversationEvent,
    ConversationEventType,
    MailFolder,
    Message,
    ReviewStatus,
    User,
    make_conversation,
)


async def _seed_classification(
    session: AsyncSession,
    user: User,
    folder: MailFolder,
) -> Classification:
    conv = make_conversation(user.id, "pending review", idx=1)
    session.add(conv)
    await session.flush()
    msg = Message(
        user_id=user.id,
        graph_message_id="m-x",
        graph_conversation_id=conv.graph_conversation_id,
        folder_id=folder.id,
        subject="pending",
    )
    session.add(msg)
    await session.flush()
    c = Classification(
        conversation_id=conv.id,
        message_id=msg.id,
        primary_bucket=ConversationBucket.FYI,
        confidence=0.55,
        classification_review_reason="ambiguous",
        review_status=ReviewStatus.pending,
    )
    session.add(c)
    await session.commit()
    return c


@pytest.mark.asyncio
async def test_accept_path(
    client: TestClient,
    session: AsyncSession,
    user: User,
    folder: MailFolder,
) -> None:
    c = await _seed_classification(session, user, folder)

    resp = client.post(
        f"/review/classifications/{c.id}/resolve",
        data={"decision": "accept"},
    )
    assert resp.status_code == 200
    assert "Accepted" in resp.text

    await session.refresh(c)
    assert c.review_status == ReviewStatus.resolved_accept

    events = list(
        (
            await session.execute(
                select(ConversationEvent).where(
                    ConversationEvent.event_type == ConversationEventType.override_applied
                )
            )
        )
        .scalars()
        .all()
    )
    assert events == []


@pytest.mark.asyncio
async def test_override_path(
    client: TestClient,
    session: AsyncSession,
    user: User,
    folder: MailFolder,
) -> None:
    c = await _seed_classification(session, user, folder)

    resp = client.post(
        f"/review/classifications/{c.id}/resolve",
        data={"decision": "override", "target_bucket": "Act"},
    )
    assert resp.status_code == 200
    assert "Overrode classification to Act" in resp.text

    await session.refresh(c)
    assert c.review_status == ReviewStatus.resolved_override

    events = list(
        (
            await session.execute(
                select(ConversationEvent).where(
                    ConversationEvent.event_type == ConversationEventType.override_applied
                )
            )
        )
        .scalars()
        .all()
    )
    assert len(events) == 1
    payload = events[0].payload_json
    assert isinstance(payload, dict)
    assert payload["scope"] == "classification"
    assert payload["target_bucket"] == "Act"
    assert payload["classification_id"] == c.id


@pytest.mark.asyncio
async def test_override_requires_bucket(
    client: TestClient,
    session: AsyncSession,
    user: User,
    folder: MailFolder,
) -> None:
    c = await _seed_classification(session, user, folder)
    resp = client.post(
        f"/review/classifications/{c.id}/resolve",
        data={"decision": "override"},
    )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_unknown_classification_404(client: TestClient, user: User) -> None:
    resp = client.post(
        "/review/classifications/9999/resolve",
        data={"decision": "accept"},
    )
    assert resp.status_code == 404
