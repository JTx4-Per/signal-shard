"""Determinism — same snapshot ⇒ identical ClassifierOutput & hash."""

from __future__ import annotations

from email_intel.classify.pipeline import classify, compute_input_hash
from email_intel.classify.rules_override import OverrideConfig
from email_intel.config import Settings
from email_intel.schemas.snapshot import UserRecipientPosition

from tests.classify._builders import make_message, make_snapshot


async def test_pipeline_is_deterministic() -> None:
    settings = Settings(CLASSIFIER_RULE_VERSION="det-v1")
    cfg = OverrideConfig(frozenset(), frozenset())
    msg = make_message(
        from_address="pm@x.com",
        body_text="Please review the doc by 2026-05-01.",
        user_position=UserRecipientPosition.TO,
    )
    snap = make_snapshot(messages=[msg])

    out_a, reason_a = await classify(snap, settings, override_config=cfg)
    out_b, reason_b = await classify(snap, settings, override_config=cfg)

    assert out_a == out_b
    assert reason_a == reason_b
    assert out_a.classifier_input_hash == out_b.classifier_input_hash


async def test_hash_stable_independent_of_object_identity() -> None:
    msg = make_message(graph_message_id="m1", body_text="hello")
    snap = make_snapshot(messages=[msg])
    h1 = compute_input_hash(snap, "v1")

    msg2 = make_message(graph_message_id="m1", body_text="hello")
    snap2 = make_snapshot(messages=[msg2])
    h2 = compute_input_hash(snap2, "v1")
    assert h1 == h2


async def test_hash_changes_with_rule_version() -> None:
    msg = make_message(graph_message_id="m1", body_text="hello")
    snap = make_snapshot(messages=[msg])
    assert compute_input_hash(snap, "v1") != compute_input_hash(snap, "v2")


async def test_hash_changes_with_body() -> None:
    snap_a = make_snapshot(messages=[make_message(body_text="A")])
    snap_b = make_snapshot(messages=[make_message(body_text="B")])
    assert compute_input_hash(snap_a, "v1") != compute_input_hash(snap_b, "v1")
