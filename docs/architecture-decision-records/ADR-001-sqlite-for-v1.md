# ADR-001: SQLite for v1

**Status:** Accepted — 2026-Q1
**Context:** project-plan §7

## Decision

The v1 deployment uses SQLite (via `aiosqlite` + SQLAlchemy async) as the
primary store, with WAL, `synchronous=NORMAL`, `foreign_keys=ON`, and a
5-second busy timeout.

## Rationale

- This is a single-user personal tool; the expected load is hundreds of
  writes per day, not per second.
- SQLite in WAL mode supports concurrent readers with one writer — which
  matches our access pattern (review console reads + one reducer writer).
- No server process = cheaper ops footprint, easier backup (single file),
  easier local development.

## Consequences

- We must enforce **single-writer-per-conversation** in the application
  layer via `acquire_conversation_lock(cid)`. No SQLite-level row locking
  helps us here; asyncio.Lock does.
- Backup/restore is just copying the `.db` file, provided WAL is
  checkpointed (see `docs/runbook.md`).
- Migration to Postgres is a future wave; all SQL is portable SQLAlchemy
  2.x and we avoid SQLite-specific syntax except for the `sqlite_where`
  partial indexes — easily re-expressed as `WHERE ... IS NOT NULL`.
