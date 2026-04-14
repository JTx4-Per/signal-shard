"""Folder delta sync. See project-plan §11.2, §11.3, §11.4, §18.1.

The GraphClient argument is treated structurally: we only require
``client.mail.delta_messages(folder_id, delta_token)`` to be an async iterable
(or awaitable returning pages). This lets tests drop in a fake without
importing the real ``email_intel.graph.client`` implementation.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, AsyncIterator, Protocol

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from email_intel.db.models import MailFolder, Message, SyncEvent
from email_intel.ingestion.normalizer import normalize_message

log = structlog.get_logger(__name__)


class DeltaTokenInvalidError(Exception):
    """Raised by graph.mail.delta_messages when a 410 gone is encountered."""


@dataclass
class _Page:
    values: list[dict[str, Any]]
    next_delta_link: str | None = None


class _MailOps(Protocol):
    def delta_messages(
        self, folder_id: str, delta_token: str | None
    ) -> AsyncIterator[Any]: ...


class GraphClientProto(Protocol):
    @property
    def mail(self) -> _MailOps: ...


@dataclass
class DeltaSyncResult:
    folder_id: str
    upserted: int = 0
    removed: int = 0
    conversation_ids_touched: set[str] = field(default_factory=set)
    new_delta_link: str | None = None


def _extract_delta_link(page: Any) -> str | None:
    """Support two page shapes:
    - dict with @odata.deltaLink / @odata.nextLink
    - a _Page dataclass used by fakes
    """
    if isinstance(page, _Page):
        return page.next_delta_link
    if isinstance(page, dict):
        link = page.get("@odata.deltaLink")
        if isinstance(link, str):
            return link
    return None


def _extract_values(page: Any) -> list[dict[str, Any]]:
    if isinstance(page, _Page):
        return page.values
    if isinstance(page, dict):
        vals = page.get("value")
        if isinstance(vals, list):
            return vals
    return []


async def _upsert_message(session: AsyncSession, record: dict[str, Any]) -> None:
    """Upsert by graph_message_id. Manual SELECT-then-update for portability."""
    stmt = select(Message).where(Message.graph_message_id == record["graph_message_id"])
    existing = (await session.execute(stmt)).scalar_one_or_none()
    if existing is None:
        session.add(Message(**record))
        return
    for key, value in record.items():
        if key == "graph_message_id":
            continue
        # Don't clobber folder_id / fields with None for tombstones except is_deleted
        if value is None and key not in ("body_text", "body_preview"):
            continue
        setattr(existing, key, value)
    existing.updated_at = datetime.now(timezone.utc)


async def sync_folder(
    session: AsyncSession,
    graph: GraphClientProto,
    user_id: int,
    folder: MailFolder,
) -> DeltaSyncResult:
    """Run a delta sync against a single folder.

    On ``DeltaTokenInvalidError`` (Graph 410) we reset the persisted
    ``delta_token`` and re-run exactly once from scratch.
    """
    result = DeltaSyncResult(folder_id=folder.graph_folder_id)

    attempted_reset = False
    token = folder.delta_token
    while True:
        try:
            await _run_delta_pages(session, graph, user_id, folder, token, result)
            break
        except DeltaTokenInvalidError:
            if attempted_reset:
                raise
            attempted_reset = True
            folder.delta_token = None
            token = None
            await session.flush()
            session.add(
                SyncEvent(
                    user_id=user_id,
                    source_type="delta",
                    source_id=folder.graph_folder_id,
                    event_type="delta_token_invalid_reset",
                    cursor_or_token=None,
                    payload_json=None,
                )
            )
            # reset per-run counters
            result = DeltaSyncResult(folder_id=folder.graph_folder_id)
            log.warning(
                "delta_token_invalid_reset",
                folder_id=folder.graph_folder_id,
                user_id=user_id,
            )
            continue

    # Persist new delta link
    if result.new_delta_link is not None:
        folder.delta_token = result.new_delta_link
    folder.last_sync_at = datetime.now(timezone.utc)

    # Emit sync_events row for this run
    session.add(
        SyncEvent(
            user_id=user_id,
            source_type="delta",
            source_id=folder.graph_folder_id,
            event_type="delta_run",
            cursor_or_token=result.new_delta_link,
            payload_json={
                "upserted": result.upserted,
                "removed": result.removed,
                "conversations_touched": len(result.conversation_ids_touched),
            },
            processed_at=datetime.now(timezone.utc),
        )
    )
    await session.flush()
    return result


async def _run_delta_pages(
    session: AsyncSession,
    graph: GraphClientProto,
    user_id: int,
    folder: MailFolder,
    token: str | None,
    result: DeltaSyncResult,
) -> None:
    async for page in graph.mail.delta_messages(folder.graph_folder_id, token):
        for raw in _extract_values(page):
            record = normalize_message(raw, user_id=user_id, folder=folder)
            conv_id = record.get("graph_conversation_id")
            if conv_id:
                result.conversation_ids_touched.add(conv_id)
            await _upsert_message(session, record)
            if record.get("is_deleted"):
                result.removed += 1
            else:
                result.upserted += 1
        link = _extract_delta_link(page)
        if link is not None:
            result.new_delta_link = link


def _folder_priority(folder: MailFolder) -> int:
    """Order folders so Sent Items syncs before Inbox (§11.4)."""
    name = (folder.well_known_name or "").lower()
    if name == "sentitems":
        return 0
    if name == "inbox":
        return 1
    return 2


async def sync_all_folders(
    session: AsyncSession, graph: GraphClientProto, user_id: int
) -> list[DeltaSyncResult]:
    """Sync every mail_folder for the user.

    Returns results in the order folders were synced: Sent Items first, then
    Inbox, then any others. Callers can compare cursor timestamps to make the
    §11.4 Sent-lag decision.
    """
    stmt = select(MailFolder).where(MailFolder.user_id == user_id)
    folders = list((await session.execute(stmt)).scalars())
    folders.sort(key=_folder_priority)

    results: list[DeltaSyncResult] = []
    for folder in folders:
        res = await sync_folder(session, graph, user_id, folder)
        results.append(res)
    return results
