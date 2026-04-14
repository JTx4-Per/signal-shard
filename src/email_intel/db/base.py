"""SQLAlchemy 2.x DeclarativeBase + async engine factory.

Per project-plan §7: SQLite WAL, single-writer. All PRAGMAs applied on every
connection via a dialect-level event.
"""

from __future__ import annotations

from sqlalchemy import event
from sqlalchemy.engine import Engine
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine
from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    """Declarative base for all ORM models."""


def _apply_sqlite_pragmas(dbapi_connection: object, _connection_record: object) -> None:
    """Install required PRAGMAs on every new SQLite connection."""
    cursor = dbapi_connection.cursor()  # type: ignore[attr-defined]
    try:
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA synchronous=NORMAL")
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.execute("PRAGMA busy_timeout=5000")
    finally:
        cursor.close()


def create_async_engine_for(url: str) -> AsyncEngine:
    """Build an async engine with SQLite PRAGMAs registered.

    PRAGMAs are installed on the *sync* dialect underlying the aiosqlite driver —
    aiosqlite proxies a standard sqlite3 connection.
    """
    engine = create_async_engine(url, future=True)

    if engine.dialect.name == "sqlite":

        @event.listens_for(engine.sync_engine, "connect")
        def _on_connect(dbapi_connection: object, connection_record: object) -> None:
            _apply_sqlite_pragmas(dbapi_connection, connection_record)

    # Silence unused-var lint in non-sqlite codepath; keep Engine imported for typing.
    _ = Engine
    return engine
