"""End-to-end tests for writeback.apply.apply_reducer_result orchestrator."""

from __future__ import annotations

import asyncio
from datetime import datetime
from typing import Any

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from email_intel.config import get_settings
from email_intel.db.models import (
    ConversationBucket,
    ConversationEvent,
    ConversationEventType,
    ConversationState,
)
from email_intel.db.session import _conversation_locks
from email_intel.schemas.intents import (
    CategoryIntent,
    CategoryIntentKind,
    ReviewFlag,
    TaskIntent,
    TaskIntentKind,
)
from email_intel.schemas.reducer import ReducerEventRecord, ReducerResult
from email_intel.writeback.apply import apply_reducer_result

pytestmark = pytest.mark.asyncio


def _result_for_create(op_key: str = "op-create-T001") -> ReducerResult:
    return ReducerResult(
        next_state=ConversationState.respond_open,
        next_bucket=ConversationBucket.Respond,
        task_intent=TaskIntent(
            kind=TaskIntentKind.create,
            target_bucket=ConversationBucket.Respond,
            operation_key=op_key,
        ),
        category_intent=CategoryIntent(
            kind=CategoryIntentKind.apply,
            target_bucket=ConversationBucket.Respond,
            operation_key=f"cat-{op_key}",
        ),
        events=[ReducerEventRecord(event_type=ConversationEventType.state_changed, payload={"to": "respond_open"})],
        operation_keys=[op_key],
        review_flag=ReviewFlag.none,
        transition_id="T001",
    )


def _result_for_hard_complete() -> ReducerResult:
    return ReducerResult(
        next_state=ConversationState.done,
        next_bucket=None,
        task_intent=TaskIntent(kind=TaskIntentKind.hard_complete, operation_key="op-hc-T023"),
        category_intent=CategoryIntent(kind=CategoryIntentKind.clear, operation_key="cat-clear"),
        events=[ReducerEventRecord(event_type=ConversationEventType.state_changed, payload={"to": "done"})],
        review_flag=ReviewFlag.none,
        transition_id="T023",
    )


def _result_for_needs_review() -> ReducerResult:
    return ReducerResult(
        next_state=ConversationState.needs_review,
        next_bucket=None,
        task_intent=TaskIntent(kind=TaskIntentKind.noop),
        category_intent=CategoryIntent(kind=CategoryIntentKind.noop),
        events=[
            ReducerEventRecord(
                event_type=ConversationEventType.needs_review_raised,
                payload={"reason": "signal_conflict"},
            )
        ],
        review_flag=ReviewFlag.state,
        transition_id="T082",
    )


async def test_t001_create_end_to_end(
    session: AsyncSession, sample_world: dict[str, Any], fake_graph: Any, now: datetime, graph_placeholder: Any
) -> None:
    fake_graph.todo.create_task.return_value = {"id": "g-t001"}
    summary = await apply_reducer_result(
        session,
        graph_placeholder,
        sample_world["conversation"],
        sample_world["message"],
        _result_for_create(),
        task_lists=sample_world["task_lists"],
        now=now,
        settings=get_settings(),
        linked_web_url="https://outlook/m-1",
    )
    assert summary["task"]["action"] == "created"
    assert summary["category"]["action"] == "applied"
    assert summary["state_changed"] is True

    await session.refresh(sample_world["conversation"])
    assert sample_world["conversation"].open_action_state == ConversationState.respond_open
    assert sample_world["conversation"].open_action_task_id is not None


async def test_t023_hard_complete_clears_pointer(
    session: AsyncSession, sample_world: dict[str, Any], fake_graph: Any, now: datetime, graph_placeholder: Any
) -> None:
    # First create a task.
    fake_graph.todo.create_task.return_value = {"id": "g-seed"}
    await apply_reducer_result(
        session, graph_placeholder, sample_world["conversation"], sample_world["message"],
        _result_for_create("op-seed"),
        task_lists=sample_world["task_lists"], now=now, settings=get_settings(),
        linked_web_url="https://outlook/m-1",
    )
    assert sample_world["conversation"].open_action_task_id is not None

    summary = await apply_reducer_result(
        session, graph_placeholder, sample_world["conversation"], sample_world["message"],
        _result_for_hard_complete(),
        task_lists=sample_world["task_lists"], now=now, settings=get_settings(),
    )
    assert summary["task"]["action"] == "completed"
    assert summary["task"]["completion_kind"] == "hard"
    await session.refresh(sample_world["conversation"])
    assert sample_world["conversation"].open_action_task_id is None


async def test_t082_needs_review_blocks_writeback(
    session: AsyncSession, sample_world: dict[str, Any], fake_graph: Any, now: datetime, graph_placeholder: Any
) -> None:
    summary = await apply_reducer_result(
        session, graph_placeholder, sample_world["conversation"], sample_world["message"],
        _result_for_needs_review(),
        task_lists=sample_world["task_lists"], now=now, settings=get_settings(),
    )
    assert summary["needs_review"] is True
    # Task and category writes must not have hit Graph.
    assert fake_graph.todo.create_task.await_count == 0
    assert fake_graph.mail.patch_categories.await_count == 0

    await session.refresh(sample_world["conversation"])
    assert sample_world["conversation"].open_action_state == ConversationState.needs_review
    assert sample_world["conversation"].state_review_reason is not None

    # needs_review_raised event was appended.
    events = (await session.execute(select(ConversationEvent))).scalars().all()
    assert any(e.event_type == ConversationEventType.needs_review_raised for e in events)


async def test_lock_held_during_apply(
    session: AsyncSession, sample_world: dict[str, Any], fake_graph: Any, now: datetime, graph_placeholder: Any
) -> None:
    conv = sample_world["conversation"]
    graph_cid = conv.graph_conversation_id
    observed_locked: list[bool] = []

    async def _observing_create(*args: Any, **kwargs: Any) -> dict[str, Any]:
        lock = _conversation_locks.get(graph_cid)
        observed_locked.append(lock is not None and lock.locked())
        return {"id": "g-lockcheck"}

    fake_graph.todo.create_task.side_effect = _observing_create

    await apply_reducer_result(
        session, graph_placeholder, conv, sample_world["message"],
        _result_for_create("op-lock"),
        task_lists=sample_world["task_lists"], now=now, settings=get_settings(),
        linked_web_url="https://outlook/m-1",
    )
    assert observed_locked == [True]

    # After exit lock is released.
    lock = _conversation_locks.get(graph_cid)
    assert lock is not None
    assert lock.locked() is False
