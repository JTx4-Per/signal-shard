"""Category writeback — ``AI-*`` labels on Outlook messages.

See project-plan §15.1 and reducer-spec §6 (category side-effect matrix).
Merge rules:

* We always drop all existing ``AI-*`` categories.
* ``apply`` adds exactly one ``AI-<bucket>`` based on the bucket mapping.
* ``clear`` strips ``AI-*`` without adding anything.
* User-assigned categories (anything not prefixed ``AI-``) are preserved.

Invariants:

* **I7** NEEDS_REVIEW guard — if the conversation is in ``needs_review`` the
  function short-circuits as a no-op (belt-and-suspenders with reducer G7).
* **I6** operation-key idempotency via
  :func:`email_intel.writeback.apply.check_and_store_key`.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from email_intel.db.models import (
    AuditLog,
    Conversation,
    ConversationBucket,
    ConversationState,
    Message,
    OperationType,
)
from email_intel.graph import mail as graph_mail
from email_intel.graph.client import EtagMismatch, GraphClient
from email_intel.schemas.intents import CategoryIntent, CategoryIntentKind

AI_PREFIX = "AI-"

_BUCKET_TO_AI_LABEL: dict[ConversationBucket, str] = {
    ConversationBucket.Act: "AI-Act",
    ConversationBucket.Respond: "AI-Respond",
    ConversationBucket.WaitingOn: "AI-Waiting",
    ConversationBucket.Delegate: "AI-Delegate",
    ConversationBucket.Defer: "AI-Deferred",
    ConversationBucket.FYI: "AI-FYI",
    ConversationBucket.DeleteOrUnsubscribe: "AI-Noise",
}


def _current_categories(msg: Message) -> list[str]:
    raw = msg.categories_json
    if isinstance(raw, list):
        return [str(x) for x in raw]
    if isinstance(raw, dict) and "value" in raw and isinstance(raw["value"], list):
        return [str(x) for x in raw["value"]]
    return []


def _strip_ai(cats: list[str]) -> list[str]:
    return [c for c in cats if not c.startswith(AI_PREFIX)]


def _merge_apply(current: list[str], bucket: ConversationBucket) -> list[str]:
    preserved = _strip_ai(current)
    label = _BUCKET_TO_AI_LABEL.get(bucket)
    if label is None:
        return list(dict.fromkeys(preserved))
    return list(dict.fromkeys([*preserved, label]))


async def apply_category_intent(
    session: AsyncSession,
    graph: GraphClient,
    conversation: Conversation,
    latest_message: Message,
    intent: CategoryIntent,
    *,
    now: datetime,
) -> dict[str, Any]:
    from email_intel.writeback.apply import check_and_store_key, finalize_key_result

    # I7 belt-and-suspenders.
    if conversation.open_action_state == ConversationState.needs_review:
        return {"action": "noop", "reason": "needs_review"}

    kind = intent.kind
    if kind is CategoryIntentKind.noop or kind is CategoryIntentKind.preserve:
        return {"action": "noop"}

    op_key = intent.operation_key
    if op_key:
        is_first, prior = await check_and_store_key(
            session,
            key=op_key,
            operation_type=OperationType.category_patch,
            conversation_id=conversation.id,
            payload_hash="",
        )
        if not is_first:
            return prior or {"action": "noop", "idempotent": True}

    current = _current_categories(latest_message)

    if kind is CategoryIntentKind.clear:
        new_cats = _strip_ai(current)
        action = "cleared"
    elif kind is CategoryIntentKind.apply:
        bucket = intent.target_bucket
        if bucket is None:
            raise ValueError("CategoryIntent.apply requires target_bucket")
        new_cats = _merge_apply(current, bucket)
        action = "applied"
    else:  # pragma: no cover — exhaustive above
        raise RuntimeError(f"unhandled CategoryIntentKind: {kind!r}")

    if new_cats == current:
        result: dict[str, Any] = {"action": "noop", "categories": new_cats}
        if op_key:
            await finalize_key_result(session, op_key, result)
        return result

    etag = latest_message.etag
    try:
        await graph_mail.patch_categories(
            graph, latest_message.graph_message_id, new_cats, etag=etag
        )
    except EtagMismatch:
        # Refetch once, recompute merge against the server copy, retry without If-Match.
        fresh = await graph_mail.get_message(graph, latest_message.graph_message_id)
        if fresh is not None:
            server_cats = [str(c) for c in (fresh.get("categories") or [])]
            if kind is CategoryIntentKind.clear:
                new_cats = _strip_ai(server_cats)
            else:
                assert intent.target_bucket is not None
                new_cats = _merge_apply(server_cats, intent.target_bucket)
            new_etag = fresh.get("@odata.etag")
            if isinstance(new_etag, str):
                latest_message.etag = new_etag
                etag = new_etag
        await graph_mail.patch_categories(
            graph, latest_message.graph_message_id, new_cats, etag=None
        )

    latest_message.categories_json = new_cats
    latest_message.updated_at = now

    session.add(
        AuditLog(
            user_id=conversation.user_id,
            entity_type="message",
            entity_id=latest_message.graph_message_id,
            action=f"category_{action}",
            before_json={"categories": current},
            after_json={"categories": new_cats},
            source="writeback.categories",
        )
    )

    result = {
        "action": action,
        "categories": new_cats,
        "message_id": latest_message.graph_message_id,
    }
    if op_key:
        await finalize_key_result(session, op_key, result)
    return result


__all__ = ["apply_category_intent"]
