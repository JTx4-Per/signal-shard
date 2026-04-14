"""initial schema — all tables from project-plan §9.

Revision ID: 0001_initial
Revises:
Create Date: 2026-04-13

Hand-written, SQLite-compatible. Mirrors src/email_intel/db/models.py.
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0001_initial"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# Enum string lengths match models.py
_STATES = ("none", "act_open", "respond_open", "delegate_open", "deferred",
           "waiting_on", "done", "needs_review", "fyi_context", "noise_transient")
_BUCKETS = ("Act", "Respond", "Delegate", "Defer", "WaitingOn", "FYI", "DeleteOrUnsubscribe")

_ALLOWED_STATE_BUCKET_SQL = (
    "(open_action_state = 'none' AND open_action_bucket IS NULL) OR "
    "(open_action_state = 'act_open' AND open_action_bucket = 'Act') OR "
    "(open_action_state = 'respond_open' AND open_action_bucket = 'Respond') OR "
    "(open_action_state = 'delegate_open' AND open_action_bucket = 'Delegate') OR "
    "(open_action_state = 'deferred' AND open_action_bucket IN "
    "('Defer','Act','Respond','Delegate','WaitingOn')) OR "
    "(open_action_state = 'waiting_on' AND open_action_bucket = 'WaitingOn') OR "
    "(open_action_state = 'done') OR "
    "(open_action_state = 'needs_review') OR "
    "(open_action_state = 'fyi_context' AND open_action_bucket = 'FYI') OR "
    "(open_action_state = 'noise_transient' AND open_action_bucket = 'DeleteOrUnsubscribe')"
)


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("graph_user_id", sa.String(), nullable=False, unique=True),
        sa.Column("email", sa.String(), nullable=False),
        sa.Column("display_name", sa.String(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True),
                  server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
    )

    op.create_table(
        "mail_folders",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("graph_folder_id", sa.String(), nullable=False),
        sa.Column("well_known_name", sa.String(), nullable=True),
        sa.Column("display_name", sa.String(), nullable=True),
        sa.Column("delta_token", sa.Text(), nullable=True),
        sa.Column("subscription_id", sa.String(), nullable=True),
        sa.Column("subscription_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_sync_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True),
                  server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
    )
    op.create_index("ix_mail_folders_user_wellknown", "mail_folders",
                    ["user_id", "well_known_name"])

    op.create_table(
        "messages",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("graph_message_id", sa.String(), nullable=False, unique=True),
        sa.Column("internet_message_id", sa.String(), nullable=True),
        sa.Column("graph_conversation_id", sa.String(), nullable=False),
        sa.Column("folder_id", sa.Integer(), sa.ForeignKey("mail_folders.id"), nullable=False),
        sa.Column("subject", sa.Text(), nullable=True),
        sa.Column("from_address", sa.String(), nullable=True),
        sa.Column("from_name", sa.String(), nullable=True),
        sa.Column("sender_address", sa.String(), nullable=True),
        sa.Column("to_recipients_json", sa.JSON(), nullable=True),
        sa.Column("cc_recipients_json", sa.JSON(), nullable=True),
        sa.Column("reply_to_json", sa.JSON(), nullable=True),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("is_read", sa.Boolean(), server_default=sa.text("0"), nullable=False),
        sa.Column("importance", sa.String(), nullable=True),
        sa.Column("has_attachments", sa.Boolean(), server_default=sa.text("0"), nullable=False),
        sa.Column("categories_json", sa.JSON(), nullable=True),
        sa.Column("body_text", sa.Text(), nullable=True),
        sa.Column("body_preview", sa.Text(), nullable=True),
        sa.Column("web_link", sa.Text(), nullable=True),
        sa.Column("parent_folder_graph_id", sa.String(), nullable=True),
        sa.Column("etag", sa.String(), nullable=True),
        sa.Column("change_key", sa.String(), nullable=True),
        sa.Column("raw_headers_json", sa.JSON(), nullable=True),
        sa.Column("is_deleted", sa.Boolean(), server_default=sa.text("0"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True),
                  server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
    )
    op.create_index("ix_messages_graph_conversation_id", "messages", ["graph_conversation_id"])
    op.create_index("ix_messages_received_at", "messages", ["received_at"])
    op.create_index("ix_messages_folder_received", "messages", ["folder_id", "received_at"])

    op.create_table(
        "conversations",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("graph_conversation_id", sa.String(), nullable=False),
        sa.Column("canonical_subject", sa.Text(), nullable=True),
        sa.Column("latest_message_id", sa.Integer(),
                  sa.ForeignKey("messages.id"), nullable=True),
        sa.Column("latest_received_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_sender_address", sa.String(), nullable=True),
        sa.Column("last_direction", sa.String(length=16),
                  server_default=sa.text("'none'"), nullable=False),
        sa.Column("open_action_state", sa.String(length=24),
                  server_default=sa.text("'none'"), nullable=False),
        sa.Column("open_action_bucket", sa.String(length=24), nullable=True),
        sa.Column("open_action_task_id", sa.Integer(), nullable=True),
        sa.Column("waiting_on_address", sa.String(), nullable=True),
        sa.Column("deferred_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("due_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("escalate_flag", sa.Boolean(), server_default=sa.text("0"), nullable=False),
        sa.Column("state_review_reason", sa.Text(), nullable=True),
        sa.Column("last_classified_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_reducer_run_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True),
                  server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.UniqueConstraint("user_id", "graph_conversation_id", name="uq_conv_user_graph"),
        sa.CheckConstraint(_ALLOWED_STATE_BUCKET_SQL, name="ck_conv_state_bucket_pair"),
    )
    op.create_index("ix_conversations_open_action_state", "conversations", ["open_action_state"])
    op.create_index("ix_conversations_due_at", "conversations", ["due_at"])
    op.create_index("ix_conversations_waiting_on_address", "conversations", ["waiting_on_address"])
    op.create_index("ix_conversations_deferred_until", "conversations", ["deferred_until"])

    op.create_table(
        "todo_lists",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("graph_todo_list_id", sa.String(), nullable=False, unique=True),
        sa.Column("display_name", sa.String(), nullable=False),
        sa.Column("purpose", sa.String(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True),
                  server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
    )

    op.create_table(
        "todo_tasks",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("conversation_id", sa.Integer(),
                  sa.ForeignKey("conversations.id"), nullable=False),
        sa.Column("action_slot", sa.String(),
                  server_default=sa.text("'primary'"), nullable=False),
        sa.Column("graph_todo_task_id", sa.String(), nullable=False, unique=True),
        sa.Column("graph_todo_list_id", sa.String(), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("status", sa.String(length=16),
                  server_default=sa.text("'notStarted'"), nullable=False),
        sa.Column("completion_kind", sa.String(length=8), nullable=True),
        sa.Column("soft_complete_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("importance", sa.String(), nullable=True),
        sa.Column("due_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("reminder_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("body_markdown", sa.Text(), nullable=True),
        sa.Column("linked_resource_external_id", sa.String(), nullable=True),
        sa.Column("linked_resource_web_url", sa.Text(), nullable=True),
        sa.Column("last_synced_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True),
                  server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
    )
    op.create_index(
        "uq_todo_active_per_conv_slot",
        "todo_tasks",
        ["conversation_id", "action_slot"],
        unique=True,
        sqlite_where=sa.text("status IN ('notStarted','inProgress')"),
    )

    # Now add the deferred FK from conversations → todo_tasks
    with op.batch_alter_table("conversations") as batch_op:
        batch_op.create_foreign_key(
            "fk_conv_open_task", "todo_tasks", ["open_action_task_id"], ["id"]
        )

    op.create_table(
        "classifications",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("conversation_id", sa.Integer(),
                  sa.ForeignKey("conversations.id"), nullable=False),
        sa.Column("message_id", sa.Integer(), sa.ForeignKey("messages.id"), nullable=False),
        sa.Column("model_version", sa.String(), nullable=True),
        sa.Column("rule_version", sa.String(), nullable=True),
        sa.Column("primary_bucket", sa.String(length=24), nullable=True),
        sa.Column("confidence", sa.Float(), nullable=True),
        sa.Column("extracted_due_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("extracted_defer_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("extracted_waiting_on_address", sa.String(), nullable=True),
        sa.Column("extracted_action_owner", sa.String(), nullable=True),
        sa.Column("extracted_escalate_flag", sa.Boolean(),
                  server_default=sa.text("0"), nullable=False),
        sa.Column("extracted_newsletter_flag", sa.Boolean(),
                  server_default=sa.text("0"), nullable=False),
        sa.Column("extracted_bulk_flag", sa.Boolean(),
                  server_default=sa.text("0"), nullable=False),
        sa.Column("should_create_task", sa.Boolean(),
                  server_default=sa.text("0"), nullable=False),
        sa.Column("reason_short", sa.Text(), nullable=True),
        sa.Column("reasoning_json", sa.JSON(), nullable=True),
        sa.Column("classifier_input_hash", sa.String(), nullable=True),
        sa.Column("classification_review_reason", sa.Text(), nullable=True),
        sa.Column("review_status", sa.String(length=24),
                  server_default=sa.text("'none'"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
    )
    op.create_index("ix_classifications_conv_created", "classifications",
                    ["conversation_id", "created_at"])
    op.create_index("ix_classifications_message_id", "classifications", ["message_id"])
    op.create_index(
        "ix_classifications_review_open",
        "classifications",
        ["review_status"],
        sqlite_where=sa.text("review_status != 'none'"),
    )

    op.create_table(
        "conversation_events",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("conversation_id", sa.Integer(),
                  sa.ForeignKey("conversations.id"), nullable=False),
        sa.Column("event_type", sa.String(length=40), nullable=False),
        sa.Column("before_state", sa.String(length=24), nullable=True),
        sa.Column("after_state", sa.String(length=24), nullable=True),
        sa.Column("payload_json", sa.JSON(), nullable=True),
        sa.Column("actor", sa.String(length=16),
                  server_default=sa.text("'system'"), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True),
                  server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
    )
    op.create_index("ix_conv_events_conv_occurred", "conversation_events",
                    ["conversation_id", "occurred_at"])
    op.create_index("ix_conv_events_type_occurred", "conversation_events",
                    ["event_type", "occurred_at"])

    op.create_table(
        "operation_keys",
        sa.Column("key", sa.String(), primary_key=True),
        sa.Column("operation_type", sa.String(length=32), nullable=False),
        sa.Column("conversation_id", sa.Integer(),
                  sa.ForeignKey("conversations.id"), nullable=False),
        sa.Column("payload_hash", sa.String(), nullable=True),
        sa.Column("result_json", sa.JSON(), nullable=True),
        sa.Column("first_applied_at", sa.DateTime(timezone=True),
                  server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
    )

    op.create_table(
        "sync_events",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("source_type", sa.String(), nullable=False),
        sa.Column("source_id", sa.String(), nullable=True),
        sa.Column("event_type", sa.String(), nullable=False),
        sa.Column("cursor_or_token", sa.Text(), nullable=True),
        sa.Column("payload_json", sa.JSON(), nullable=True),
        sa.Column("processed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
    )

    op.create_table(
        "audit_log",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("entity_type", sa.String(), nullable=False),
        sa.Column("entity_id", sa.String(), nullable=False),
        sa.Column("action", sa.String(), nullable=False),
        sa.Column("before_json", sa.JSON(), nullable=True),
        sa.Column("after_json", sa.JSON(), nullable=True),
        sa.Column("source", sa.String(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("audit_log")
    op.drop_table("sync_events")
    op.drop_table("operation_keys")
    op.drop_index("ix_conv_events_type_occurred", table_name="conversation_events")
    op.drop_index("ix_conv_events_conv_occurred", table_name="conversation_events")
    op.drop_table("conversation_events")
    op.drop_index("ix_classifications_review_open", table_name="classifications")
    op.drop_index("ix_classifications_message_id", table_name="classifications")
    op.drop_index("ix_classifications_conv_created", table_name="classifications")
    op.drop_table("classifications")
    op.drop_index("uq_todo_active_per_conv_slot", table_name="todo_tasks")
    op.drop_table("todo_tasks")
    op.drop_table("todo_lists")
    op.drop_index("ix_conversations_deferred_until", table_name="conversations")
    op.drop_index("ix_conversations_waiting_on_address", table_name="conversations")
    op.drop_index("ix_conversations_due_at", table_name="conversations")
    op.drop_index("ix_conversations_open_action_state", table_name="conversations")
    op.drop_table("conversations")
    op.drop_index("ix_messages_folder_received", table_name="messages")
    op.drop_index("ix_messages_received_at", table_name="messages")
    op.drop_index("ix_messages_graph_conversation_id", table_name="messages")
    op.drop_table("messages")
    op.drop_index("ix_mail_folders_user_wellknown", table_name="mail_folders")
    op.drop_table("mail_folders")
    op.drop_table("users")
