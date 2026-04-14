"""Top-level writeback orchestrator.

Consumes a ``ReducerResult`` for a single conversation and applies, in order:

1. conversation state / bucket / review reason update
2. append conversation_events
3. task intent (via ``writeback.tasks.apply_task_intent``)
4. category intent (via ``writeback.categories.apply_category_intent``)

All work runs under the per-conversation lock from ``db.session`` so that the
SQLite single-writer rule (project-plan §7) holds. Invariants enforced:

- **I2** reverse mapping: ``conversations.open_action_task_id`` is kept in sync
  by the task layer (we never write it here — it's their job).
- **I6** operation-key idempotency: enforced via ``check_and_store_key`` /
  ``finalize_key_result``.
- **I7** needs_review guard: if the reducer says ``NEEDS_REVIEW`` we skip both
  task and category writeback even if the intents are non-noop.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from typing import Any

from sqlalchemy import insert, select
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import AsyncSession

from email_intel.config import Settings
from email_intel.db.models import (
    Conversation,
    ConversationEvent,
    ConversationState,
    EventActor,
    Message,
    OperationKey,
    OperationType,
    TodoList,
)
from email_intel.db.session import acquire_conversation_lock
from email_intel.graph.client import GraphClient
from email_intel.schemas.reducer import ReducerResult
from email_intel.writeback import dead_letter
from email_intel.writeback.categories import apply_category_intent
from email_intel.writeback.tasks import apply_task_intent


# --- operation-key helpers -------------------------------------------------


def _hash_payload(payload: dict[str, Any]) -> str:
    blob = json.dumps(payload, sort_keys=True, default=str).encode("utf-8")
    return hashlib.sha256(blob).hexdigest()


async def check_and_store_key(
    session: AsyncSession,
    key: str,
    operation_type: OperationType,
    conversation_id: int,
    payload_hash: str,
) -> tuple[bool, dict[str, Any] | None]:
    """SQLite-safe idempotency gate on ``operation_keys``.

    Returns ``(is_first_time, prior_result_json_or_none)``. On a duplicate key
    the caller must no-op and return ``prior_result_json`` verbatim (I6). On
    the first insert the row starts with ``result_json=None`` — the caller does
    the side effect then calls :func:`finalize_key_result`.
    """
    if not key:
        return True, None

    stmt = sqlite_insert(OperationKey).values(
        key=key,
        operation_type=operation_type,
        conversation_id=conversation_id,
        payload_hash=payload_hash,
        result_json=None,
    )
    stmt = stmt.on_conflict_do_nothing(index_elements=["key"])
    existing_before = await session.get(OperationKey, key)
    if existing_before is not None:
        prior_any: Any = existing_before.result_json
        prior_dict = prior_any if isinstance(prior_any, dict) else None
        return False, prior_dict

    await session.execute(stmt)
    await session.flush()
    return True, None


async def finalize_key_result(
    session: AsyncSession, key: str, result_json: dict[str, Any]
) -> None:
    """Record the observed side-effect summary on an existing operation key row."""
    if not key:
        return
    row = await session.get(OperationKey, key)
    if row is None:
        return
    row.result_json = result_json
    await session.flush()


# --- orchestrator ---------------------------------------------------------


def _utcnow_like(now: datetime) -> datetime:
    return now


async def apply_reducer_result(
    session: AsyncSession,
    graph: GraphClient,
    conversation: Conversation,
    latest_message: Message,
    result: ReducerResult,
    *,
    task_lists: dict[str, TodoList],
    now: datetime,
    settings: Settings,
    title: str | None = None,
    body_markdown: str | None = None,
    due_at: datetime | None = None,
    linked_web_url: str | None = None,
) -> dict[str, Any]:
    """Apply a reducer result under the per-conversation lock.

    Returns a summary dict containing the task + category sub-results, a list
    of appended event names, and ``needs_review`` if we short-circuited (I7).
    """
    summary: dict[str, Any] = {
        "task": {"action": "noop"},
        "category": {"action": "noop"},
        "events": [],
        "needs_review": False,
        "state_changed": False,
    }

    async with acquire_conversation_lock(conversation.graph_conversation_id):
        # 1. state / bucket update ------------------------------------------
        before_state = conversation.open_action_state
        if (
            conversation.open_action_state != result.next_state
            or conversation.open_action_bucket != result.next_bucket
        ):
            conversation.open_action_state = result.next_state
            conversation.open_action_bucket = result.next_bucket
            summary["state_changed"] = True

        conversation.last_reducer_run_at = now
        conversation.updated_at = now

        # 2. append events ---------------------------------------------------
        for ev in result.events:
            session.add(
                ConversationEvent(
                    user_id=conversation.user_id,
                    conversation_id=conversation.id,
                    event_type=ev.event_type,
                    before_state=before_state,
                    after_state=result.next_state,
                    payload_json=dict(ev.payload),
                    actor=EventActor.reducer,
                    occurred_at=now,
                )
            )
            summary["events"].append(ev.event_type.value)

        await session.flush()

        # 3. I7 guard --------------------------------------------------------
        if result.next_state == ConversationState.needs_review:
            summary["needs_review"] = True
            if conversation.state_review_reason is None:
                conversation.state_review_reason = (
                    f"reducer:{result.transition_id}:review_flag={result.review_flag.value}"
                )
            await session.flush()
            return summary

        # 4. tasks -----------------------------------------------------------
        try:
            task_result = await apply_task_intent(
                session,
                graph,
                conversation,
                result.task_intent,
                task_lists,
                title=title or (conversation.canonical_subject or "(no subject)"),
                body_markdown=body_markdown or "",
                due_at=due_at,
                linked_web_url=linked_web_url,
                now=now,
                soft_complete_window_days=settings.SOFT_COMPLETE_WINDOW_DAYS,
            )
            summary["task"] = task_result
        except Exception as exc:  # noqa: BLE001 — caller sees aggregated summary
            count = await dead_letter.record_failure(
                session, conversation.id, exc, operation="task"
            )
            summary["task"] = {
                "action": "dead_letter",
                "error": str(exc),
                "failure_count": count,
            }

        # 5. categories ------------------------------------------------------
        try:
            cat_result = await apply_category_intent(
                session,
                graph,
                conversation,
                latest_message,
                result.category_intent,
                now=now,
            )
            summary["category"] = cat_result
        except Exception as exc:  # noqa: BLE001
            count = await dead_letter.record_failure(
                session, conversation.id, exc, operation="category"
            )
            summary["category"] = {
                "action": "dead_letter",
                "error": str(exc),
                "failure_count": count,
            }

        # 6. threshold gate --------------------------------------------------
        recent = await dead_letter.count_recent_failures(session, conversation.id)
        if recent >= dead_letter.DEAD_LETTER_THRESHOLD:
            await dead_letter.flag_for_review(
                session,
                conversation.id,
                reason=f"writeback_failure_threshold: {recent} failures in 24h",
            )
            summary["needs_review"] = True
            summary["failure_count"] = recent

        await session.flush()

    return summary


__all__ = [
    "apply_reducer_result",
    "check_and_store_key",
    "finalize_key_result",
]


# silence "unused import" for types we intentionally re-export in typing
_ = (select, insert)
