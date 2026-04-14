"""Task writeback — consumes ``TaskIntent`` and applies via Microsoft To Do.

See project-plan §14 (To Do design) and reducer-spec §5 (task side-effect
matrix). Every outbound Graph call happens here; the reducer is pure.

Invariants touched:

* **I2** reverse mapping — on create/move/reopen we set
  ``conversations.open_action_task_id`` to the active task row; on hard
  complete we clear it.
* **I6** operation-key idempotency — ``intent.operation_key`` is checked via
  :func:`email_intel.writeback.apply.check_and_store_key`. A duplicate returns
  the prior result unchanged.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from email_intel.db.models import (
    CompletionKind,
    Conversation,
    ConversationBucket,
    ConversationEvent,
    ConversationEventType,
    EventActor,
    OperationType,
    TaskStatus,
    TodoList,
    TodoTask,
)
from email_intel.graph.client import GraphClient
from email_intel.graph import todo as graph_todo
from email_intel.schemas.intents import TaskIntent, TaskIntentKind

# Map reducer bucket → task-list key in the ``task_lists`` dict.
_BUCKET_TO_LIST_KEY: dict[ConversationBucket, str] = {
    ConversationBucket.Act: "Act",
    ConversationBucket.Respond: "Respond",
    ConversationBucket.WaitingOn: "WaitingOn",
    ConversationBucket.Delegate: "Delegate",
    ConversationBucket.Defer: "Deferred",
}


def _list_for_bucket(
    task_lists: dict[str, TodoList], bucket: ConversationBucket | None
) -> TodoList | None:
    if bucket is None:
        return None
    key = _BUCKET_TO_LIST_KEY.get(bucket)
    if key is None:
        return None
    return task_lists.get(key)


async def _active_task(
    session: AsyncSession, conversation_id: int
) -> TodoTask | None:
    stmt = select(TodoTask).where(
        TodoTask.conversation_id == conversation_id,
        TodoTask.status.in_([TaskStatus.notStarted, TaskStatus.inProgress]),
    )
    return (await session.execute(stmt)).scalars().first()


def _audit_payload(action: str, **kw: Any) -> dict[str, Any]:
    p: dict[str, Any] = {"action": action}
    p.update({k: v for k, v in kw.items() if v is not None})
    return p


def _emit_event(
    session: AsyncSession,
    conversation: Conversation,
    event_type: ConversationEventType,
    now: datetime,
    payload: dict[str, Any],
) -> None:
    session.add(
        ConversationEvent(
            user_id=conversation.user_id,
            conversation_id=conversation.id,
            event_type=event_type,
            before_state=conversation.open_action_state,
            after_state=conversation.open_action_state,
            payload_json=payload,
            actor=EventActor.reducer,
            occurred_at=now,
        )
    )


def _todo_payload(
    *, title: str, body_markdown: str, due_at: datetime | None
) -> dict[str, Any]:
    body = {
        "content": body_markdown or "",
        "contentType": "text",
    }
    payload: dict[str, Any] = {"title": title, "body": body}
    if due_at is not None:
        payload["dueDateTime"] = {
            "dateTime": due_at.isoformat(),
            "timeZone": "UTC",
        }
    return payload


async def apply_task_intent(
    session: AsyncSession,
    graph: GraphClient,
    conversation: Conversation,
    intent: TaskIntent,
    task_lists: dict[str, TodoList],
    *,
    title: str,
    body_markdown: str,
    due_at: datetime | None,
    linked_web_url: str | None,
    now: datetime,
    soft_complete_window_days: int,
) -> dict[str, Any]:
    """Dispatch on ``intent.kind`` and apply the required To Do effect."""
    # Local import to avoid circular import at module load time.
    from email_intel.writeback.apply import check_and_store_key, finalize_key_result

    kind = intent.kind

    if kind is TaskIntentKind.noop:
        return {"action": "noop", "task_id": None, "graph_task_id": None, "list_id": None}

    if kind is TaskIntentKind.dead_letter:
        session.add(
            ConversationEvent(
                user_id=conversation.user_id,
                conversation_id=conversation.id,
                event_type=ConversationEventType.needs_review_raised,
                before_state=conversation.open_action_state,
                after_state=conversation.open_action_state,
                payload_json={"reason": "task_intent.dead_letter"},
                actor=EventActor.system,
                occurred_at=now,
            )
        )
        return {
            "action": "dead_letter",
            "task_id": None,
            "graph_task_id": None,
            "list_id": None,
        }

    # Idempotency gate for kinds carrying an operation_key.
    op_key = intent.operation_key
    op_type = {
        TaskIntentKind.create: OperationType.task_create,
        TaskIntentKind.update_fields: OperationType.task_update,
        TaskIntentKind.move_list: OperationType.task_update,
        TaskIntentKind.soft_complete: OperationType.task_complete,
        TaskIntentKind.hard_complete: OperationType.task_complete,
        TaskIntentKind.reopen: OperationType.task_update,
        TaskIntentKind.suppress: OperationType.task_complete,
    }.get(kind, OperationType.task_update)

    is_first = True
    prior: dict[str, Any] | None = None
    if op_key:
        is_first, prior = await check_and_store_key(
            session,
            key=op_key,
            operation_type=op_type,
            conversation_id=conversation.id,
            payload_hash="",
        )
        if not is_first:
            return prior or {"action": "noop", "idempotent": True}

    try:
        if kind is TaskIntentKind.create:
            result = await _do_create(
                session,
                graph,
                conversation,
                intent,
                task_lists,
                title=title,
                body_markdown=body_markdown,
                due_at=due_at,
                linked_web_url=linked_web_url,
                now=now,
            )
        elif kind is TaskIntentKind.update_fields:
            result = await _do_update_fields(
                session,
                graph,
                conversation,
                intent,
                title=title,
                body_markdown=body_markdown,
                due_at=due_at,
                now=now,
            )
        elif kind is TaskIntentKind.move_list:
            result = await _do_move_list(
                session,
                graph,
                conversation,
                intent,
                task_lists,
                title=title,
                body_markdown=body_markdown,
                due_at=due_at,
                linked_web_url=linked_web_url,
                now=now,
            )
        elif kind is TaskIntentKind.soft_complete:
            result = await _do_complete(
                session,
                graph,
                conversation,
                completion_kind=CompletionKind.soft,
                now=now,
                soft_complete_window_days=soft_complete_window_days,
            )
        elif kind is TaskIntentKind.hard_complete:
            result = await _do_complete(
                session,
                graph,
                conversation,
                completion_kind=CompletionKind.hard,
                now=now,
                soft_complete_window_days=soft_complete_window_days,
            )
        elif kind is TaskIntentKind.reopen:
            result = await _do_reopen(session, graph, conversation, task_lists, now=now)
        elif kind is TaskIntentKind.suppress:
            result = await _do_suppress(
                session,
                graph,
                conversation,
                now=now,
                soft_complete_window_days=soft_complete_window_days,
            )
        else:  # pragma: no cover — exhaustively handled
            raise RuntimeError(f"unhandled TaskIntentKind: {kind!r}")
    except Exception:
        # Leave operation_key row in place with null result; caller records failure.
        raise

    if op_key:
        await finalize_key_result(session, op_key, result)
    return result


# --- individual handlers --------------------------------------------------


async def _do_create(
    session: AsyncSession,
    graph: GraphClient,
    conversation: Conversation,
    intent: TaskIntent,
    task_lists: dict[str, TodoList],
    *,
    title: str,
    body_markdown: str,
    due_at: datetime | None,
    linked_web_url: str | None,
    now: datetime,
) -> dict[str, Any]:
    lst = _list_for_bucket(task_lists, intent.target_bucket)
    if lst is None:
        raise ValueError(
            f"no todo list configured for bucket {intent.target_bucket!r}"
        )

    payload = _todo_payload(title=title, body_markdown=body_markdown, due_at=due_at)
    response = await graph_todo.create_task(graph, lst.graph_todo_list_id, payload)
    graph_task_id = str(response["id"])

    linked_external_id = conversation.graph_conversation_id
    if linked_web_url:
        await graph_todo.add_linked_resource(
            graph,
            lst.graph_todo_list_id,
            graph_task_id,
            external_id=linked_external_id,
            web_url=linked_web_url,
            app_name="EmailIntel",
            display_name=title,
        )

    task_row = TodoTask(
        user_id=conversation.user_id,
        conversation_id=conversation.id,
        action_slot="primary",
        graph_todo_task_id=graph_task_id,
        graph_todo_list_id=lst.graph_todo_list_id,
        title=title,
        status=TaskStatus.notStarted,
        completion_kind=None,
        soft_complete_until=None,
        due_at=due_at,
        body_markdown=body_markdown,
        linked_resource_external_id=linked_external_id,
        linked_resource_web_url=linked_web_url,
        last_synced_at=now,
    )
    session.add(task_row)
    await session.flush()

    # I2 reverse mapping.
    conversation.open_action_task_id = task_row.id
    conversation.updated_at = now

    _emit_event(
        session,
        conversation,
        ConversationEventType.task_created,
        now,
        _audit_payload(
            "create",
            graph_task_id=graph_task_id,
            list_id=lst.graph_todo_list_id,
            bucket=intent.target_bucket.value if intent.target_bucket else None,
        ),
    )

    return {
        "action": "created",
        "task_id": task_row.id,
        "graph_task_id": graph_task_id,
        "list_id": lst.graph_todo_list_id,
    }


async def _do_update_fields(
    session: AsyncSession,
    graph: GraphClient,
    conversation: Conversation,
    intent: TaskIntent,
    *,
    title: str,
    body_markdown: str,
    due_at: datetime | None,
    now: datetime,
) -> dict[str, Any]:
    task_row = await _active_task(session, conversation.id)
    if task_row is None:
        return {"action": "noop", "task_id": None, "graph_task_id": None, "list_id": None}

    patch: dict[str, Any] = {}
    fields = intent.fields or {}
    # Always apply title/body/due if provided (reducer passes them via fields).
    if "title" in fields:
        new_title = str(fields["title"])
        patch["title"] = new_title
        task_row.title = new_title
    if "body" in fields or "body_markdown" in fields:
        new_body = str(fields.get("body") or fields.get("body_markdown") or body_markdown)
        patch["body"] = {"content": new_body, "contentType": "text"}
        task_row.body_markdown = new_body
    if "due_at" in fields:
        new_due = fields["due_at"]
        if isinstance(new_due, datetime):
            patch["dueDateTime"] = {"dateTime": new_due.isoformat(), "timeZone": "UTC"}
            task_row.due_at = new_due
        elif new_due is None:
            patch["dueDateTime"] = None
            task_row.due_at = None

    if not patch:
        # Fallback: patch known fields from args even if `fields` dict empty.
        patch = _todo_payload(title=title, body_markdown=body_markdown, due_at=due_at)
        task_row.title = title
        task_row.body_markdown = body_markdown
        task_row.due_at = due_at

    await graph_todo.update_task(
        graph, task_row.graph_todo_list_id, task_row.graph_todo_task_id, patch
    )
    task_row.last_synced_at = now
    task_row.updated_at = now

    _emit_event(
        session,
        conversation,
        ConversationEventType.task_updated,
        now,
        _audit_payload(
            "update_fields",
            graph_task_id=task_row.graph_todo_task_id,
            fields=sorted(patch.keys()),
        ),
    )

    return {
        "action": "updated",
        "task_id": task_row.id,
        "graph_task_id": task_row.graph_todo_task_id,
        "list_id": task_row.graph_todo_list_id,
    }


async def _do_move_list(
    session: AsyncSession,
    graph: GraphClient,
    conversation: Conversation,
    intent: TaskIntent,
    task_lists: dict[str, TodoList],
    *,
    title: str,
    body_markdown: str,
    due_at: datetime | None,
    linked_web_url: str | None,
    now: datetime,
) -> dict[str, Any]:
    target_list = _list_for_bucket(task_lists, intent.target_bucket)
    if target_list is None:
        raise ValueError(
            f"no todo list configured for bucket {intent.target_bucket!r}"
        )

    task_row = await _active_task(session, conversation.id)
    if task_row is not None and task_row.graph_todo_list_id == target_list.graph_todo_list_id:
        return {
            "action": "noop",
            "task_id": task_row.id,
            "graph_task_id": task_row.graph_todo_task_id,
            "list_id": task_row.graph_todo_list_id,
        }

    # Graph To Do does NOT support reassigning listId — we recreate + close.
    create_payload = _todo_payload(title=title, body_markdown=body_markdown, due_at=due_at)
    response = await graph_todo.create_task(
        graph, target_list.graph_todo_list_id, create_payload
    )
    new_graph_id = str(response["id"])

    linked_external_id = conversation.graph_conversation_id
    if linked_web_url:
        await graph_todo.add_linked_resource(
            graph,
            target_list.graph_todo_list_id,
            new_graph_id,
            external_id=linked_external_id,
            web_url=linked_web_url,
            app_name="EmailIntel",
            display_name=title,
        )

    if task_row is not None:
        move_note = (
            (task_row.body_markdown or "")
            + f"\n\n[moved to {intent.target_bucket.value if intent.target_bucket else '?'}]"
        )
        await graph_todo.update_task(
            graph,
            task_row.graph_todo_list_id,
            task_row.graph_todo_task_id,
            {
                "status": "completed",
                "body": {"content": move_note, "contentType": "text"},
            },
        )
        task_row.status = TaskStatus.completed
        task_row.completion_kind = CompletionKind.hard
        task_row.body_markdown = move_note
        task_row.last_synced_at = now
        task_row.updated_at = now

    new_row = TodoTask(
        user_id=conversation.user_id,
        conversation_id=conversation.id,
        action_slot="primary",
        graph_todo_task_id=new_graph_id,
        graph_todo_list_id=target_list.graph_todo_list_id,
        title=title,
        status=TaskStatus.notStarted,
        completion_kind=None,
        due_at=due_at,
        body_markdown=body_markdown,
        linked_resource_external_id=linked_external_id,
        linked_resource_web_url=linked_web_url,
        last_synced_at=now,
    )
    session.add(new_row)
    await session.flush()

    conversation.open_action_task_id = new_row.id
    conversation.updated_at = now

    _emit_event(
        session,
        conversation,
        ConversationEventType.task_updated,
        now,
        _audit_payload(
            "move_list",
            from_list=task_row.graph_todo_list_id if task_row else None,
            to_list=target_list.graph_todo_list_id,
            new_graph_task_id=new_graph_id,
        ),
    )

    return {
        "action": "moved",
        "task_id": new_row.id,
        "graph_task_id": new_graph_id,
        "list_id": target_list.graph_todo_list_id,
    }


async def _do_complete(
    session: AsyncSession,
    graph: GraphClient,
    conversation: Conversation,
    *,
    completion_kind: CompletionKind,
    now: datetime,
    soft_complete_window_days: int,
) -> dict[str, Any]:
    task_row = await _active_task(session, conversation.id)
    if task_row is None:
        return {"action": "noop", "task_id": None, "graph_task_id": None, "list_id": None}

    await graph_todo.complete_task(
        graph, task_row.graph_todo_list_id, task_row.graph_todo_task_id
    )
    task_row.status = TaskStatus.completed
    task_row.completion_kind = completion_kind
    if completion_kind is CompletionKind.soft:
        task_row.soft_complete_until = now + timedelta(days=soft_complete_window_days)
        event_type = ConversationEventType.task_soft_complete
        action = "completed"
    else:
        task_row.soft_complete_until = None
        event_type = ConversationEventType.task_hard_complete
        action = "completed"
        # I2: clear reverse pointer when the task is truly closed.
        conversation.open_action_task_id = None
    task_row.last_synced_at = now
    task_row.updated_at = now
    conversation.updated_at = now

    _emit_event(
        session,
        conversation,
        event_type,
        now,
        _audit_payload(
            action,
            completion_kind=completion_kind.value,
            graph_task_id=task_row.graph_todo_task_id,
        ),
    )

    return {
        "action": action,
        "completion_kind": completion_kind.value,
        "task_id": task_row.id,
        "graph_task_id": task_row.graph_todo_task_id,
        "list_id": task_row.graph_todo_list_id,
    }


async def _do_reopen(
    session: AsyncSession,
    graph: GraphClient,
    conversation: Conversation,
    task_lists: dict[str, TodoList],
    *,
    now: datetime,
) -> dict[str, Any]:
    # Find the most recent completed row for this conversation.
    stmt = (
        select(TodoTask)
        .where(TodoTask.conversation_id == conversation.id)
        .order_by(TodoTask.updated_at.desc())
    )
    task_row = (await session.execute(stmt)).scalars().first()
    if task_row is None:
        return {"action": "noop", "task_id": None, "graph_task_id": None, "list_id": None}

    await graph_todo.reopen_task(
        graph, task_row.graph_todo_list_id, task_row.graph_todo_task_id
    )
    task_row.status = TaskStatus.notStarted
    task_row.completion_kind = None
    task_row.soft_complete_until = None
    task_row.last_synced_at = now
    task_row.updated_at = now

    conversation.open_action_task_id = task_row.id
    conversation.updated_at = now

    _emit_event(
        session,
        conversation,
        ConversationEventType.task_reopened,
        now,
        _audit_payload("reopen", graph_task_id=task_row.graph_todo_task_id),
    )

    return {
        "action": "reopened",
        "task_id": task_row.id,
        "graph_task_id": task_row.graph_todo_task_id,
        "list_id": task_row.graph_todo_list_id,
    }


async def _do_suppress(
    session: AsyncSession,
    graph: GraphClient,
    conversation: Conversation,
    *,
    now: datetime,
    soft_complete_window_days: int,
) -> dict[str, Any]:
    task_row = await _active_task(session, conversation.id)
    if task_row is None:
        return {"action": "noop", "task_id": None, "graph_task_id": None, "list_id": None}

    note = (task_row.body_markdown or "") + "\n\n[suppressed_by_reducer]"
    await graph_todo.update_task(
        graph,
        task_row.graph_todo_list_id,
        task_row.graph_todo_task_id,
        {"status": "completed", "body": {"content": note, "contentType": "text"}},
    )
    task_row.status = TaskStatus.completed
    task_row.completion_kind = CompletionKind.soft
    task_row.soft_complete_until = now + timedelta(days=soft_complete_window_days)
    task_row.body_markdown = note
    task_row.last_synced_at = now
    task_row.updated_at = now
    conversation.updated_at = now

    _emit_event(
        session,
        conversation,
        ConversationEventType.task_soft_complete,
        now,
        _audit_payload(
            "suppress",
            graph_task_id=task_row.graph_todo_task_id,
            reason="suppressed_by_reducer",
        ),
    )

    return {
        "action": "suppressed",
        "task_id": task_row.id,
        "graph_task_id": task_row.graph_todo_task_id,
        "list_id": task_row.graph_todo_list_id,
    }


__all__ = ["apply_task_intent"]
