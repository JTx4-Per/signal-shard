"""Tests for ingestion.delta_sync."""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from email_intel.db.models import MailFolder, Message, SyncEvent, User
from email_intel.ingestion.delta_sync import (
    DeltaTokenInvalidError,
    sync_all_folders,
    sync_folder,
)


class _FakeMailOps:
    def __init__(self, pages_by_call: list[list[dict[str, Any]]],
                 raise_invalid_on_token: str | None = None) -> None:
        self._pages_by_call = pages_by_call
        self._raise_invalid_on_token = raise_invalid_on_token
        self.calls: list[tuple[str, str | None]] = []

    def delta_messages(
        self, folder_id: str, delta_token: str | None
    ) -> AsyncIterator[dict[str, Any]]:
        self.calls.append((folder_id, delta_token))
        if self._raise_invalid_on_token is not None and delta_token == self._raise_invalid_on_token:
            async def gen_raise() -> AsyncIterator[dict[str, Any]]:
                raise DeltaTokenInvalidError("token expired")
                yield  # pragma: no cover
            return gen_raise()

        if not self._pages_by_call:
            pages: list[dict[str, Any]] = []
        else:
            pages = self._pages_by_call.pop(0)

        async def gen() -> AsyncIterator[dict[str, Any]]:
            for p in pages:
                yield p

        return gen()


class _FakeGraph:
    def __init__(self, mail: _FakeMailOps) -> None:
        self.mail = mail


def _msg(i: str, conv: str = "C1") -> dict[str, Any]:
    return {
        "id": i,
        "conversationId": conv,
        "subject": f"msg {i}",
        "receivedDateTime": "2026-04-12T10:00:00Z",
        "sentDateTime": "2026-04-12T10:00:00Z",
        "from": {"emailAddress": {"address": "alice@example.com", "name": "Alice"}},
        "toRecipients": [{"emailAddress": {"address": "me@example.com"}}],
        "body": {"contentType": "text", "content": "hello"},
        "bodyPreview": "hello",
    }


async def _seed_user_and_folder(
    session: AsyncSession, well_known: str = "inbox"
) -> tuple[User, MailFolder]:
    u = User(graph_user_id="user-1", email="me@example.com", display_name="Me")
    session.add(u)
    await session.flush()
    f = MailFolder(
        user_id=u.id,
        graph_folder_id=f"folder-{well_known}",
        well_known_name=well_known,
        display_name=well_known,
        delta_token=None,
    )
    session.add(f)
    await session.flush()
    return u, f


@pytest.mark.asyncio
async def test_two_pages_with_removed(session: AsyncSession) -> None:
    u, f = await _seed_user_and_folder(session)
    pages = [
        [
            {
                "value": [_msg("M1"), _msg("M2")],
                "@odata.nextLink": "continue",
            },
            {
                "value": [
                    _msg("M3"),
                    {"id": "M4", "@removed": {"reason": "deleted"}, "conversationId": "C1"},
                ],
                "@odata.deltaLink": "DELTA-FINAL",
            },
        ]
    ]
    graph = _FakeGraph(_FakeMailOps(pages))

    result = await sync_folder(session, graph, u.id, f)
    await session.commit()

    assert result.upserted == 3
    assert result.removed == 1
    assert result.new_delta_link == "DELTA-FINAL"
    assert result.conversation_ids_touched == {"C1"}

    # Messages persisted
    msgs = (await session.execute(select(Message).order_by(Message.graph_message_id))).scalars().all()
    assert [m.graph_message_id for m in msgs] == ["M1", "M2", "M3", "M4"]
    removed = [m for m in msgs if m.graph_message_id == "M4"][0]
    assert removed.is_deleted is True

    # Folder token persisted
    refreshed = (
        await session.execute(select(MailFolder).where(MailFolder.id == f.id))
    ).scalar_one()
    assert refreshed.delta_token == "DELTA-FINAL"
    assert refreshed.last_sync_at is not None

    # SyncEvent written
    events = (await session.execute(select(SyncEvent))).scalars().all()
    assert any(e.event_type == "delta_run" and e.cursor_or_token == "DELTA-FINAL" for e in events)


@pytest.mark.asyncio
async def test_invalid_delta_token_resets_and_reruns(session: AsyncSession) -> None:
    u, f = await _seed_user_and_folder(session)
    f.delta_token = "STALE-TOKEN"
    await session.flush()

    pages = [
        [
            {
                "value": [_msg("N1")],
                "@odata.deltaLink": "FRESH-DELTA",
            },
        ]
    ]
    mail = _FakeMailOps(pages, raise_invalid_on_token="STALE-TOKEN")
    graph = _FakeGraph(mail)

    result = await sync_folder(session, graph, u.id, f)
    await session.commit()

    assert result.upserted == 1
    assert result.new_delta_link == "FRESH-DELTA"

    # Folder reset call happened with token=None after invalidation
    assert mail.calls[0] == ("folder-inbox", "STALE-TOKEN")
    assert mail.calls[1] == ("folder-inbox", None)

    events = (await session.execute(select(SyncEvent))).scalars().all()
    types = [e.event_type for e in events]
    assert "delta_token_invalid_reset" in types
    assert "delta_run" in types


@pytest.mark.asyncio
async def test_sync_all_folders_sent_before_inbox(session: AsyncSession) -> None:
    u = User(graph_user_id="u1", email="me@example.com")
    session.add(u)
    await session.flush()
    inbox = MailFolder(user_id=u.id, graph_folder_id="fi", well_known_name="inbox", delta_token=None)
    sent = MailFolder(user_id=u.id, graph_folder_id="fs", well_known_name="sentitems", delta_token=None)
    session.add_all([inbox, sent])
    await session.flush()

    pages_per_call = [
        [{"value": [], "@odata.deltaLink": "D-SENT"}],
        [{"value": [], "@odata.deltaLink": "D-INBOX"}],
    ]
    mail = _FakeMailOps(pages_per_call)
    graph = _FakeGraph(mail)

    results = await sync_all_folders(session, graph, u.id)
    await session.commit()

    assert [r.folder_id for r in results] == ["fs", "fi"]
    assert [c[0] for c in mail.calls] == ["fs", "fi"]
