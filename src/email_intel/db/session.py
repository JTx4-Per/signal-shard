"""Async session factory + per-conversation write lock.

SQLite single-writer rule (project-plan §7): all reducer + writeback work for a
given conversation must execute under an in-process asyncio lock keyed by
`conversation_id`. Reads may be concurrent; writes must not. A session bound
to one conversation should always be acquired *inside* the matching lock.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker


def make_session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    """Return a configured async_sessionmaker bound to `engine`."""
    return async_sessionmaker(
        bind=engine,
        class_=AsyncSession,
        expire_on_commit=False,
        autoflush=False,
    )


_conversation_locks: dict[str, asyncio.Lock] = {}
_locks_guard = asyncio.Lock()


@asynccontextmanager
async def acquire_conversation_lock(conversation_id: str) -> AsyncIterator[None]:
    """Serialize writes for a given conversation_id.

    Usage:
        async with acquire_conversation_lock(cid):
            # reducer + writeback here
            ...
    """
    async with _locks_guard:
        lock = _conversation_locks.get(conversation_id)
        if lock is None:
            lock = asyncio.Lock()
            _conversation_locks[conversation_id] = lock
    async with lock:
        yield
