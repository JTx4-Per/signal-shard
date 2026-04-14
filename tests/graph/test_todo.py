"""Tests for todo module."""

from __future__ import annotations

import json

import httpx

from email_intel.graph import todo


async def test_ensure_lists_creates_only_missing(make_client, fake_auth):
    created: list[str] = []

    def handler(req: httpx.Request) -> httpx.Response:
        if req.method == "GET" and req.url.path == "/v1.0/me/todo/lists":
            return httpx.Response(
                200,
                json={"value": [{"id": "L1", "displayName": "AI Act"}]},
            )
        if req.method == "POST" and req.url.path == "/v1.0/me/todo/lists":
            body = json.loads(req.content)
            created.append(body["displayName"])
            return httpx.Response(201, json={"id": "L2", "displayName": body["displayName"]})
        return httpx.Response(404)

    client = make_client(handler, fake_auth)
    result = await todo.ensure_lists(client, {"act": "AI Act", "respond": "AI Respond"})
    assert result["act"]["id"] == "L1"
    assert result["respond"]["id"] == "L2"
    assert created == ["AI Respond"]
    await client.aclose()


async def test_add_linked_resource_builds_correct_url(make_client, fake_auth):
    captured: dict[str, object] = {}

    def handler(req: httpx.Request) -> httpx.Response:
        captured["path"] = req.url.path
        captured["body"] = json.loads(req.content)
        return httpx.Response(201, json={"id": "LR1"})

    client = make_client(handler, fake_auth)
    res = await todo.add_linked_resource(
        client,
        "LID",
        "TID",
        external_id="msg-1",
        web_url="https://outlook.office.com/x",
        app_name="email_intel",
        display_name="Re: hello",
    )
    assert res == {"id": "LR1"}
    assert captured["path"] == "/v1.0/me/todo/lists/LID/tasks/TID/linkedResources"
    assert captured["body"]["externalId"] == "msg-1"
    await client.aclose()


async def test_complete_task_sends_completed_status(make_client, fake_auth):
    body_seen: dict[str, object] = {}

    def handler(req: httpx.Request) -> httpx.Response:
        assert req.method == "PATCH"
        body_seen.update(json.loads(req.content))
        return httpx.Response(200, json={"id": "T1", "status": "completed"})

    client = make_client(handler, fake_auth)
    res = await todo.complete_task(client, "LID", "T1")
    assert res["status"] == "completed"
    assert body_seen == {"status": "completed"}
    await client.aclose()
