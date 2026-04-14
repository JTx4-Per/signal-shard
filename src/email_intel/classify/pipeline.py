"""Classification pipeline orchestrator.

v1 rules-only path per project-plan §12.4:
    Stage A → (skip Model) → Stage C → Gate.

The pipeline is deterministic: same snapshot + rule_version ⇒ identical
``ClassifierOutput`` (including ``classifier_input_hash``).
"""

from __future__ import annotations

import hashlib
from typing import Any

import structlog

from email_intel.config import Settings
from email_intel.db.models import ConversationBucket
from email_intel.schemas.classifier import ClassifierOutput
from email_intel.schemas.snapshot import ThreadSnapshot

from email_intel.classify.gate import apply_gate
from email_intel.classify.rules_a import StageAResult, run_stage_a
from email_intel.classify.rules_override import (
    OverrideConfig,
    apply_final_override,
    load_override_config,
)

__all__ = ["classify", "compute_input_hash"]

log = structlog.get_logger(__name__)

_MODEL_VERSION_NONE = "rules-only-v1"
_BODY_EXCERPT = 1024


def compute_input_hash(snapshot: ThreadSnapshot, rule_version: str) -> str:
    """Stable hash of ``(message_ids, subject, body excerpts, rule_version)``.

    No wall-clock; purely derived from the snapshot + rule version.
    """
    h = hashlib.sha256()
    h.update(rule_version.encode("utf-8"))
    h.update(b"\x00")
    for msg in snapshot.messages:
        h.update(msg.graph_message_id.encode("utf-8"))
        h.update(b"\x01")
        h.update((msg.subject or "").encode("utf-8"))
        h.update(b"\x02")
        body = msg.body_text or msg.body_preview or ""
        h.update(body[:_BODY_EXCERPT].encode("utf-8"))
        h.update(b"\x03")
    return h.hexdigest()


def _stage_a_to_output(
    result: StageAResult,
    rule_version: str,
    input_hash: str,
) -> ClassifierOutput:
    sig: dict[str, Any] = result.extracted_signals
    actionable = result.provisional_bucket in {
        ConversationBucket.Act,
        ConversationBucket.Respond,
        ConversationBucket.Delegate,
    }
    # Stage A never authorizes writes directly; gate / reducer decide.
    # But we seed should_create_task when confidence is high and bucket is actionable.
    should_create_task = actionable and result.confidence >= 0.75

    return ClassifierOutput(
        primary_bucket=result.provisional_bucket,
        confidence=float(result.confidence),
        reason_short=result.reason_short or "no-rules-fired",
        should_create_task=should_create_task,
        task_kind=None,
        task_title=None,
        due_at=sig.get("due_at"),
        defer_until=sig.get("defer_until"),
        waiting_on=sig.get("waiting_on"),
        action_owner=sig.get("action_owner"),
        escalate=False,
        newsletter=bool(sig.get("newsletter", False)),
        automated=bool(sig.get("automated", False)),
        delete_candidate=bool(sig.get("delete_candidate", False)),
        unsubscribe_candidate=bool(sig.get("unsubscribe_candidate", False)),
        rule_version=rule_version,
        model_version=_MODEL_VERSION_NONE,
        classifier_input_hash=input_hash,
    )


async def classify(
    snapshot: ThreadSnapshot,
    settings: Settings,
    override_config: OverrideConfig | None = None,
) -> tuple[ClassifierOutput, str | None]:
    """Run Stage A → Stage C → Gate and return ``(output, review_reason)``."""
    rule_version = settings.CLASSIFIER_RULE_VERSION
    input_hash = compute_input_hash(snapshot, rule_version)

    # --- Stage A ---
    stage_a = run_stage_a(snapshot, rule_version=rule_version)
    provisional = _stage_a_to_output(stage_a, rule_version, input_hash)

    # --- Stage B (model) : skipped for v1 (§12.4) ---

    # --- Stage C ---
    cfg = override_config if override_config is not None else load_override_config()
    overridden = apply_final_override(
        provisional=provisional,
        snapshot=snapshot,
        rule_version=rule_version,
        config=cfg,
    )

    # --- Gate ---
    final, review_reason = apply_gate(overridden)

    log.info(
        "classify.done",
        rule_version=rule_version,
        primary_bucket=final.primary_bucket.value if final.primary_bucket else None,
        confidence=final.confidence,
        review_reason=review_reason,
        classifier_input_hash=input_hash,
    )

    return final, review_reason
