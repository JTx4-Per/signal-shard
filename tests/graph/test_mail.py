"""Tests for mail module."""

from __future__ import annotations

import httpx
import pytest

from email_intel.graph import mail
from email_intel.graph.client import EtagMismatch


async def test_get_well_known_folders(make_client, fake_auth):
    def handler(req: httpx.Request) -> httpx.Response:
        assert req.url.path == "/v1.0/me/mailFolders"
        return httpx.Response(
            200,
            json={
                "value": [
                    {"id": "1", "displayName": "Inbox"},
                    {"id": "2", "displayName": "Sent Items"},
                    {"id": "3", "displayName": "Drafts"},
                ]
            },
        )

    client = make_client(handler, fake_auth)
    folders = await mail.get_well_known_folders(client)
    assert folders["Inbox"]["id"] == "1"
    assert folders["SentItems"]["id"] == "2"
    assert folders["Archive"] is None
    await client.aclose()


async def test_patch_categories_merges_preserving_user_labels(make_client, fake_auth):
    state: dict[str, object] = {"patched": None}

    def handler(req: httpx.Request) -> httpx.Response:
        if req.method == "GET":
            return httpx.Response(
                200,
                json={
                    "id": "MSG1",
                    "categories": ["Important", "AI-Respond"],
                },
            )
        if req.method == "PATCH":
            import json as _json

            state["patched"] = _json.loads(req.content)
            return httpx.Response(
                200,
                json={"id": "MSG1", "categories": state["patched"]["categories"]},
            )
        return httpx.Response(405)

    client = make_client(handler, fake_auth)
    result = await mail.patch_categories(client, "MSG1", ["AI-Act"])
    cats = result["categories"]
    assert "Important" in cats
    assert "AI-Act" in cats
    assert "AI-Respond" not in cats
    await client.aclose()


async def test_patch_categories_with_etag_mismatch_raises(make_client, fake_auth):
    def handler(req: httpx.Request) -> httpx.Response:
        assert req.method == "PATCH"
        assert req.headers.get("If-Match") == "etag-xyz"
        return httpx.Response(412, json={"error": "conflict"})

    client = make_client(handler, fake_auth)
    with pytest.raises(EtagMismatch):
        await mail.patch_categories(client, "MSG1", ["AI-Act"], etag="etag-xyz")
    await client.aclose()
