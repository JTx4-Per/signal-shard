"""ORM models — every table from project-plan §9.

All timestamps are timezone-aware. Enums are stored as SQLAlchemy String Enum
columns for portability (SQLite has no native enum). The reducer is the sole
writer to `conversations.open_action_*`; classification writes evidence only.
"""

from __future__ import annotations

import enum
from datetime import datetime
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    DateTime,
    Enum as SAEnum,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    event,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from email_intel.db.base import Base


# ---------- Enums (persisted as strings) ----------


class Direction(str, enum.Enum):
    inbound = "inbound"
    outbound = "outbound"
    mixed = "mixed"
    none = "none"


class ConversationState(str, enum.Enum):
    none = "none"
    act_open = "act_open"
    respond_open = "respond_open"
    delegate_open = "delegate_open"
    deferred = "deferred"
    waiting_on = "waiting_on"
    done = "done"
    needs_review = "needs_review"
    fyi_context = "fyi_context"
    noise_transient = "noise_transient"


class ConversationBucket(str, enum.Enum):
    Act = "Act"
    Respond = "Respond"
    Delegate = "Delegate"
    Defer = "Defer"
    WaitingOn = "WaitingOn"
    FYI = "FYI"
    DeleteOrUnsubscribe = "DeleteOrUnsubscribe"


class TaskStatus(str, enum.Enum):
    notStarted = "notStarted"
    inProgress = "inProgress"
    completed = "completed"


class CompletionKind(str, enum.Enum):
    soft = "soft"
    hard = "hard"


class ConversationEventType(str, enum.Enum):
    message_added = "message_added"
    classified = "classified"
    state_changed = "state_changed"
    task_created = "task_created"
    task_updated = "task_updated"
    task_soft_complete = "task_soft_complete"
    task_hard_complete = "task_hard_complete"
    task_reopened = "task_reopened"
    override_applied = "override_applied"
    needs_review_raised = "needs_review_raised"
    needs_review_resolved = "needs_review_resolved"
    reducer_ran_noop = "reducer_ran_noop"


class EventActor(str, enum.Enum):
    system = "system"
    reducer = "reducer"
    user_override = "user_override"


class OperationType(str, enum.Enum):
    task_create = "task_create"
    task_update = "task_update"
    task_complete = "task_complete"
    classification = "classification"
    category_patch = "category_patch"


class ReviewStatus(str, enum.Enum):
    none = "none"
    pending = "pending"
    resolved_accept = "resolved_accept"
    resolved_override = "resolved_override"


# ---------- Common column helpers ----------


def _ts_col(**kw: Any) -> Mapped[datetime]:
    return mapped_column(DateTime(timezone=True), **kw)


# ---------- Tables ----------


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    graph_user_id: Mapped[str] = mapped_column(String, unique=True, nullable=False)
    email: Mapped[str] = mapped_column(String, nullable=False)
    display_name: Mapped[str | None] = mapped_column(String, nullable=True)
    created_at: Mapped[datetime] = _ts_col(nullable=False, server_default=text("CURRENT_TIMESTAMP"))
    updated_at: Mapped[datetime] = _ts_col(nullable=False, server_default=text("CURRENT_TIMESTAMP"))


class MailFolder(Base):
    __tablename__ = "mail_folders"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    graph_folder_id: Mapped[str] = mapped_column(String, nullable=False)
    well_known_name: Mapped[str | None] = mapped_column(String, nullable=True)
    display_name: Mapped[str | None] = mapped_column(String, nullable=True)
    delta_token: Mapped[str | None] = mapped_column(Text, nullable=True)
    subscription_id: Mapped[str | None] = mapped_column(String, nullable=True)
    subscription_expires_at: Mapped[datetime | None] = _ts_col(nullable=True)
    last_sync_at: Mapped[datetime | None] = _ts_col(nullable=True)
    created_at: Mapped[datetime] = _ts_col(nullable=False, server_default=text("CURRENT_TIMESTAMP"))
    updated_at: Mapped[datetime] = _ts_col(nullable=False, server_default=text("CURRENT_TIMESTAMP"))

    __table_args__ = (
        Index("ix_mail_folders_user_wellknown", "user_id", "well_known_name"),
    )


