"""Build a ThreadSnapshot from persisted rows. See project-plan §10.2, §11.4."""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from email_intel.db.models import (
    Classification,
    Conversation,
    ConversationState,
    Message,
    TodoTask,
)
from email_intel.schemas.snapshot import (
    CanonicalMessage,
    ThreadSnapshot,
    UserRecipientPosition,
)

_MAX_CLASSIFICATIONS = 5
_MAX_UNRESOLVED_ASKS = 3
_SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+")


def _addr_set(payload: Any) -> set[str]:
    """Extract lowercased address set from to/cc/reply JSON."""
    if not isinstance(payload, list):
        return set()
    out: set[str] = set()
    for entry in payload:
        if isinstance(entry, dict):
            addr = entry.get("address")
            if isinstance(addr, str):
                out.add(addr.lower())
    return out


def _position(msg: Message, user_address: str) -> UserRecipientPosition:
    ua = user_address.lower()
    to_addrs = _addr_set(msg.to_recipients_json)
    cc_addrs = _addr_set(msg.cc_recipients_json)
    # BCC recipients typically don't appear in message payloads for non-recipients;
    # when present they live in a bcc_recipients_json field, which we don't model.
    if ua in to_addrs:
        return UserRecipientPosition.TO
    if ua in cc_addrs:
        return UserRecipientPosition.CC
    return UserRecipientPosition.NONE


def _canonical_message(msg: Message, user_address: str) -> CanonicalMessage:
    ua = user_address.lower()
    from_addr = msg.from_address.lower() if isinstance(msg.from_address, str) else None
    is_from_user = from_addr == ua if from_addr else False
    to_list = sorted(_addr_set(msg.to_recipients_json))
    cc_list = sorted(_addr_set(msg.cc_recipients_json))
    categories = msg.categories_json if isinstance(msg.categories_json, list) else []
    headers = msg.raw_headers_json if isinstance(msg.raw_headers_json, dict) else {}
    return CanonicalMessage(
        graph_message_id=msg.graph_message_id,
        graph_conversation_id=msg.graph_conversation_id,
        from_address=from_addr,
        sender_address=msg.sender_address,
        to_addresses=to_list,
        cc_addresses=cc_list,
        received_at=msg.received_at,
        sent_at=msg.sent_at,
        is_from_user=is_from_user,
        subject=msg.subject,
        body_text=msg.body_text,
        body_preview=msg.body_preview,
        user_position=_position(msg, user_address),
        has_attachments=bool(msg.has_attachments),
        categories=list(categories),
        headers=dict(headers),
    )


def _extract_unresolved_asks(body: str | None) -> list[str]:
    """Stub: pick the first 3 sentences ending in '?'. Real NLP comes later."""
    if not body:
        return []
    found: list[str] = []
    for sentence in _SENTENCE_SPLIT.split(body):
        s = sentence.strip()
        if not s:
            continue
        if s.endswith("?"):
            found.append(s)
            if len(found) >= _MAX_UNRESOLVED_ASKS:
                break
    return found


async def build_snapshot(
    session: AsyncSession,
    conversation_id: int,
    *,
    sent_items_cursor_ts: datetime | None,
    user_address: str,
) -> ThreadSnapshot:
    """Assemble a ThreadSnapshot for ``conversation_id``."""
    conv_stmt = select(Conversation).where(Conversation.id == conversation_id)
    conv = (await session.execute(conv_stmt)).scalar_one()

    msg_stmt = (
        select(Message)
        .where(Message.graph_conversation_id == conv.graph_conversation_id)
        .where(Message.user_id == conv.user_id)
        .where(Message.is_deleted.is_(False))
        .order_by(Message.received_at.asc().nulls_last())
    )
    messages = list((await session.execute(msg_stmt)).scalars())

    canonical = [_canonical_message(m, user_address) for m in messages]

    ua = user_address.lower()
    latest_inbound_ts: datetime | None = None
    latest_outbound_ts: datetime | None = None
    latest_inbound_msg: Message | None = None
    user_sent_last = False

    for m, cm in zip(messages, canonical, strict=True):
        if cm.is_from_user:
            if m.received_at is not None and (
                latest_outbound_ts is None or m.received_at > latest_outbound_ts
            ):
                latest_outbound_ts = m.received_at
        else:
            if m.received_at is not None and (
                latest_inbound_ts is None or m.received_at > latest_inbound_ts
            ):
                latest_inbound_ts = m.received_at
                latest_inbound_msg = m

    if messages:
        last_msg_from = (messages[-1].from_address or "").lower()
        user_sent_last = last_msg_from == ua

    user_position_on_latest = (
        _position(latest_inbound_msg, user_address)
        if latest_inbound_msg is not None
        else UserRecipientPosition.NONE
    )

    unresolved_asks = (
        _extract_unresolved_asks(latest_inbound_msg.body_text)
        if latest_inbound_msg is not None
        else []
    )

    # Classifications — latest N desc
    cls_stmt = (
        select(Classification)
        .where(Classification.conversation_id == conversation_id)
        .order_by(Classification.created_at.desc())
        .limit(_MAX_CLASSIFICATIONS)
    )
    classifications = list((await session.execute(cls_stmt)).scalars())
    classifications_json: list[dict[str, Any]] = [
        {
            "id": c.id,
            "message_id": c.message_id,
            "model_version": c.model_version,
            "rule_version": c.rule_version,
            "primary_bucket": c.primary_bucket.value if c.primary_bucket else None,
            "confidence": c.confidence,
            "extracted_due_at": c.extracted_due_at.isoformat() if c.extracted_due_at else None,
            "extracted_defer_until": c.extracted_defer_until.isoformat()
            if c.extracted_defer_until
            else None,
            "extracted_waiting_on_address": c.extracted_waiting_on_address,
            "extracted_action_owner": c.extracted_action_owner,
            "extracted_escalate_flag": bool(c.extracted_escalate_flag),
            "extracted_newsletter_flag": bool(c.extracted_newsletter_flag),
            "extracted_bulk_flag": bool(c.extracted_bulk_flag),
            "should_create_task": bool(c.should_create_task),
            "reason_short": c.reason_short,
            "created_at": c.created_at.isoformat() if c.created_at else None,
        }
        for c in classifications
    ]

    # Prior task state
    prior_task_id: int | None = conv.open_action_task_id
    prior_completion_kind = None
    prior_soft_complete_until: datetime | None = None
    if prior_task_id is not None:
        task = (
            await session.execute(select(TodoTask).where(TodoTask.id == prior_task_id))
        ).scalar_one_or_none()
        if task is not None:
            prior_completion_kind = task.completion_kind
            prior_soft_complete_until = task.soft_complete_until

    return ThreadSnapshot(
        conversation_id=conv.id,
        graph_conversation_id=conv.graph_conversation_id,
        messages=canonical,
        latest_inbound_ts=latest_inbound_ts,
        latest_outbound_ts=latest_outbound_ts,
        sent_items_cursor_ts=sent_items_cursor_ts,
        user_sent_last=user_sent_last,
        user_position_on_latest=user_position_on_latest,
        unresolved_asks=unresolved_asks,
        latest_due_at=conv.due_at,
        current_waiting_on=conv.waiting_on_address,
        prior_state=conv.open_action_state or ConversationState.none,
        prior_bucket=conv.open_action_bucket,
        prior_task_id=prior_task_id,
        prior_completion_kind=prior_completion_kind,
        prior_soft_complete_until=prior_soft_complete_until,
        deferred_until=conv.deferred_until,
        classifications_json=classifications_json,
    )
