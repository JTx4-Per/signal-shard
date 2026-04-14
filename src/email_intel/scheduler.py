"""APScheduler registration for background jobs.

Runs four recurring jobs on the shared event loop:

* **subscription_renewal** — every 2h — calls
  :func:`email_intel.ingestion.webhook.renew_due_subscriptions` with a
  ``timedelta(hours=12)`` window so Graph doesn't expire our mailbox
  subscriptions mid-day.
* **delta_fallback_poll** — every 10m — runs
  :func:`email_intel.ingestion.delta_sync.sync_all_folders` for every user
  *only if* no webhook traffic has arrived in the last 15 minutes.
* **defer_sweeper** — every 1m — finds conversations with
  ``open_action_state == deferred`` and ``deferred_until <= now``, enqueuing
  a reducer-only cycle (no Graph sync required).
* **dead_letter_health** — every 30m — emits a structlog summary of every
  conversation still sitting in ``needs_review``.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING, Any

import structlog
from apscheduler.schedulers.asyncio import AsyncIOScheduler  # type: ignore[import-untyped]
from sqlalchemy import func, select

from email_intel.db.models import Conversation, ConversationState, User

if TYPE_CHECKING:
    from fastapi import FastAPI

log = structlog.get_logger(__name__)


_RENEWAL_INTERVAL_HOURS = 2
_RENEWAL_WINDOW_HOURS = 12
_POLL_INTERVAL_MINUTES = 10
_POLL_SKIP_IF_WEBHOOK_WITHIN_MINUTES = 15
_DEFER_SWEEP_INTERVAL_MINUTES = 1
_DEAD_LETTER_INTERVAL_MINUTES = 30


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


async def _renew_subscriptions_job(app: "FastAPI") -> None:
    graph = getattr(app.state, "graph", None)
    factory = getattr(app.state, "session_factory", None)
    if graph is None or factory is None:
        return
    from email_intel.ingestion.webhook import renew_due_subscriptions

    async with factory() as session:
        try:
            count = await renew_due_subscriptions(
                session, graph, renew_before=timedelta(hours=_RENEWAL_WINDOW_HOURS)
            )
            await session.commit()
            log.info("scheduler.subscription_renewal", renewed=count)
        except Exception:
            await session.rollback()
            log.exception("scheduler.subscription_renewal_failed")


async def _delta_poll_job(app: "FastAPI") -> None:
    graph = getattr(app.state, "graph", None)
    factory = getattr(app.state, "session_factory", None)
    if graph is None or factory is None:
        return
    last_webhook: datetime | None = getattr(app.state, "last_webhook_at", None)
    if last_webhook is not None:
        delta = _utcnow() - last_webhook
        if delta < timedelta(minutes=_POLL_SKIP_IF_WEBHOOK_WITHIN_MINUTES):
            return

    from email_intel.pipeline import run_full_reducer_cycle

    settings = app.state.settings
    async with factory() as session:
        users = list((await session.execute(select(User))).scalars())
    for user in users:
        try:
            summary = await run_full_reducer_cycle(factory, graph, user.id, settings)
            log.info("scheduler.delta_poll", user_id=user.id, **{
                "folders": len(summary.get("folders", [])),
                "conversations": summary.get("conversations", 0),
            })
        except Exception:
            log.exception("scheduler.delta_poll_failed", user_id=user.id)


async def _defer_sweeper_job(app: "FastAPI") -> None:
    factory = getattr(app.state, "session_factory", None)
    graph = getattr(app.state, "graph", None)
    if factory is None:
        return
    now = _utcnow()
    async with factory() as session:
        stmt = (
            select(Conversation)
            .where(Conversation.open_action_state == ConversationState.deferred)
            .where(Conversation.deferred_until.is_not(None))
            .where(Conversation.deferred_until <= now)
        )
        due = list((await session.execute(stmt)).scalars())
        if not due:
            return

        from email_intel.pipeline import process_conversations

        settings = app.state.settings
        # Group by user
        by_user: dict[int, list[int]] = {}
        for conv in due:
            by_user.setdefault(conv.user_id, []).append(conv.id)

        for user_id, ids in by_user.items():
            user = await session.get(User, user_id)
            if user is None:
                continue
            try:
                await process_conversations(
                    session,
                    graph,
                    conversation_ids=ids,
                    user=user,
                    sent_items_cursor_ts=None,
                    settings=settings,
                )
                log.info(
                    "scheduler.defer_sweeper",
                    user_id=user_id,
                    conversations=len(ids),
                )
            except Exception:
                await session.rollback()
                log.exception("scheduler.defer_sweeper_failed", user_id=user_id)


async def _dead_letter_health_job(app: "FastAPI") -> None:
    factory = getattr(app.state, "session_factory", None)
    if factory is None:
        return
    async with factory() as session:
        stmt = select(func.count()).select_from(Conversation).where(
            Conversation.open_action_state == ConversationState.needs_review
        )
        count = (await session.execute(stmt)).scalar_one()
    log.info("scheduler.dead_letter_health", needs_review_count=count)


def build_scheduler(app: "FastAPI") -> AsyncIOScheduler:
    """Create + configure (but do not start) the application scheduler."""
    scheduler = AsyncIOScheduler(timezone="UTC")

    scheduler.add_job(
        _renew_subscriptions_job,
        "interval",
        hours=_RENEWAL_INTERVAL_HOURS,
        id="subscription_renewal",
        kwargs={"app": app},
        replace_existing=True,
    )
    scheduler.add_job(
        _delta_poll_job,
        "interval",
        minutes=_POLL_INTERVAL_MINUTES,
        id="delta_fallback_poll",
        kwargs={"app": app},
        replace_existing=True,
    )
    scheduler.add_job(
        _defer_sweeper_job,
        "interval",
        minutes=_DEFER_SWEEP_INTERVAL_MINUTES,
        id="defer_sweeper",
        kwargs={"app": app},
        replace_existing=True,
    )
    scheduler.add_job(
        _dead_letter_health_job,
        "interval",
        minutes=_DEAD_LETTER_INTERVAL_MINUTES,
        id="dead_letter_health",
        kwargs={"app": app},
        replace_existing=True,
    )
    return scheduler


def _silence_mypy_unused(_x: Any) -> None:
    pass
