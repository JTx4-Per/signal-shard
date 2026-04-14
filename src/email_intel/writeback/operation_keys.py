"""Idempotency keys. See project-plan §9 `operation_keys`."""

from __future__ import annotations

import hashlib


def task_create_key(conversation_id: str, action_slot: str, task_kind: str) -> str:
    """`sha256(conversation_id + action_slot + task_kind)`."""
    payload = f"{conversation_id}{action_slot}{task_kind}".encode()
    return hashlib.sha256(payload).hexdigest()


def classification_key(message_id: str, model_version: str, rule_version: str) -> str:
    payload = f"{message_id}{model_version}{rule_version}".encode()
    return hashlib.sha256(payload).hexdigest()


def writeback_key(
    conversation_id: str, intent: str, target_state: str, action_slot: str
) -> str:
    payload = f"{conversation_id}{intent}{target_state}{action_slot}".encode()
    return hashlib.sha256(payload).hexdigest()
