"""FastAPI router for Graph change notifications + validation handshake.

See project-plan §11.2, §18.1 (subscription expiration).
"""

from __future__ import annotations

import asyncio
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Protocol

import structlog
from fastapi import APIRouter, HTTPException, Request, Response, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from email_intel.db.models import MailFolder

log = structlog.get_logger(__name__)


@dataclass
class JobQueue:
    """Minimal in-process job queue; Wave 3 swaps in the real scheduler."""

    jobs: list[dict[str, Any]] = field(default_factory=list)
    _tasks: set[asyncio.Task[Any]] = field(default_factory=set)

    def enqueue(self, job: dict[str, Any]) -> None:
        self.jobs.append(job)

    def __len__(self) -> int:
        return len(self.jobs)


class SubscriptionsOps(Protocol):
    async def renew_subscription(self, subscription_id: str) -> Any: ...


class GraphClientProto(Protocol):
    @property
    def subscriptions(self) -> SubscriptionsOps: ...


router = APIRouter(prefix="/graph", tags=["graph-notifications"])


def _job_queue(request: Request) -> JobQueue:
    queue: JobQueue | None = getattr(request.app.state, "job_queue", None)
    if queue is None:
        queue = JobQueue()
        request.app.state.job_queue = queue
    return queue


def _client_state_for(request: Request, subscription_id: str) -> str | None:
    """Look up the expected clientState for a subscription.

    Production: read from mail_folders row. For tests, app.state may provide
    a dict override ``client_states: {subscription_id: expected_state}``.
    """
    overrides: dict[str, str] | None = getattr(request.app.state, "client_states", None)
    if overrides is not None:
        return overrides.get(subscription_id)
    return None


@router.get("/notifications")
@router.post("/notifications")
async def notifications(request: Request) -> Response:
    # Validation handshake: Graph sends ?validationToken=... expecting plain text.
    validation_token = request.query_params.get("validationToken")
    if validation_token is not None:
        return Response(content=validation_token, media_type="text/plain", status_code=200)

    if request.method != "POST":
        raise HTTPException(status_code=status.HTTP_405_METHOD_NOT_ALLOWED)

    try:
        payload = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="invalid json") from None

    items = payload.get("value") if isinstance(payload, dict) else None
    if not isinstance(items, list):
        raise HTTPException(status_code=400, detail="missing value[] array")

    queue = _job_queue(request)

    for note in items:
        if not isinstance(note, dict):
            raise HTTPException(status_code=400, detail="malformed notification")
        subscription_id = note.get("subscriptionId")
        client_state = note.get("clientState")
        if not isinstance(subscription_id, str):
            raise HTTPException(status_code=400, detail="missing subscriptionId")

        expected = _client_state_for(request, subscription_id)
        if expected is None or expected != client_state:
            log.warning(
                "webhook_client_state_mismatch",
                subscription_id=subscription_id,
            )
            raise HTTPException(status_code=401, detail="clientState mismatch")

        correlation_id = str(uuid.uuid4())
        log.info(
            "webhook_notification_received",
            correlation_id=correlation_id,
            subscription_id=subscription_id,
            change_type=note.get("changeType"),
            resource=note.get("resource"),
        )
        queue.enqueue(
            {
                "correlation_id": correlation_id,
                "subscription_id": subscription_id,
                "change_type": note.get("changeType"),
                "resource": note.get("resource"),
                "resource_data": note.get("resourceData"),
            }
        )

    return Response(status_code=status.HTTP_202_ACCEPTED)


async def renew_due_subscriptions(
    session: AsyncSession,
    graph: GraphClientProto,
    *,
    renew_before: timedelta,
) -> int:
    """Renew every mail_folder subscription expiring before ``now+renew_before``.

    Returns the number of subscriptions renewed.
    """
    now = datetime.now(timezone.utc)
    threshold = now + renew_before

    stmt = select(MailFolder).where(
        MailFolder.subscription_id.is_not(None),
        MailFolder.subscription_expires_at.is_not(None),
        MailFolder.subscription_expires_at < threshold,
    )
    folders = list((await session.execute(stmt)).scalars())
    renewed = 0
    for folder in folders:
        sub_id = folder.subscription_id
        if sub_id is None:
            continue
        try:
            result = await graph.subscriptions.renew_subscription(sub_id)
        except Exception:
            log.exception("subscription_renew_failed", subscription_id=sub_id)
            continue
        # Graph returns the updated subscription object with a new expirationDateTime.
        new_expiry: datetime | None = None
        if isinstance(result, dict):
            raw = result.get("expirationDateTime")
            if isinstance(raw, str):
                try:
                    iso = raw[:-1] + "+00:00" if raw.endswith("Z") else raw
                    new_expiry = datetime.fromisoformat(iso)
                    if new_expiry.tzinfo is None:
                        new_expiry = new_expiry.replace(tzinfo=timezone.utc)
                except ValueError:
                    new_expiry = None
        if new_expiry is not None:
            folder.subscription_expires_at = new_expiry
        renewed += 1
    await session.flush()
    return renewed
