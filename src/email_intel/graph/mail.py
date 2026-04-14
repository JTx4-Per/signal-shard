"""Mail operations: folder listing, delta, message fetch, category patch. §8.3."""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

from .client import EtagMismatch, GraphClient

AI_CATEGORY_PREFIX = "AI-"
WELL_KNOWN = ("Inbox", "SentItems", "Archive")


async def list_mail_folders(client: GraphClient) -> list[dict[str, Any]]:
    folders: list[dict[str, Any]] = []
    async for f in client.paged("/me/mailFolders", params={"$top": 100}):
        folders.append(f)
    return folders


async def get_well_known_folders(client: GraphClient) -> dict[str, dict[str, Any] | None]:
    """Pick out Inbox, SentItems, Archive from the folder list by displayName match."""
    folders = await list_mail_folders(client)
    by_name: dict[str, dict[str, Any]] = {}
    for f in folders:
        name = (f.get("displayName") or "").strip()
        if name:
            by_name[name] = f
    result: dict[str, dict[str, Any] | None] = {}
    # Inbox/SentItems vary: "Sent Items" vs "SentItems".
    result["Inbox"] = by_name.get("Inbox")
    result["SentItems"] = by_name.get("Sent Items") or by_name.get("SentItems")
    result["Archive"] = by_name.get("Archive")
    return result


async def delta_messages(
    client: GraphClient, folder_id: str, delta_link: str | None
) -> AsyncIterator[tuple[dict[str, Any], str | None]]:
    path = delta_link or f"/me/mailFolders/{folder_id}/messages/delta"
    async for item, new_delta in client.delta(path):
        yield item, new_delta


async def get_message(client: GraphClient, message_id: str) -> dict[str, Any] | None:
    return await client.get(f"/me/messages/{message_id}")


async def patch_categories(
    client: GraphClient,
    message_id: str,
    categories: list[str],
    etag: str | None = None,
) -> dict[str, Any]:
    """Patch categories.

    If `etag` is provided we send If-Match and trust the caller's merge. A 412
    bubbles up as EtagMismatch. When etag is None we GET the message, strip
    existing AI-* categories, and union with the requested set to preserve
    user-assigned labels.
    """
    headers: dict[str, str] = {}
    final_categories: list[str]

    if etag is not None:
        headers["If-Match"] = etag
        final_categories = list(dict.fromkeys(categories))
    else:
        existing = await get_message(client, message_id)
        current: list[str] = list(existing.get("categories", []) if existing else [])
        preserved = [c for c in current if not c.startswith(AI_CATEGORY_PREFIX)]
        final_categories = list(dict.fromkeys([*preserved, *categories]))

    try:
        result = await client.patch(
            f"/me/messages/{message_id}",
            json={"categories": final_categories},
            headers=headers or None,
        )
    except EtagMismatch:
        raise

    if result is None:
        return {"id": message_id, "categories": final_categories}
    return result


__all__ = [
    "list_mail_folders",
    "get_well_known_folders",
    "delta_messages",
    "get_message",
    "patch_categories",
    "EtagMismatch",
]
