"""Tests for GraphClient: retries, paging, delta, 404 semantics."""

from __future__ import annotations

import httpx
import pytest

from email_intel.graph.client import EtagMismatch, GraphClient, GraphHTTPError


async def test_401_triggers_refresh_then_retries(make_client, fake_auth):
    calls: list[str] = []

    def handler(req: httpx.Request) -> httpx.Response:
        calls.append(req.headers["Authorization"])
        if len(calls) == 1:
            return httpx.Response(401, json={"error": "expired"})
        return httpx.Response(200, json={"ok": True})

    client: GraphClient = make_client(handler, fake_auth)
    res = await client.get("/me")
    assert res == {"ok": True}
    assert fake_auth.refresh_if_needed.await_count == 1
    assert calls[0] != calls[1]  # token changed
    await client.aclose()


async def test_429_retry_after_honored(make_client, fake_auth):
    attempts: list[int] = []

    def handler(req: httpx.Request) -> httpx.Response:
        attempts.append(1)
        if len(attempts) < 3:
            return httpx.Response(429, headers={"Retry-After": "1"}, json={})
        return httpx.Response(200, json={"done": True})

    client = make_client(handler, fake_auth)
    res = await client.get("/me/messages")
    assert res == {"done": True}
    assert len(attempts) == 3
    await client.aclose()


async def test_5xx_backoff_then_success(make_client, fake_auth):
    count = {"n": 0}

    def handler(req: httpx.Request) -> httpx.Response:
        count["n"] += 1
        if count["n"] < 4:
            return httpx.Response(503, json={})
        return httpx.Response(200, json={"v": 1})

    client = make_client(handler, fake_auth)
    res = await client.get("/x")
    assert res == {"v": 1}
    assert count["n"] == 4
    await client.aclose()


async def test_5xx_exhausts_attempts(make_client, fake_auth):
    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(500, json={"err": "boom"})

    client = make_client(handler, fake_auth)
    with pytest.raises(GraphHTTPError) as exc:
        await client.get("/x")
    assert exc.value.status == 500
    await client.aclose()


async def test_paged_follows_nextlink(make_client, fake_auth):
    def handler(req: httpx.Request) -> httpx.Response:
        if req.url.path.endswith("/page2"):
            return httpx.Response(200, json={"value": [{"id": "3"}]})
        if req.url.path == "/v1.0/items":
            return httpx.Response(
                200,
                json={
                    "value": [{"id": "1"}, {"id": "2"}],
                    "@odata.nextLink": "https://graph.microsoft.com/v1.0/page2",
                },
            )
        return httpx.Response(404)

    client = make_client(handler, fake_auth)
    ids = [item["id"] async for item in client.paged("/items")]
    assert ids == ["1", "2", "3"]
    await client.aclose()


async def test_delta_surfaces_deltalink_only_on_last_page(make_client, fake_auth):
    def handler(req: httpx.Request) -> httpx.Response:
        if req.url.path.endswith("/next"):
            return httpx.Response(
                200,
                json={
                    "value": [{"id": "b"}],
                    "@odata.deltaLink": "https://graph.microsoft.com/v1.0/deltaX",
                },
            )
        return httpx.Response(
            200,
            json={
                "value": [{"id": "a"}],
                "@odata.nextLink": "https://graph.microsoft.com/v1.0/next",
            },
        )

    client = make_client(handler, fake_auth)
    collected: list[tuple[str, str | None]] = []
    async for item, delta in client.delta("/me/mailFolders/x/messages/delta"):
        collected.append((item["id"], delta))
    assert collected[0] == ("a", None)
    assert collected[1][0] == "b"
    assert collected[1][1] and collected[1][1].endswith("/deltaX")
    await client.aclose()


async def test_404_get_returns_none(make_client, fake_auth):
    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(404)

    client = make_client(handler, fake_auth)
    assert await client.get("/missing") is None
    await client.aclose()


async def test_404_post_raises(make_client, fake_auth):
    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(404, json={"error": "nope"})

    client = make_client(handler, fake_auth)
    with pytest.raises(GraphHTTPError):
        await client.post("/missing", json={"x": 1})
    await client.aclose()


async def test_412_raises_etag_mismatch(make_client, fake_auth):
    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(412, json={"error": "etag"})

    client = make_client(handler, fake_auth)
    with pytest.raises(EtagMismatch):
        await client.patch("/me/messages/X", json={}, headers={"If-Match": "abc"})
    await client.aclose()
