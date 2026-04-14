"""Confidence gate tests — all three branches."""

from __future__ import annotations

from email_intel.classify.gate import apply_gate
from email_intel.db.models import ConversationBucket
from email_intel.schemas.classifier import ClassifierOutput


def _mk(conf: float, should_create_task: bool = True) -> ClassifierOutput:
    return ClassifierOutput(
        primary_bucket=ConversationBucket.Respond,
        confidence=conf,
        reason_short="r",
        should_create_task=should_create_task,
        rule_version="v1",
        model_version="rules-only-v1",
        classifier_input_hash="h",
    )


def test_gate_high_confidence_passes_through() -> None:
    inp = _mk(0.9)
    out, reason = apply_gate(inp, write_threshold=0.75, review_threshold=0.5)
    assert reason is None
    assert out.should_create_task is True
    assert out == inp


def test_gate_at_write_threshold_passes_through() -> None:
    inp = _mk(0.75)
    out, reason = apply_gate(inp)
    assert reason is None
    assert out.should_create_task is True


def test_gate_below_write_zeroes_task_flag() -> None:
    inp = _mk(0.6)
    out, reason = apply_gate(inp)
    assert reason == "below_write_threshold"
    assert out.should_create_task is False
    assert out.primary_bucket == ConversationBucket.Respond


def test_gate_below_review_threshold_flagged() -> None:
    inp = _mk(0.3)
    out, reason = apply_gate(inp)
    assert reason == "below_review_threshold"
    assert out.should_create_task is False


def test_gate_at_review_threshold_is_below_write() -> None:
    inp = _mk(0.5)
    out, reason = apply_gate(inp)
    assert reason == "below_write_threshold"
    assert out.should_create_task is False


def test_gate_custom_thresholds() -> None:
    inp = _mk(0.4)
    _, reason = apply_gate(inp, write_threshold=0.9, review_threshold=0.3)
    assert reason == "below_write_threshold"
    _, reason2 = apply_gate(inp, write_threshold=0.9, review_threshold=0.45)
    assert reason2 == "below_review_threshold"
