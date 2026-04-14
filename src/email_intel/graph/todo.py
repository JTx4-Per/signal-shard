"""Microsoft To Do operations. See project-plan §14."""

from __future__ import annotations

from typing import Any

from .client import GraphClient


async def list_task_lists(client: GraphClient) -> list[dict[str, Any]]:
    lists: list[dict[str, Any]] = []
    async for item in client.paged("/me/todo/lists"):
        lists.append(item)
    return lists


async def create_task_list(client: GraphClient, display_name: str) -> dict[str, Any]:
    result = await client.post("/me/todo/lists", json={"displayName": display_name})
    assert result is not None
    return result


async def ensure_lists(
    client: GraphClient, purposes: dict[str, str]
) -> dict[str, dict[str, Any]]:
    """Ensure each purpose maps to an existing list; create missing ones."""
    existing = await list_task_lists(client)
    by_name: dict[str, dict[str, Any]] = {
        (lst.get("displayName") or ""): lst for lst in existing
    }
    out: dict[str, dict[str, Any]] = {}
    for purpose, display_name in purposes.items():
        found = by_name.get(display_name)
        if found is None:
            found = await create_task_list(client, display_name)
        out[purpose] = found
    return out


async def create_task(
    client: GraphClient, list_id: str, payload: dict[str, Any]
) -> dict[str, Any]:
    result = await client.post(f"/me/todo/lists/{list_id}/tasks", json=payload)
    assert result is not None
    return result


async def update_task(
    client: GraphClient, list_id: str, task_id: str, payload: dict[str, Any]
) -> dict[str, Any]:
    result = await client.patch(
        f"/me/todo/lists/{list_id}/tasks/{task_id}", json=payload
    )
    assert result is not None
    return result


async def complete_task(
    client: GraphClient, list_id: str, task_id: str
) -> dict[str, Any]:
    return await update_task(client, list_id, task_id, {"status": "completed"})


async def reopen_task(
    client: GraphClient, list_id: str, task_id: str
) -> dict[str, Any]:
    return await update_task(client, list_id, task_id, {"status": "notStarted"})


async def add_linked_resource(
    client: GraphClient,
    list_id: str,
    task_id: str,
    *,
    external_id: str,
    web_url: str,
    app_name: str,
    display_name: str,
) -> dict[str, Any]:
    payload = {
        "externalId": external_id,
        "webUrl": web_url,
        "applicationName": app_name,
        "displayName": display_name,
    }
    result = await client.post(
        f"/me/todo/lists/{list_id}/tasks/{task_id}/linkedResources",
        json=payload,
    )
    assert result is not None
    return result


__all__ = [
    "list_task_lists",
    "create_task_list",
    "ensure_lists",
    "create_task",
    "update_task",
    "complete_task",
    "reopen_task",
    "add_linked_resource",
]
