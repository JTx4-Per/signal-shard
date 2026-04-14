"""Reducer output intents: task and category writeback vocab.

See reducer-spec §4 intent vocabularies.
"""

from __future__ import annotations

import enum

from pydantic import BaseModel, ConfigDict

from email_intel.db.models import ConversationBucket


class TaskIntentKind(str, enum.Enum):
    noop = "noop"
    create = "create"
    update_fields = "update_fields"
    move_list = "move_list"
    soft_complete = "soft_complete"
    hard_complete = "hard_complete"
    reopen = "reopen"
    suppress = "suppress"
    dead_letter = "dead_letter"


class TaskIntent(BaseModel):
    """Reducer's task writeback intent. Consumed by writeback layer."""

    model_config = ConfigDict(frozen=True)

    kind: TaskIntentKind
    target_bucket: ConversationBucket | None = None
    operation_key: str = ""
    fields: dict[str, object] = {}


class CategoryIntentKind(str, enum.Enum):
    noop = "noop"
    apply = "apply"
    clear = "clear"
    preserve = "preserve"


class CategoryIntent(BaseModel):
    model_config = ConfigDict(frozen=True)

    kind: CategoryIntentKind
    target_bucket: ConversationBucket | None = None
    operation_key: str = ""


class ReviewFlag(str, enum.Enum):
    none = "none"
    classification = "classification"
    state = "state"