class Message(Base):
    __tablename__ = "messages"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    graph_message_id: Mapped[str] = mapped_column(String, unique=True, nullable=False)
    internet_message_id: Mapped[str | None] = mapped_column(String, nullable=True)
    graph_conversation_id: Mapped[str] = mapped_column(String, nullable=False, index=True)
    folder_id: Mapped[int] = mapped_column(ForeignKey("mail_folders.id"), nullable=False)
    subject: Mapped[str | None] = mapped_column(Text, nullable=True)
    from_address: Mapped[str | None] = mapped_column(String, nullable=True)
    from_name: Mapped[str | None] = mapped_column(String, nullable=True)
    sender_address: Mapped[str | None] = mapped_column(String, nullable=True)
    to_recipients_json: Mapped[dict[str, Any] | list[Any] | None] = mapped_column(JSON, nullable=True)
    cc_recipients_json: Mapped[dict[str, Any] | list[Any] | None] = mapped_column(JSON, nullable=True)
    reply_to_json: Mapped[dict[str, Any] | list[Any] | None] = mapped_column(JSON, nullable=True)
    received_at: Mapped[datetime | None] = _ts_col(nullable=True, index=True)
    sent_at: Mapped[datetime | None] = _ts_col(nullable=True)
    is_read: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("0"))
    importance: Mapped[str | None] = mapped_column(String, nullable=True)
    has_attachments: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("0"))
    categories_json: Mapped[dict[str, Any] | list[Any] | None] = mapped_column(JSON, nullable=True)
    body_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    body_preview: Mapped[str | None] = mapped_column(Text, nullable=True)
    web_link: Mapped[str | None] = mapped_column(Text, nullable=True)
    parent_folder_graph_id: Mapped[str | None] = mapped_column(String, nullable=True)
    etag: Mapped[str | None] = mapped_column(String, nullable=True)
    change_key: Mapped[str | None] = mapped_column(String, nullable=True)
    raw_headers_json: Mapped[dict[str, Any] | list[Any] | None] = mapped_column(JSON, nullable=True)
    is_deleted: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("0"))
    created_at: Mapped[datetime] = _ts_col(nullable=False, server_default=text("CURRENT_TIMESTAMP"))
    updated_at: Mapped[datetime] = _ts_col(nullable=False, server_default=text("CURRENT_TIMESTAMP"))

    __table_args__ = (
        Index("ix_messages_folder_received", "folder_id", "received_at"),
    )


# The allowed (state, bucket) pairs per project-plan §9.
_ALLOWED_STATE_BUCKET_SQL = (
    "(open_action_state = 'none' AND open_action_bucket IS NULL) OR "
    "(open_action_state = 'act_open' AND open_action_bucket = 'Act') OR "
    "(open_action_state = 'respond_open' AND open_action_bucket = 'Respond') OR "
    "(open_action_state = 'delegate_open' AND open_action_bucket = 'Delegate') OR "
    "(open_action_state = 'deferred' AND open_action_bucket IN ('Defer','Act','Respond','Delegate','WaitingOn')) OR "
    "(open_action_state = 'waiting_on' AND open_action_bucket = 'WaitingOn') OR "
    "(open_action_state = 'done') OR "
    "(open_action_state = 'needs_review') OR "
    "(open_action_state = 'fyi_context' AND open_action_bucket = 'FYI') OR "
    "(open_action_state = 'noise_transient' AND open_action_bucket = 'DeleteOrUnsubscribe')"
)


class Conversation(Base):
    __tablename__ = "conversations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    graph_conversation_id: Mapped[str] = mapped_column(String, nullable=False)
    canonical_subject: Mapped[str | None] = mapped_column(Text, nullable=True)
    latest_message_id: Mapped[int | None] = mapped_column(
        ForeignKey("messages.id"), nullable=True
    )
    latest_received_at: Mapped[datetime | None] = _ts_col(nullable=True)
    last_sender_address: Mapped[str | None] = mapped_column(String, nullable=True)
    last_direction: Mapped[Direction] = mapped_column(
        SAEnum(Direction, name="direction_enum", native_enum=False, length=16),
        nullable=False,
        server_default=text("'none'"),
    )
    open_action_state: Mapped[ConversationState] = mapped_column(
        SAEnum(ConversationState, name="conversation_state_enum", native_enum=False, length=24),
        nullable=False,
        server_default=text("'none'"),
        index=True,
    )
    open_action_bucket: Mapped[ConversationBucket | None] = mapped_column(
        SAEnum(ConversationBucket, name="conversation_bucket_enum", native_enum=False, length=24),
        nullable=True,
    )
    open_action_task_id: Mapped[int | None] = mapped_column(
        ForeignKey("todo_tasks.id", use_alter=True, name="fk_conv_open_task"), nullable=True
    )
    waiting_on_address: Mapped[str | None] = mapped_column(String, nullable=True, index=True)
    deferred_until: Mapped[datetime | None] = _ts_col(nullable=True, index=True)
    due_at: Mapped[datetime | None] = _ts_col(nullable=True, index=True)
    escalate_flag: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("0"))
    state_review_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    last_classified_at: Mapped[datetime | None] = _ts_col(nullable=True)
    last_reducer_run_at: Mapped[datetime | None] = _ts_col(nullable=True)
    created_at: Mapped[datetime] = _ts_col(nullable=False, server_default=text("CURRENT_TIMESTAMP"))
    updated_at: Mapped[datetime] = _ts_col(nullable=False, server_default=text("CURRENT_TIMESTAMP"))

    __table_args__ = (
        UniqueConstraint("user_id", "graph_conversation_id", name="uq_conv_user_graph"),
        CheckConstraint(_ALLOWED_STATE_BUCKET_SQL, name="ck_conv_state_bucket_pair"),
    )


