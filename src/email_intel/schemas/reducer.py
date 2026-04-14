"""Reducer input/output contracts. See reducer-spec §1 (G1, G2)."""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

from email_intel.db.models import ConversationBucket, ConversationEventType, ConversationState
from email_intel.schemas.events import Evidence
from email_intel.schemas.intents import CategoryIntent, ReviewFlag, TaskIntent
from email_intel.schemas.snapshot import ThreadSnapshot


class ManualOverride(BaseModel):
    model_config = ConfigDict(frozen=True)

    target_state: ConversationState
    target_bucket: ConversationBucket | None = None


class ReducerInput(BaseModel):
    model_config = ConfigDict(frozen=True, arbitrary_types_allowed=True)

    snapshot: ThreadSnapshot
    prior_state: ConversationState
    prior_bucket: ConversationBucket | None = None
    now: datetime
    evidence_set: frozenset[Evidence]
    manual_override: ManualOverride | None = None


class ReducerEventRecord(BaseModel):
    model_config = ConfigDict(frozen=True)

    event_type: ConversationEventType
    payload: dict[str, Any] = Field(default_factory=dict)


_TRANSITION_ID_RE = re.compile(r"^T\d{3}$")


class ReducerResult(BaseModel):
    """G2 · reducer output envelope."""

    model_config = ConfigDict(frozen=True, arbitrary_types_allowed=True)

    next_state: ConversationState
    next_bucket: ConversationBucket | None
    task_intent: TaskIntent
    category_intent: CategoryIntent
    events: list[ReducerEventRecord] = Field(default_factory=list)
    operation_keys: list[str] = Field(default_factory=list)
    review_flag: ReviewFlag = ReviewFlag.none
    transition_id: str
    suppressed_evidence: list[Evidence] = Field(default_factory=list)

    @field_validator("transition_id")
    @classmethod
    def _check_transition_id(cls, v: str) -> str:
        if not _TRANSITION_ID_RE.match(v):
            raise ValueError(f"transition_id must match T### (got {v!r})")
        return v
