"""F1 · multi-action schema hook — verifies unique-index lets ('primary','secondary') coexist."""

from __future__ import annotations

import pytest

from email_intel.db.models import (
    Conversation,
    ConversationState,
    TaskStatus,
    TodoTask,
    User,
)


@pytest.mark.asyncio
async def test_F1_two_active_tasks_with_different_slots(session) -> None:
    u = User(graph_user_id="u1", email="u@x")
    session.add(u)
    await session.flush()
    c = Conversation(user_id=u.id, graph_conversation_id="g1",
                     open_action_state=ConversationState.act_open)
    session.add(c)
    await session.flush()

    t1 = TodoTask(
        user_id=u.id, conversation_id=c.id, action_slot="primary",
        graph_todo_task_id="gt1", graph_todo_list_id="gl1",
        title="primary task", status=TaskStatus.notStarted,
    )
    t2 = TodoTask(
        user_id=u.id, conversation_id=c.id, action_slot="secondary",
        graph_todo_task_id="gt2", graph_todo_list_id="gl1",
        title="secondary task", status=TaskStatus.notStarted,
    )
    session.add_all([t1, t2])
    await session.commit()  # must not violate unique index

    # Adding another 'primary' while active → must violate.
    t3 = TodoTask(
        user_id=u.id, conversation_id=c.id, action_slot="primary",
        graph_todo_task_id="gt3", graph_todo_list_id="gl1",
        title="duplicate primary", status=TaskStatus.notStarted,
    )
    session.add(t3)
    with pytest.raises(Exception):
        await session.commit()
