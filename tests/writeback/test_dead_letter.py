"""Dead-letter threshold + flag_for_review tests."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from email_intel.db.models import ConversationEvent, ConversationEventType, SyncEvent
from email_intel.writeback import dead_letter

pytestmark = pytest.mark.asyncio


async def test_record_failure_counts_and_flag(
    session: AsyncSession, sample_world: dict[str, Any]
) -> None:
    cid = sample_world["conversation"].id
    for i in range(dead_letter.DEAD_LETTER_THRESHOLD):
        count = await dead_letter.record_failure(
            session, cid, RuntimeError(f"err-{i}"), operation="task"
        )
    assert count == dead_letter.DEAD_LETTER_THRESHOLD

    rows = (await session.execute(select(SyncEvent))).scalars().all()
    assert len(rows) == dead_letter.DEAD_LETTER_THRESHOLD
    assert all(r.event_type == dead_letter.WRITEBACK_FAILURE_EVENT_TYPE for r in rows)

    await dead_letter.flag_for_review(session, cid, reason="writeback_failure_threshold")
    await session.refresh(sample_world["conversation"])
    assert sample_world["conversation"].state_review_reason == "writeback_failure_threshold"

    events = (await session.execute(select(ConversationEvent))).scalars().all()
    assert any(e.event_type == ConversationEventType.needs_review_raised for e in events)


async def test_old_failures_excluded_from_window(
    session: AsyncSession, sample_world: dict[str, Any]
) -> None:
    cid = sample_world["conversation"].id
    old = datetime.now(timezone.utc) - timedelta(hours=48)
    session.add(
        SyncEvent(
            user_id=sample_world["user"].id,
            source_type="writeback",
            source_id=str(cid),
            event_type=dead_letter.WRITEBACK_FAILURE_EVENT_TYPE,
            payload_json={"operation": "task"},
            created_at=old,
        )
    )
    await session.flush()
    count = await dead_letter.count_recent_failures(session, cid)
    assert count == 0
