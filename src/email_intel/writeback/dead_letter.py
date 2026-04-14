"""Dead-letter handling for repeated writeback failures.

See project-plan §18 (error handling) and reducer-spec §8. When a conversation
accumulates too many writeback failures we flag it for review so the reducer's
E15 (WRITEBACK_FAILURE_THRESHOLD) can gate further automation.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from email_intel.db.models import (
    Conversation,
    ConversationEvent,
    ConversationEventType,
    EventActor,
    SyncEvent,
)

DEAD_LETTER_THRESHOLD = 5
WRITEBACK_FAILURE_EVENT_TYPE = "writeback_failure"


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


async def record_failure(
    session: AsyncSession,
    conversation_id: int,
    error: Exception,
    operation: str,
) -> int:
    """Record a writeback failure and return the 24h rolling failure count.

    Inserts a ``sync_events`` row with ``event_type='writeback_failure'``. The
    ``source_id`` carries the conversation id so recent-failure queries stay
    scoped. Returns the count of failures within the last 24 hours (including
    this one).
    """
    conv = await session.get(Conversation, conversation_id)
    if conv is None:
        raise ValueError(f"unknown conversation_id={conversation_id}")

    payload: dict[str, Any] = {
        "conversation_id": conversation_id,
        "operation": operation,
        "error_type": type(error).__name__,
        "error_message": str(error)[:500],
    }
    session.add(
        SyncEvent(
            user_id=conv.user_id,
            source_type="writeback",
            source_id=str(conversation_id),
            event_type=WRITEBACK_FAILURE_EVENT_TYPE,
            payload_json=payload,
            created_at=_utcnow(),
        )
    )
    await session.flush()
    return await count_recent_failures(session, conversation_id)


async def count_recent_failures(
    session: AsyncSession,
    conversation_id: int,
    window: timedelta = timedelta(hours=24),
) -> int:
    """Return number of writeback failures for this conversation within ``window``."""
    threshold = _utcnow() - window
    stmt = select(SyncEvent).where(
        SyncEvent.source_type == "writeback",
        SyncEvent.source_id == str(conversation_id),
        SyncEvent.event_type == WRITEBACK_FAILURE_EVENT_TYPE,
        SyncEvent.created_at >= threshold,
    )
    rows = (await session.execute(stmt)).scalars().all()
    return len(rows)


async def flag_for_review(
    session: AsyncSession,
    conversation_id: int,
    reason: str,
) -> None:
    """Set ``conversations.state_review_reason`` and append a ``needs_review_raised`` event."""
    conv = await session.get(Conversation, conversation_id)
    if conv is None:
        raise ValueError(f"unknown conversation_id={conversation_id}")

    conv.state_review_reason = reason
    conv.updated_at = _utcnow()

    session.add(
        ConversationEvent(
            user_id=conv.user_id,
            conversation_id=conversation_id,
            event_type=ConversationEventType.needs_review_raised,
            before_state=conv.open_action_state,
            after_state=conv.open_action_state,
            payload_json={"reason": reason, "source": "writeback_dead_letter"},
            actor=EventActor.system,
            occurred_at=_utcnow(),
        )
    )
    await session.flush()


__all__ = [
    "DEAD_LETTER_THRESHOLD",
    "WRITEBACK_FAILURE_EVENT_TYPE",
    "record_failure",
    "count_recent_failures",
    "flag_for_review",
]
