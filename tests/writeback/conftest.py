"""Shared fixtures for writeback tests — fake GraphClient, DB factories."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timezone
from typing import Any
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession

from email_intel.db.models import (
    Conversation,
    ConversationState,
    MailFolder,
    Message,
    TodoList,
    User,
)


class FakeTodo:
    """Async-mock facade matching the surface of ``email_intel.graph.todo``."""

    def __init__(self) -> None:
        self.create_task = AsyncMock(return_value={"id": "graph-task-new", "status": "notStarted"})
        self.update_task = AsyncMock(return_value={"id": "graph-task-existing"})
        self.complete_task = AsyncMock(return_value={"id": "graph-task-existing", "status": "completed"})
        self.reopen_task = AsyncMock(return_value={"id": "graph-task-existing", "status": "notStarted"})
        self.add_linked_resource = AsyncMock(return_value={"id": "linked-1"})


class FakeMail:
    """Async-mock facade for ``email_intel.graph.mail``."""

    def __init__(self) -> None:
        self.patch_categories = AsyncMock(return_value={"id": "m-1", "categories": []})
        self.get_message = AsyncMock(return_value={"id": "m-1", "categories": [], "@odata.etag": "W/\"new\""})


class FakeGraphClient:
    """Minimal stand-in for GraphClient — only exposes ``todo``/``mail`` mocks."""

    def __init__(self) -> None:
        self.todo = FakeTodo()
        self.mail = FakeMail()


@pytest.fixture
def fake_graph(monkeypatch: pytest.MonkeyPatch) -> FakeGraphClient:
    client = FakeGraphClient()

    # Patch the module-level graph_todo functions used by writeback.tasks.
    from email_intel.writeback import tasks as tasks_mod

    monkeypatch.setattr(tasks_mod.graph_todo, "create_task", client.todo.create_task)
    monkeypatch.setattr(tasks_mod.graph_todo, "update_task", client.todo.update_task)
    monkeypatch.setattr(tasks_mod.graph_todo, "complete_task", client.todo.complete_task)
    monkeypatch.setattr(tasks_mod.graph_todo, "reopen_task", client.todo.reopen_task)
    monkeypatch.setattr(
        tasks_mod.graph_todo, "add_linked_resource", client.todo.add_linked_resource
    )

    from email_intel.writeback import categories as cat_mod

    monkeypatch.setattr(cat_mod.graph_mail, "patch_categories", client.mail.patch_categories)
    monkeypatch.setattr(cat_mod.graph_mail, "get_message", client.mail.get_message)

    return client


@pytest_asyncio.fixture
async def sample_world(session: AsyncSession) -> dict[str, Any]:
    """Seed a single user + folder + conversation + latest message + task lists."""
    user = User(graph_user_id="u-graph", email="me@example.com", display_name="Me")
    session.add(user)
    await session.flush()

    folder = MailFolder(
        user_id=user.id,
        graph_folder_id="f-inbox",
        well_known_name="Inbox",
        display_name="Inbox",
    )
    session.add(folder)
    await session.flush()

    message = Message(
        user_id=user.id,
        graph_message_id="m-graph-1",
        graph_conversation_id="conv-graph-1",
        folder_id=folder.id,
        subject="Hello",
        from_address="alice@example.com",
        received_at=datetime(2026, 4, 10, 9, 0, tzinfo=timezone.utc),
        categories_json=["Work"],
        etag='W/"original"',
    )
    session.add(message)
    await session.flush()

    conv = Conversation(
        user_id=user.id,
        graph_conversation_id="conv-graph-1",
        canonical_subject="Hello",
        latest_message_id=message.id,
        open_action_state=ConversationState.none,
    )
    session.add(conv)
    await session.flush()

    lists = {
        key: TodoList(
            user_id=user.id,
            graph_todo_list_id=f"list-{key.lower()}",
            display_name=key,
            purpose=key,
        )
        for key in ("Act", "Respond", "WaitingOn", "Delegate", "Deferred")
    }
    for lst in lists.values():
        session.add(lst)
    await session.flush()

    return {
        "user": user,
        "folder": folder,
        "message": message,
        "conversation": conv,
        "task_lists": lists,
    }


@pytest.fixture
def now() -> datetime:
    return datetime(2026, 4, 13, 12, 0, tzinfo=timezone.utc)


@pytest.fixture
def graph_placeholder() -> Any:
    """Anywhere the real GraphClient type is demanded — we pass a plain object.

    Writeback code only uses the `graph` argument to hand to patched module-level
    functions; nothing reads attributes off it in tests.
    """
    return object()


# Re-export a convenience builder for ad-hoc conversations in tests.
@pytest.fixture
def make_message() -> Callable[..., Message]:
    def _factory(**overrides: Any) -> Message:
        base: dict[str, Any] = {
            "user_id": 1,
            "graph_message_id": "m-x",
            "graph_conversation_id": "c-x",
            "folder_id": 1,
        }
        base.update(overrides)
        return Message(**base)

    return _factory
