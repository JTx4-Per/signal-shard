"""Tests for ingestion.webhook."""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from email_intel.ingestion.webhook import JobQueue, router


def _make_app() -> FastAPI:
    app = FastAPI()
    app.include_router(router)
    app.state.job_queue = JobQueue()
    app.state.client_states = {"sub-1": "secret-state"}
    return app


@pytest.mark.asyncio
async def test_validation_handshake_returns_token_plain() -> None:
    app = _make_app()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        r = await client.post(
            "/graph/notifications", params={"validationToken": "VAL-TOKEN-XYZ"}
        )
    assert r.status_code == 200
    assert r.text == "VAL-TOKEN-XYZ"
    assert r.headers["content-type"].startswith("text/plain")


@pytest.mark.asyncio
async def test_mismatched_client_state_rejected() -> None:
    app = _make_app()
    transport = ASGITransport(app=app)
    payload = {
        "value": [
            {
                "subscriptionId": "sub-1",
                "clientState": "WRONG",
                "changeType": "created",
                "resource": "me/mailfolders('AAA')/messages",
            }
        ]
    }
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        r = await client.post("/graph/notifications", json=payload)
    assert r.status_code == 401


@pytest.mark.asyncio
async def test_valid_notification_enqueues_and_returns_202() -> None:
    app = _make_app()
    transport = ASGITransport(app=app)
    payload = {
        "value": [
            {
                "subscriptionId": "sub-1",
                "clientState": "secret-state",
                "changeType": "created",
                "resource": "me/mailfolders('AAA')/messages",
                "resourceData": {"id": "MSGID"},
            },
            {
                "subscriptionId": "sub-1",
                "clientState": "secret-state",
                "changeType": "updated",
                "resource": "me/mailfolders('AAA')/messages",
                "resourceData": {"id": "MSGID2"},
            },
        ]
    }
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        r = await client.post("/graph/notifications", json=payload)
    assert r.status_code == 202
    queue: JobQueue = app.state.job_queue
    assert len(queue) == 2
    assert queue.jobs[0]["subscription_id"] == "sub-1"
    assert queue.jobs[0]["change_type"] == "created"
    assert "correlation_id" in queue.jobs[0]