class Classification(Base):
    __tablename__ = "classifications"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    conversation_id: Mapped[int] = mapped_column(ForeignKey("conversations.id"), nullable=False)
    message_id: Mapped[int] = mapped_column(ForeignKey("messages.id"), nullable=False, index=True)
    model_version: Mapped[str | None] = mapped_column(String, nullable=True)
    rule_version: Mapped[str | None] = mapped_column(String, nullable=True)
    primary_bucket: Mapped[ConversationBucket | None] = mapped_column(
        SAEnum(ConversationBucket, name="conversation_bucket_enum", native_enum=False, length=24),
        nullable=True,
    )
    confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    extracted_due_at: Mapped[datetime | None] = _ts_col(nullable=True)
    extracted_defer_until: Mapped[datetime | None] = _ts_col(nullable=True)
    extracted_waiting_on_address: Mapped[str | None] = mapped_column(String, nullable=True)
    extracted_action_owner: Mapped[str | None] = mapped_column(String, nullable=True)
    extracted_escalate_flag: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("0"))
    extracted_newsletter_flag: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("0"))
    extracted_bulk_flag: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("0"))
    should_create_task: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("0"))
    reason_short: Mapped[str | None] = mapped_column(Text, nullable=True)
    reasoning_json: Mapped[dict[str, Any] | list[Any] | None] = mapped_column(JSON, nullable=True)
    classifier_input_hash: Mapped[str | None] = mapped_column(String, nullable=True)
    classification_review_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    review_status: Mapped[ReviewStatus] = mapped_column(
        SAEnum(ReviewStatus, name="review_status_enum", native_enum=False, length=24),
        nullable=False,
        server_default=text("'none'"),
    )
    created_at: Mapped[datetime] = _ts_col(nullable=False, server_default=text("CURRENT_TIMESTAMP"))

    __table_args__ = (
        Index(
            "ix_classifications_conv_created",
            "conversation_id",
            "created_at",
        ),
        Index(
            "ix_classifications_review_open",
            "review_status",
            sqlite_where=text("review_status != 'none'"),
        ),
    )


class TodoList(Base):
    __tablename__ = "todo_lists"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    graph_todo_list_id: Mapped[str] = mapped_column(String, unique=True, nullable=False)
    display_name: Mapped[str] = mapped_column(String, nullable=False)
    purpose: Mapped[str | None] = mapped_column(String, nullable=True)
    created_at: Mapped[datetime] = _ts_col(nullable=False, server_default=text("CURRENT_TIMESTAMP"))
    updated_at: Mapped[datetime] = _ts_col(nullable=False, server_default=text("CURRENT_TIMESTAMP"))


class TodoTask(Base):
    __tablename__ = "todo_tasks"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    conversation_id: Mapped[int] = mapped_column(ForeignKey("conversations.id"), nullable=False)
    action_slot: Mapped[str] = mapped_column(String, nullable=False, server_default=text("'primary'"))
    graph_todo_task_id: Mapped[str] = mapped_column(String, unique=True, nullable=False)
    graph_todo_list_id: Mapped[str] = mapped_column(String, nullable=False)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[TaskStatus] = mapped_column(
        SAEnum(TaskStatus, name="task_status_enum", native_enum=False, length=16),
        nullable=False,
        server_default=text("'notStarted'"),
    )
    completion_kind: Mapped[CompletionKind | None] = mapped_column(
        SAEnum(CompletionKind, name="completion_kind_enum", native_enum=False, length=8),
        nullable=True,
    )
    soft_complete_until: Mapped[datetime | None] = _ts_col(nullable=True)
    importance: Mapped[str | None] = mapped_column(String, nullable=True)
    due_at: Mapped[datetime | None] = _ts_col(nullable=True)
    reminder_at: Mapped[datetime | None] = _ts_col(nullable=True)
    body_markdown: Mapped[str | None] = mapped_column(Text, nullable=True)
    linked_resource_external_id: Mapped[str | None] = mapped_column(String, nullable=True)
    linked_resource_web_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    last_synced_at: Mapped[datetime | None] = _ts_col(nullable=True)
    created_at: Mapped[datetime] = _ts_col(nullable=False, server_default=text("CURRENT_TIMESTAMP"))
    updated_at: Mapped[datetime] = _ts_col(nullable=False, server_default=text("CURRENT_TIMESTAMP"))

    __table_args__ = (
        Index(
            "uq_todo_active_per_conv_slot",
            "conversation_id",
            "action_slot",
            unique=True,
            sqlite_where=text("status IN ('notStarted','inProgress')"),
        ),
    )


