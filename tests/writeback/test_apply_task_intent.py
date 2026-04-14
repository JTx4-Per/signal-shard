"""Unit tests for writeback.tasks.apply_task_intent."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from email_intel.db.models import (
    CompletionKind,
    ConversationBucket,
    ConversationEvent,
    ConversationEventType,
    TaskStatus,
    TodoTask,
)
from email_intel.schemas.intents import TaskIntent, TaskIntentKind
from email_intel.writeback import dead_letter
from email_intel.writeback.tasks import apply_task_intent

pytestmark = pytest.mark.asyncio


async def _invoke(
    session: AsyncSession,
    graph: Any,
    world: dict[str, Any],
    intent: TaskIntent,
    now: datetime,
    **kw: Any,
) -> dict[str, Any]:
    return await apply_task_intent(
        session,
        graph,
        world["conversation"],
        intent,
        world["task_lists"],
        title=kw.pop("title", "Reply to Alice"),
        body_markdown=kw.pop("body_markdown", "body"),
        due_at=kw.pop("due_at", None),
        linked_web_url=kw.pop("linked_web_url", "https://outlook/m-1"),
        now=now,
        soft_complete_window_days=7,
        **kw,
    )


async def test_noop(session: AsyncSession, sample_world: dict[str, Any], fake_graph: Any, now: datetime, graph_placeholder: Any) -> None:
    result = await _invoke(
        session, graph_placeholder, sample_world,
        TaskIntent(kind=TaskIntentKind.noop), now,
    )
    assert result["action"] == "noop"
    assert fake_graph.todo.create_task.await_count == 0


async def test_create_and_idempotent_repeat(
    session: AsyncSession, sample_world: dict[str, Any], fake_graph: Any, now: datetime, graph_placeholder: Any
) -> None:
    fake_graph.todo.create_task.return_value = {"id": "g-1"}
    intent = TaskIntent(
        kind=TaskIntentKind.create,
        target_bucket=ConversationBucket.Respond,
        operation_key="op-create-1",
    )
    first = await _invoke(session, graph_placeholder, sample_world, intent, now)
    assert first["action"] == "created"
    assert first["graph_task_id"] == "g-1"

    # I2 reverse link populated.
    conv = sample_world["conversation"]
    await session.refresh(conv)
    assert conv.open_action_task_id == first["task_id"]

    # I6: repeat with same operation_key is idempotent.
    second = await _invoke(session, graph_placeholder, sample_world, intent, now)
    assert second == first  # prior result returned verbatim
    assert fake_graph.todo.create_task.await_count == 1


async def test_update_fields_patches_only(
    session: AsyncSession, sample_world: dict[str, Any], fake_graph: Any, now: datetime, graph_placeholder: Any
) -> None:
    # First create a task.
    fake_graph.todo.create_task.return_value = {"id": "g-upd"}
    await _invoke(
        session, graph_placeholder, sample_world,
        TaskIntent(kind=TaskIntentKind.create, target_bucket=ConversationBucket.Respond, operation_key="k-c"),
        now,
    )

    fake_graph.todo.update_task.reset_mock()
    new_due = now + timedelta(days=2)
    res = await _invoke(
        session, graph_placeholder, sample_world,
        TaskIntent(
            kind=TaskIntentKind.update_fields,
            operation_key="k-u",
            fields={"title": "Reply to Alice (rev)", "due_at": new_due},
        ),
        now,
    )
    assert res["action"] == "updated"
    assert fake_graph.todo.update_task.await_count == 1
    args, kwargs = fake_graph.todo.update_task.call_args
    patch = args[3] if len(args) >= 4 else kwargs.get("payload")
    # update_task signature: (client, list_id, task_id, payload)
    assert patch["title"] == "Reply to Alice (rev)"
    assert "dueDateTime" in patch


async def test_soft_complete_sets_window(
    session: AsyncSession, sample_world: dict[str, Any], fake_graph: Any, now: datetime, graph_placeholder: Any
) -> None:
    fake_graph.todo.create_task.return_value = {"id": "g-sc"}
    await _invoke(
        session, graph_placeholder, sample_world,
        TaskIntent(kind=TaskIntentKind.create, target_bucket=ConversationBucket.Respond, operation_key="k-c"),
        now,
    )

    res = await _invoke(
        session, graph_placeholder, sample_world,
        TaskIntent(kind=TaskIntentKind.soft_complete, operation_key="k-sc"),
        now,
    )
    assert res["action"] == "completed"
    assert res["completion_kind"] == "soft"

    row = (await session.execute(select(TodoTask))).scalars().first()
    assert row is not None
    assert row.completion_kind == CompletionKind.soft
    assert row.soft_complete_until is not None
    delta = row.soft_complete_until.replace(tzinfo=timezone.utc) - now if row.soft_complete_until.tzinfo is None else row.soft_complete_until - now
    assert delta == timedelta(days=7)


async def test_hard_complete_clears_reverse_pointer(
    session: AsyncSession, sample_world: dict[str, Any], fake_graph: Any, now: datetime, graph_placeholder: Any
) -> None:
    fake_graph.todo.create_task.return_value = {"id": "g-hc"}
    await _invoke(
        session, graph_placeholder, sample_world,
        TaskIntent(kind=TaskIntentKind.create, target_bucket=ConversationBucket.Act, operation_key="k-c"),
        now,
    )
    conv = sample_world["conversation"]
    assert conv.open_action_task_id is not None

    await _invoke(
        session, graph_placeholder, sample_world,
        TaskIntent(kind=TaskIntentKind.hard_complete, operation_key="k-hc"),
        now,
    )
    await session.refresh(conv)
    assert conv.open_action_task_id is None  # I2


async def test_reopen_restores_notstarted(
    session: AsyncSession, sample_world: dict[str, Any], fake_graph: Any, now: datetime, graph_placeholder: Any
) -> None:
    fake_graph.todo.create_task.return_value = {"id": "g-ro"}
    await _invoke(
        session, graph_placeholder, sample_world,
        TaskIntent(kind=TaskIntentKind.create, target_bucket=ConversationBucket.Act, operation_key="k-c"),
        now,
    )
    await _invoke(
        session, graph_placeholder, sample_world,
        TaskIntent(kind=TaskIntentKind.hard_complete, operation_key="k-hc"),
        now,
    )

    res = await _invoke(
        session, graph_placeholder, sample_world,
        TaskIntent(kind=TaskIntentKind.reopen, operation_key="k-ro"),
        now,
    )
    assert res["action"] == "reopened"
    row = (await session.execute(select(TodoTask))).scalars().first()
    assert row is not None
    assert row.status == TaskStatus.notStarted
    assert row.completion_kind is None


async def test_move_list_creates_new_completes_old(
    session: AsyncSession, sample_world: dict[str, Any], fake_graph: Any, now: datetime, graph_placeholder: Any
) -> None:
    fake_graph.todo.create_task.side_effect = [{"id": "g-first"}, {"id": "g-second"}]
    await _invoke(
        session, graph_placeholder, sample_world,
        TaskIntent(kind=TaskIntentKind.create, target_bucket=ConversationBucket.Respond, operation_key="k-c"),
        now,
    )

    res = await _invoke(
        session, graph_placeholder, sample_world,
        TaskIntent(kind=TaskIntentKind.move_list, target_bucket=ConversationBucket.Act, operation_key="k-mv"),
        now,
    )
    assert res["action"] == "moved"
    assert res["graph_task_id"] == "g-second"

    rows = (await session.execute(select(TodoTask))).scalars().all()
    assert len(rows) == 2
    statuses = {r.graph_todo_task_id: r.status for r in rows}
    assert statuses["g-first"] == TaskStatus.completed
    assert statuses["g-second"] == TaskStatus.notStarted

    conv = sample_world["conversation"]
    await session.refresh(conv)
    assert conv.open_action_task_id == next(r.id for r in rows if r.graph_todo_task_id == "g-second")


async def test_graph_failure_recorded_by_dead_letter(
    session: AsyncSession, sample_world: dict[str, Any], fake_graph: Any, now: datetime, graph_placeholder: Any
) -> None:
    fake_graph.todo.create_task.side_effect = RuntimeError("graph 500")
    with pytest.raises(RuntimeError):
        await _invoke(
            session, graph_placeholder, sample_world,
            TaskIntent(kind=TaskIntentKind.create, target_bucket=ConversationBucket.Respond, operation_key="k-fail"),
            now,
        )
    # Caller records; emulate that here to ensure plumbing works.
    for _ in range(5):
        await dead_letter.record_failure(session, sample_world["conversation"].id, RuntimeError("x"), "task")
    count = await dead_letter.count_recent_failures(session, sample_world["conversation"].id)
    assert count >= 5


async def test_create_emits_task_created_event(
    session: AsyncSession, sample_world: dict[str, Any], fake_graph: Any, now: datetime, graph_placeholder: Any
) -> None:
    fake_graph.todo.create_task.return_value = {"id": "g-ev"}
    await _invoke(
        session, graph_placeholder, sample_world,
        TaskIntent(kind=TaskIntentKind.create, target_bucket=ConversationBucket.Respond, operation_key="k-c"),
        now,
    )
    events = (await session.execute(select(ConversationEvent))).scalars().all()
    assert any(e.event_type == ConversationEventType.task_created for e in events)
