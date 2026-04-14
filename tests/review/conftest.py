"""Fixtures for review console tests.

Builds a minimal FastAPI app that mounts only the review router against a
fresh in-memory SQLite DB. The `get_session` dependency is overridden to use
a shared session per-test so state inserted via the fixture is visible to the
request handlers.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterator
from datetime import datetime, timedelta, timezone

import pytest
import pytest_asyncio
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from email_intel.db.base import Base, create_async_engine_for
from email_intel.db.models import (
    Classification,
    Conversation,
    ConversationBucket,
    ConversationEvent,
    ConversationEventType,
    ConversationState,
    EventActor,
    MailFolder,
    Message,
    ReviewStatus,
    TaskStatus,
    TodoTask,
    User,
)
from email_intel.db.session import make_session_factory
from email_intel.review.routes import get_session, router


@pytest_asyncio.fixture
async def engine() -> AsyncIterator[AsyncEngine]:
    eng = create_async_engine_for("sqlite+aiosqlite:///:memory:")
    async with eng.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    try:
        yield eng
    finally:
        async with eng.begin() as conn:
            await conn.run_sync(Base.metadata.drop_all)
        await eng.dispose()


@pytest_asyncio.fixture
async def session(engine: AsyncEngine) -> AsyncIterator[AsyncSession]:
    factory = make_session_factory(engine)
    async with factory() as sess:
        yield sess


@pytest.fixture
def app(engine: AsyncEngine, session: AsyncSession) -> Iterator[FastAPI]:
    application = FastAPI()
    application.state.session_factory = make_session_factory(engine)
    application.include_router(router)

    async def _override_session() -> AsyncSession:
        return session

    application.dependency_overrides[get_session] = _override_session
    yield application
    application.dependency_overrides.clear()


@pytest.fixture
def client(app: FastAPI) -> Iterator[TestClient]:
    with TestClient(app) as c:
        yield c


# ---------- Seed helpers ----------


@pytest_asyncio.fixture
async def user(session: AsyncSession) -> User:
    u = User(graph_user_id="graph-user-1", email="me@example.com", display_name="Me")
    session.add(u)
    await session.commit()
    await session.refresh(u)
    return u


@pytest_asyncio.fixture
async def folder(session: AsyncSession, user: User) -> MailFolder:
    f = MailFolder(
        user_id=user.id,
        graph_folder_id="folder-inbox",
        well_known_name="inbox",
        display_name="Inbox",
    )
    session.add(f)
    await session.commit()
    await session.refresh(f)
    return f


def make_conversation(
    user_id: int,
    subject: str,
    state: ConversationState = ConversationState.none,
    bucket: ConversationBucket | None = None,
    state_review_reason: str | None = None,
    last_sender: str | None = "alice@example.com",
    idx: int = 0,
) -> Conversation:
    return Conversation(
        user_id=user_id,
        graph_conversation_id=f"conv-{idx}",
        canonical_subject=subject,
        last_sender_address=last_sender,
        latest_received_at=datetime.now(timezone.utc) - timedelta(minutes=idx),
        open_action_state=state,
        open_action_bucket=bucket,
        state_review_reason=state_review_reason,
    )


async def seed_event(
    session: AsyncSession,
    conv: Conversation,
    event_type: ConversationEventType = ConversationEventType.message_added,
    before: ConversationState | None = None,
    after: ConversationState | None = None,
    occurred_at: datetime | None = None,
    actor: EventActor = EventActor.system,
) -> ConversationEvent:
    ev = ConversationEvent(
        user_id=conv.user_id,
        conversation_id=conv.id,
        event_type=event_type,
        before_state=before,
        after_state=after,
        occurred_at=occurred_at or datetime.now(timezone.utc),
        actor=actor,
    )
    session.add(ev)
    await session.flush()
    return ev


__all__ = [
    "Classification",
    "Conversation",
    "ConversationBucket",
    "ConversationEvent",
    "ConversationEventType",
    "ConversationState",
    "EventActor",
    "MailFolder",
    "Message",
    "ReviewStatus",
    "TaskStatus",
    "TodoTask",
    "User",
    "make_conversation",
    "seed_event",
]