class ConversationEvent(Base):
    """Append-only log. I8 — updates/deletes are rejected at the ORM layer."""

    __tablename__ = "conversation_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    conversation_id: Mapped[int] = mapped_column(ForeignKey("conversations.id"), nullable=False)
    event_type: Mapped[ConversationEventType] = mapped_column(
        SAEnum(ConversationEventType, name="conversation_event_type_enum", native_enum=False, length=40),
        nullable=False,
    )
    before_state: Mapped[ConversationState | None] = mapped_column(
        SAEnum(ConversationState, name="conversation_state_enum", native_enum=False, length=24),
        nullable=True,
    )
    after_state: Mapped[ConversationState | None] = mapped_column(
        SAEnum(ConversationState, name="conversation_state_enum", native_enum=False, length=24),
        nullable=True,
    )
    payload_json: Mapped[dict[str, Any] | list[Any] | None] = mapped_column(JSON, nullable=True)
    actor: Mapped[EventActor] = mapped_column(
        SAEnum(EventActor, name="event_actor_enum", native_enum=False, length=16),
        nullable=False,
        server_default=text("'system'"),
    )
    occurred_at: Mapped[datetime] = _ts_col(nullable=False, server_default=text("CURRENT_TIMESTAMP"))
    created_at: Mapped[datetime] = _ts_col(nullable=False, server_default=text("CURRENT_TIMESTAMP"))

    __table_args__ = (
        Index("ix_conv_events_conv_occurred", "conversation_id", "occurred_at"),
        Index("ix_conv_events_type_occurred", "event_type", "occurred_at"),
    )


@event.listens_for(ConversationEvent, "before_update", propagate=True)
def _reject_event_update(_mapper: Any, _conn: Any, _target: Any) -> None:
    raise RuntimeError("conversation_events is append-only (Invariant I8)")


@event.listens_for(ConversationEvent, "before_delete", propagate=True)
def _reject_event_delete(_mapper: Any, _conn: Any, _target: Any) -> None:
    raise RuntimeError("conversation_events is append-only (Invariant I8)")


class OperationKey(Base):
    __tablename__ = "operation_keys"

    key: Mapped[str] = mapped_column(String, primary_key=True)
    operation_type: Mapped[OperationType] = mapped_column(
        SAEnum(OperationType, name="operation_type_enum", native_enum=False, length=32),
        nullable=False,
    )
    conversation_id: Mapped[int] = mapped_column(ForeignKey("conversations.id"), nullable=False)
    payload_hash: Mapped[str | None] = mapped_column(String, nullable=True)
    result_json: Mapped[dict[str, Any] | list[Any] | None] = mapped_column(JSON, nullable=True)
    first_applied_at: Mapped[datetime] = _ts_col(
        nullable=False, server_default=text("CURRENT_TIMESTAMP")
    )


class SyncEvent(Base):
    __tablename__ = "sync_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    source_type: Mapped[str] = mapped_column(String, nullable=False)
    source_id: Mapped[str | None] = mapped_column(String, nullable=True)
    event_type: Mapped[str] = mapped_column(String, nullable=False)
    cursor_or_token: Mapped[str | None] = mapped_column(Text, nullable=True)
    payload_json: Mapped[dict[str, Any] | list[Any] | None] = mapped_column(JSON, nullable=True)
    processed_at: Mapped[datetime | None] = _ts_col(nullable=True)
    created_at: Mapped[datetime] = _ts_col(nullable=False, server_default=text("CURRENT_TIMESTAMP"))


class AuditLog(Base):
    __tablename__ = "audit_log"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    entity_type: Mapped[str] = mapped_column(String, nullable=False)
    entity_id: Mapped[str] = mapped_column(String, nullable=False)
    action: Mapped[str] = mapped_column(String, nullable=False)
    before_json: Mapped[dict[str, Any] | list[Any] | None] = mapped_column(JSON, nullable=True)
    after_json: Mapped[dict[str, Any] | list[Any] | None] = mapped_column(JSON, nullable=True)
    source: Mapped[str | None] = mapped_column(String, nullable=True)
    created_at: Mapped[datetime] = _ts_col(nullable=False, server_default=text("CURRENT_TIMESTAMP"))


# Keep relationship hook to avoid unused-import warning on relationship()
_ = relationship
