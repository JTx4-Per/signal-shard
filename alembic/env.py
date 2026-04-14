"""Alembic env — async-capable; reads DATABASE_URL from env if present."""

from __future__ import annotations

import asyncio
import os
from logging.config import fileConfig

from alembic import context
from sqlalchemy import pool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import async_engine_from_config

from email_intel.db.base import Base
import email_intel.db.models  # noqa: F401  # ensure all tables are registered

config = context.config

# Prefer env DATABASE_URL; alembic's sqlalchemy.url only carries a default.
_db_url = os.environ.get("DATABASE_URL", config.get_main_option("sqlalchemy.url"))
# Alembic doesn't need the async driver for offline mode; keep the URL whatever
# the caller gave us, then swap to sync for sqlite if needed.
if _db_url and _db_url.startswith("sqlite+aiosqlite"):
    sync_url = _db_url.replace("sqlite+aiosqlite", "sqlite")
else:
    sync_url = _db_url or "sqlite:///./email_intel.db"
config.set_main_option("sqlalchemy.url", sync_url)

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        render_as_batch=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def _do_run_migrations(connection: Connection) -> None:
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        render_as_batch=True,
    )
    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    # Use the async driver for online mode.
    url = os.environ.get("DATABASE_URL", "sqlite+aiosqlite:///./email_intel.db")
    section = config.get_section(config.config_ini_section, {})
    section["sqlalchemy.url"] = url
    connectable = async_engine_from_config(
        section,
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    async with connectable.connect() as connection:
        await connection.run_sync(_do_run_migrations)
    await connectable.dispose()


def run_migrations_online() -> None:
    asyncio.run(run_async_migrations())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
