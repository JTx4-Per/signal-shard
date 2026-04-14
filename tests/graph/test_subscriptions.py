"""Tests for subscriptions module."""

from __future__ import annotations

import json
from datetime import datetime, timezone

import httpx

from email_intel.graph import subscriptions


async def test_create_subscription_iso8601_z(make_client, fake_auth):
    seen: dict[str, object] = {}

    def handler(req: httpx.Request) -> httpx.Response:
        assert req.method == "POST"
        assert req.url.path == "/v1.0/subscriptions"
        seen.update(json.loads(req.content))
        return httpx.Response(201, json={"id": "SUB1", **seen})

    client = make_client(handler, fake_auth)
    expiry = datetime(2026, 4, 16, 12, 30, 45, 123000, tzinfo=timezone.utc)
    res = await subscriptions.create_subscription(
        client,
        resource="/me/mailFolders('Inbox')/messages",
        change_types=["created", "updated"],
        notification_url="https://example.org/hook",
        client_state="secret",
        expiration=expiry,
    )
    assert res["id"] == "SUB1"
    assert seen["expirationDateTime"] == "2026-04-16T12:30:45.123Z"
    assert seen["changeType"] == "created,updated"
    await client.aclose()


async def test_renew_sends_patch(make_client, fake_auth):
    seen: dict[str, object] = {}

    def handler(req: httpx.Request) -> httpx.Response:
        assert req.method == "PATCH"
        assert req.url.path == "/v1.0/subscriptions/SUB1"
        seen.update(json.loads(req.content))
        return httpx.Response(200, json={"id": "SUB1", **seen})

    client = make_client(handler, fake_auth)
    expiry = datetime(2026, 4, 17, 0, 0, 0, tzinfo=timezone.utc)
    res = await subscriptions.renew_subscription(client, "SUB1", expiry)
    assert res["id"] == "SUB1"
    assert seen["expirationDateTime"].endswith("Z")
    await client.aclose()
