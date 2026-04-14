"""Confidence gate. See project-plan §12.3.

Three branches:
  - ``conf >= write_threshold``   → pass, no review reason.
  - ``review_threshold <= conf``  → ``should_create_task=False``,
                                    ``"below_write_threshold"``.
  - ``conf < review_threshold``   → ``should_create_task=False``,
                                    ``"below_review_threshold"``.
"""

from __future__ import annotations

import structlog

from email_intel.schemas.classifier import ClassifierOutput

__all__ = ["apply_gate"]

log = structlog.get_logger(__name__)


def _with(output: ClassifierOutput, **changes: object) -> ClassifierOutput:
    base = output.model_dump()
    base.update(changes)
    return ClassifierOutput(**base)


def apply_gate(
    classification: ClassifierOutput,
    write_threshold: float = 0.75,
    review_threshold: float = 0.5,
) -> tuple[ClassifierOutput, str | None]:
    """Return ``(classification, review_reason_or_None)``."""
    conf = classification.confidence

    if conf >= write_threshold:
        return classification, None

    if conf >= review_threshold:
        log.info(
            "gate.below_write_threshold",
            confidence=conf,
            write_threshold=write_threshold,
        )
        return (
            _with(classification, should_create_task=False),
            "below_write_threshold",
        )

    log.info(
        "gate.below_review_threshold",
        confidence=conf,
        review_threshold=review_threshold,
    )
    return (
        _with(classification, should_create_task=False),
        "below_review_threshold",
    )
