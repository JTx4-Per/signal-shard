"""ClassifierOutput contract. See project-plan §12.2."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from email_intel.db.models import ConversationBucket


class ClassifierOutput(BaseModel):
    """Strict JSON contract returned by the classification pipeline.

    Evidence-only. Never writes tasks or categories directly (I1).
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    primary_bucket: ConversationBucket | None = None
    confidence: float = Field(ge=0.0, le=1.0)
    reason_short: str
    should_create_task: bool = False
    task_kind: str | None = None
    task_title: str | None = None
    due_at: datetime | None = None
    defer_until: datetime | None = None
    waiting_on: str | None = None
    action_owner: str | None = None
    escalate: bool = False
    newsletter: bool = False
    automated: bool = False
    delete_candidate: bool = False
    unsubscribe_candidate: bool = False

    rule_version: str
    model_version: str
    classifier_input_hash: str
