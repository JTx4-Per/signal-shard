"""Override and clear-review tests."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from tests.review.conftest import (
    Conversation,
    ConversationBucket,
    ConversationEvent,
    ConversationEventType,
    ConversationState,
    EventActor,
    User,
    make_conversation,
)


@pytest.mark.asyncio
async def test_post_override_clears_reason_and_inserts_event(
    client: TestClient,
    session: AsyncSession,
    user: User,
) -> None:
    conv = make_conversation(
        user.id,
        "Weird conversation",
        state=ConversationState.needs_review,
        state_review_reason="reducer unsure",
        idx=1,
    )
    session.add(conv)
    await session.commit()

    resp = client.post(
        f"/review/conversations/{conv.id}/override",
        data={
            "target_state": "act_open",
            "target_bucket": "Act",
            "note": "I know what I want",
        },
    )
    assert resp.status_code == 200
    assert "Override recorded." in resp.text

    await session.refresh(conv)
    assert conv.state_review_reason is None

    events = list(
        (
            await session.execute(
                select(ConversationEvent).where(ConversationEvent.conversation_id == conv.id)
            )
        )
        .scalars()
        .all()
    )
    assert len(events) == 1
    e = events[0]
    assert e.event_type == ConversationEventType.override_applied
    assert e.actor == EventActor.user_override
    payload = e.payload_json
    assert isinstance(payload, dict)
    assert payload["target_state"] == "act_open"
    assert payload["target_bucket"] == "Act"
    assert payload["note"] == "I know what I want"


@pytest.mark.asyncio
async def test_post_clear_review_clears_reason_and_logs_event(
    client: TestClient,
    session: AsyncSession,
    user: User,
) -> None:
    conv = make_conversation(
        user.id,
        "Another one",
        state=ConversationState.needs_review,
        state_review_reason="classifier disagreed",
        idx=2,
    )
    session.add(conv)
    await session.commit()

    resp = client.post(f"/review/conversations/{conv.id}/clear-review")
    assert resp.status_code == 200
    assert "cleared" in resp.text.lower()

    await session.refresh(conv)
    assert conv.state_review_reason is None

    events = list(
        (
            await session.execute(
                select(ConversationEvent).where(ConversationEvent.conversation_id == conv.id)
            )
        )
        .scalars()
        .all()
    )
    assert len(events) == 1
    assert events[0].event_type == ConversationEventType.needs_review_resolved
    assert events[0].actor == EventActor.user_override


@pytest.mark.asyncio
async def test_override_unknown_conversation_returns_404(
    client: TestClient,
    user: User,
) -> None:
    resp = client.post(
        "/review/conversations/9999/override",
        data={"target_state": "act_open", "note": ""},
    )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_override_rejects_invalid_state(
    client: TestClient,
    session: AsyncSession,
    user: User,
) -> None:
    conv = make_conversation(user.id, "bad", idx=3)
    session.add(conv)
    await session.commit()

    resp = client.post(
        f"/review/conversations/{conv.id}/override",
        data={"target_state": "not_a_state", "note": ""},
    )
    assert resp.status_code == 422
