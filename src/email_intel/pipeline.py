"""End-to-end processing pipeline.

Wires ingestion → classification → reducer → writeback for a single folder
sync job, plus a ``run_full_reducer_cycle`` entry point for scheduled or
manual full-mailbox passes (dev + replay, per project-plan §17).
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from email_intel.classify.pipeline import classify
from email_intel.config import Settings
from email_intel.db.models import (
    Classification,
    Conversation,
    ConversationState,
    MailFolder,
    Message,
    ReviewStatus,
    TodoList,
    User,
)
from email_intel.ingestion.delta_sync import sync_all_folders, sync_folder
from email_intel.ingestion.snapshot_builder import build_snapshot
from email_intel.reducer.reducer import reduce
from email_intel.schemas.events import Evidence
from email_intel.schemas.reducer import ReducerInput
from email_intel.writeback.apply import apply_reducer_result

log = structlog.get_logger(__name__)


__all__ = [
    "process_folder_sync_job",
    "run_full_reducer_cycle",
    "process_conversations",
]


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


async def _load_task_lists(session: AsyncSession, user_id: int) -> dict[str, TodoList]:
    rows = (
        await session.execute(
            select(TodoList).where(TodoList.user_id == user_id)
        )
    ).scalars().all()
    return {tl.purpose or tl.display_name: tl for tl in rows}


async def _latest_message_for_conv(
    session: AsyncSession, graph_conversation_id: str, user_id: int
) -> Message | None:
    stmt = (
        select(Message)
        .where(Message.graph_conversation_id == graph_conversation_id)
        .where(Message.user_id == user_id)
        .where(Message.is_deleted.is_(False))
        .order_by(Message.received_at.desc().nulls_last())
        .limit(1)
    )
    return (await session.execute(stmt)).scalars().first()


async def _process_one_conversation(
    session: AsyncSession,
    graph: Any,
    conversation: Conversation,
    user_email: str,
    sent_items_cursor_ts: datetime | None,
    settings: Settings,
    now: datetime,
    task_lists: dict[str, TodoList],
) -> dict[str, Any]:
    """Run classify + reducer + writeback for a single conversation row.

    Writeback's ``apply_reducer_result`` acquires the per-conversation lock
    itself (G7 / I6 invariants live there), so we don't take it here — doing
    so would deadlock since ``asyncio.Lock`` is not re-entrant.
    """
    snapshot = await build_snapshot(
        session,
        conversation.id,
        sent_items_cursor_ts=sent_items_cursor_ts,
        user_address=user_email,
    )

    classifier_output, review_reason = await classify(snapshot, settings)

    latest = await _latest_message_for_conv(
        session, conversation.graph_conversation_id, conversation.user_id
    )
    if latest is None:
        log.warning(
            "pipeline.no_latest_message",
            conversation_id=conversation.id,
        )
        return {"skipped": "no_latest_message"}

    session.add(
        Classification(
            conversation_id=conversation.id,
            message_id=latest.id,
            model_version=classifier_output.model_version,
            rule_version=classifier_output.rule_version,
            primary_bucket=classifier_output.primary_bucket,
            confidence=classifier_output.confidence,
            extracted_due_at=classifier_output.due_at,
            extracted_defer_until=classifier_output.defer_until,
            extracted_waiting_on_address=classifier_output.waiting_on,
            extracted_action_owner=classifier_output.action_owner,
            extracted_escalate_flag=classifier_output.escalate,
            extracted_newsletter_flag=classifier_output.newsletter,
            extracted_bulk_flag=classifier_output.automated,
            should_create_task=classifier_output.should_create_task,
            reason_short=classifier_output.reason_short,
            classifier_input_hash=classifier_output.classifier_input_hash,
            classification_review_reason=review_reason,
            review_status=(
                ReviewStatus.pending if review_reason else ReviewStatus.none
            ),
        )
    )
    conversation.last_classified_at = now
    await session.flush()

    reducer_input = ReducerInput(
        snapshot=snapshot,
        prior_state=conversation.open_action_state,
        prior_bucket=conversation.open_action_bucket,
        now=now,
        evidence_set=frozenset(),  # evidence detected from snapshot
    )
    result = reduce(reducer_input, settings)

    summary = await apply_reducer_result(
        session,
        graph,
        conversation,
        latest,
        result,
        task_lists=task_lists,
        now=now,
        settings=settings,
        title=conversation.canonical_subject or "(no subject)",
        body_markdown=latest.body_preview or "",
        linked_web_url=latest.web_link,
    )
    return {
        "transition_id": result.transition_id,
        "next_state": result.next_state.value,
        **summary,
    }


async def process_conversations(
    session: AsyncSession,
    graph: Any,
    *,
    conversation_ids: list[int],
    user: User,
    sent_items_cursor_ts: datetime | None,
    settings: Settings,
) -> list[dict[str, Any]]:
    """Classify + reduce + write back a batch of conversations.

    Each conversation runs under its own lock + try/except; one failure does
    not block others.
    """
    now = _utcnow()
    task_lists = await _load_task_lists(session, user.id)
    out: list[dict[str, Any]] = []
    for cid in conversation_ids:
        conv = await session.get(Conversation, cid)
        if conv is None:
            log.warning("pipeline.conversation_missing", conversation_id=cid)
            continue
        try:
            summary = await _process_one_conversation(
                session,
                graph,
                conv,
                user_email=user.email,
                sent_items_cursor_ts=sent_items_cursor_ts,
                settings=settings,
                now=now,
                task_lists=task_lists,
            )
            await session.commit()
            out.append({"conversation_id": cid, **summary})
        except Exception as exc:  # noqa: BLE001
            await session.rollback()
            log.exception(
                "pipeline.conversation_failed",
                conversation_id=cid,
                error=str(exc),
            )
            out.append({"conversation_id": cid, "error": str(exc)})
    return out


async def _conversations_touched(
    session: AsyncSession, user_id: int, graph_conv_ids: set[str]
) -> list[int]:
    if not graph_conv_ids:
        return []
    stmt = (
        select(Conversation.id)
        .where(Conversation.user_id == user_id)
        .where(Conversation.graph_conversation_id.in_(graph_conv_ids))
    )
    return list((await session.execute(stmt)).scalars())


async def process_folder_sync_job(
    session_factory: async_sessionmaker[AsyncSession],
    graph: Any,
    *,
    user_id: int,
    folder_id: int,
    settings: Settings,
) -> dict[str, Any]:
    """Webhook queue worker: sync one folder then reduce each touched conv.

    Steps:
      1. delta-sync the folder → set of graph_conversation_ids touched.
      2. build snapshot, classify, reduce, apply writeback per conversation,
         each under its own lock + try/except.
      3. return a summary suitable for logging.
    """
    summary: dict[str, Any] = {
        "folder_id": folder_id,
        "upserted": 0,
        "removed": 0,
        "conversations_processed": 0,
        "errors": [],
    }

    async with session_factory() as session:
        folder = await session.get(MailFolder, folder_id)
        if folder is None:
            summary["errors"].append("folder_not_found")
            return summary
        user = await session.get(User, user_id)
        if user is None:
            summary["errors"].append("user_not_found")
            return summary

        delta_result = await sync_folder(session, graph, user_id, folder)
        await session.commit()
        summary["upserted"] = delta_result.upserted
        summary["removed"] = delta_result.removed

        # Resolve sent_items_cursor_ts if available
        sent_cursor = await _sent_items_cursor(session, user_id)

        conv_ids = await _conversations_touched(
            session, user_id, delta_result.conversation_ids_touched
        )
        processed = await process_conversations(
            session,
            graph,
            conversation_ids=conv_ids,
            user=user,
            sent_items_cursor_ts=sent_cursor,
            settings=settings,
        )
        summary["conversations_processed"] = len(processed)
        summary["results"] = processed

    log.info("pipeline.folder_sync_job.done", **{k: v for k, v in summary.items() if k != "results"})
    return summary


async def _sent_items_cursor(session: AsyncSession, user_id: int) -> datetime | None:
    stmt = (
        select(MailFolder.last_sync_at)
        .where(MailFolder.user_id == user_id)
        .where(MailFolder.well_known_name == "sentitems")
        .limit(1)
    )
    return (await session.execute(stmt)).scalar_one_or_none()


async def run_full_reducer_cycle(
    session_factory: async_sessionmaker[AsyncSession],
    graph: Any,
    user_id: int,
    settings: Settings,
) -> dict[str, Any]:
    """Entry point for scheduled/manual full-mailbox passes.

    Syncs every folder for the user (Sent Items first — §11.4), then re-runs
    the classify → reduce → writeback pipeline for every conversation with an
    open action state. Intended for dev + replay, not routine production use.
    """
    summary: dict[str, Any] = {"user_id": user_id, "folders": [], "conversations": 0}
    async with session_factory() as session:
        user = await session.get(User, user_id)
        if user is None:
            return {"error": "user_not_found"}

        results = await sync_all_folders(session, graph, user_id)
        await session.commit()
        summary["folders"] = [
            {"folder_id": r.folder_id, "upserted": r.upserted, "removed": r.removed}
            for r in results
        ]

        sent_cursor = await _sent_items_cursor(session, user_id)

        stmt = (
            select(Conversation.id)
            .where(Conversation.user_id == user_id)
            .where(Conversation.open_action_state != ConversationState.done)
        )
        conv_ids = list((await session.execute(stmt)).scalars())
        processed = await process_conversations(
            session,
            graph,
            conversation_ids=conv_ids,
            user=user,
            sent_items_cursor_ts=sent_cursor,
            settings=settings,
        )
        summary["conversations"] = len(processed)
    return summary


# Silence unused-import warning — kept for downstream scheduler symmetry.
_ = Evidence
