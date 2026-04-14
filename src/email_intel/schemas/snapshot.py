"""ThreadSnapshot — the reducer's input view.

See project-plan §10.2 + reducer-spec §3 requirements. Must carry enough
context to evaluate every evidence rule E01–E15 and any priority-tier
decision, including Sent Items lag (G8 / I3).
"""

from __future__ import annotations

import enum
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from email_intel.db.models import (
    CompletionKind,
    ConversationBucket,
    ConversationState,
)


class UserRecipientPosition(str, enum.Enum):
    TO = "TO"
    CC = "CC"
    BCC = "BCC"
    NONE = "NONE"


class CanonicalMessage(BaseModel):
    """Normalized Graph message — project-plan §10.1."""

    model_config = ConfigDict(frozen=True)

    graph_message_id: str
    graph_conversation_id: str
    from_address: str | None
    sender_address: str | None
    to_addresses: list[str] = Field(default_factory=list)
    cc_addresses: list[str] = Field(default_factory=list)
    received_at: datetime | None
    sent_at: datetime | None
    is_from_user: bool
    subject: str | None
    body_text: str | None
    body_preview: str | None
    user_position: UserRecipientPosition = UserRecipientPosition.NONE
    has_attachments: bool = False
    categories: list[str] = Field(default_factory=list)
    headers: dict[str, Any] = Field(default_factory=dict)


class ThreadSnapshot(BaseModel):
    """Canonical thread snapshot passed to the reducer."""

    model_config = ConfigDict(frozen=True)

    conversation_id: int
    graph_conversation_id: str
    messages: list[CanonicalMessage]

    latest_inbound_ts: datetime | None = None
    latest_outbound_ts: datetime | None = None
    sent_items_cursor_ts: datetime | None = None
    user_sent_last: bool = False
    user_position_on_latest: UserRecipientPosition = UserRecipientPosition.NONE

    unresolved_asks: list[str] = Field(default_factory=list)
    latest_due_at: datetime | None = None
    current_waiting_on: str | None = None

    prior_state: ConversationState = ConversationState.none
    prior_bucket: ConversationBucket | None = None
    prior_task_id: int | None = None
    prior_completion_kind: CompletionKind | None = None
    prior_soft_complete_until: datetime | None = None
    deferred_until: datetime | None = None

    classifications_json: list[dict[str, Any]] = Field(default_factory=list)
