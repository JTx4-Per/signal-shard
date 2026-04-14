"""FastAPI app entrypoint — `email_intel.app:app`.

Wires every subsystem built across Waves 2A-2F:

- SQLAlchemy async engine + session factory (`app.state.session_factory`).
- Graph auth + client (`app.state.graph`), if configured via env.
- Webhook ingestion router + review console router.
- An in-process ``JobQueue`` plus an ``APScheduler`` managing subscription
  renewal, delta fallback poll, defer-timer sweep, and dead-letter health.

The scheduler + Graph client start lazily: missing ``MS_GRAPH_CLIENT_ID`` only
emits a warning, because the review console + healthz remain functional for
operators inspecting a quiescent DB.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import structlog
from fastapi import FastAPI

from email_intel.config import Settings, get_settings
from email_intel.db.base import create_async_engine_for
from email_intel.db.session import make_session_factory
from email_intel.ingestion.webhook import JobQueue, router as webhook_router
from email_intel.review.routes import router as review_router

log = structlog.get_logger(__name__)


def _configure_logging(level: str) -> None:
    logging.basicConfig(level=level.upper())
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(
            getattr(logging, level.upper(), logging.INFO)
        ),
    )


def _build_graph(settings: Settings) -> Any | None:
    """Best-effort Graph client construction via the pluggable AuthProvider.

    Never raises on bad config: logs a warning and returns None so the app can
    still boot (useful for running the review console / tests without creds).
    Scheduled Graph jobs will skip work while the client is None.
    """
    try:
        from email_intel.graph.auth import GraphAuthError, build_auth_provider
        from email_intel.graph.client import GraphClient

        token_store = Path(settings.MS_GRAPH_TOKEN_STORE_PATH)
        try:
            auth = build_auth_provider(settings, token_store_path=token_store)
        except GraphAuthError as exc:
            log.warning(
                "graph_auth_not_configured",
                auth_mode=settings.AUTH_MODE,
                detail=str(exc),
            )
            return None
        log.info("graph_auth_ready", auth_mode=settings.AUTH_MODE)
        return GraphClient(auth=auth)
    except Exception:  # pragma: no cover - defensive
        log.exception("graph_client_build_failed")
        return None


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()
    _configure_logging(settings.LOG_LEVEL)

    engine = create_async_engine_for(settings.DATABASE_URL)
    session_factory = make_session_factory(engine)

    app.state.settings = settings
    app.state.engine = engine
    app.state.session_factory = session_factory
    app.state.graph = _build_graph(settings)
    app.state.client_states = {}
    app.state.job_queue = JobQueue()

    # Scheduler — imported lazily to keep app startup cheap in tests.
    from email_intel.scheduler import build_scheduler

    scheduler = build_scheduler(app)
    app.state.scheduler = scheduler
    scheduler.start()
    log.info("app.startup", reducer_version=settings.REDUCER_VERSION)
    try:
        yield
    finally:
        scheduler.shutdown(wait=False)
        graph = getattr(app.state, "graph", None)
        if graph is not None:
            try:
                await graph.aclose()
            except Exception:  # pragma: no cover
                log.exception("graph_close_failed")
        await engine.dispose()
        log.info("app.shutdown")


app = FastAPI(title="email_intel", version="0.1.0", lifespan=lifespan)

# Routers
app.include_router(webhook_router)
app.include_router(review_router)

@app.get("/healthz")
async def healthz() -> dict[str, str]:
    settings = get_settings()
    return {"status": "ok", "reducer_version": settings.REDUCER_VERSION}
