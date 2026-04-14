"""Graph change-notification subscriptions. See project-plan §8.3, §11.2."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from .client import GraphClient


def _iso_z(dt: datetime) -> str:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    else:
        dt = dt.astimezone(timezone.utc)
    # Graph expects ISO8601 with trailing Z (not +00:00).
    return dt.strftime("%Y-%m-%dT%H:%M:%S.") + f"{dt.microsecond // 1000:03d}Z"


async def create_subscription(
    client: GraphClient,
    *,
    resource: str,
    change_types: list[str],
    notification_url: str,
    client_state: str,
    expiration: datetime,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "changeType": ",".join(change_types),
        "notificationUrl": notification_url,
        "resource": resource,
        "expirationDateTime": _iso_z(expiration),
        "clientState": client_state,
    }
    result = await client.post("/subscriptions", json=payload)
    assert result is not None
    return result


async def renew_subscription(
    client: GraphClient, subscription_id: str, new_expiration: datetime
) -> dict[str, Any]:
    result = await client.patch(
        f"/subscriptions/{subscription_id}",
        json={"expirationDateTime": _iso_z(new_expiration)},
    )
    assert result is not None
    return result


async def delete_subscription(client: GraphClient, subscription_id: str) -> None:
    await client.delete(f"/subscriptions/{subscription_id}")


async def list_subscriptions(client: GraphClient) -> list[dict[str, Any]]:
    subs: list[dict[str, Any]] = []
    async for s in client.paged("/subscriptions"):
        subs.append(s)
    return subs


__all__ = [
    "create_subscription",
    "renew_subscription",
    "delete_subscription",
    "list_subscriptions",
]
