"""Integration tests for the rules-only pipeline."""

from __future__ import annotations

import pytest

from email_intel.classify.pipeline import classify
from email_intel.classify.rules_override import OverrideConfig
from email_intel.config import Settings
from email_intel.db.models import ConversationBucket
from email_intel.schemas.snapshot import UserRecipientPosition

from tests.classify._builders import make_message, make_snapshot


@pytest.fixture
def settings() -> Settings:
    return Settings(CLASSIFIER_RULE_VERSION="test-v1")


@pytest.fixture
def empty_overrides() -> OverrideConfig:
    return OverrideConfig(vip_senders=frozenset(), blocked_domains=frozenset())


async def test_pipeline_noreply_newsletter(
    settings: Settings, empty_overrides: OverrideConfig
) -> None:
    msg = make_message(
        from_address="noreply@brand.com",
        subject="Your weekly digest",
        body_text="Offers inside.",
        headers={"List-Unsubscribe": "<mailto:u@brand.com>"},
    )
    snap = make_snapshot(messages=[msg])
    out, reason = await classify(snap, settings, override_config=empty_overrides)
    assert out.primary_bucket == ConversationBucket.DeleteOrUnsubscribe
    assert out.unsubscribe_candidate is True
    assert out.should_create_task is False
    assert reason is None
    assert out.rule_version == "test-v1"
    assert out.classifier_input_hash


async def test_pipeline_direct_question(
    settings: Settings, empty_overrides: OverrideConfig
) -> None:
    msg = make_message(
        from_address="peer@corp.com",
        body_text="Do you have a few minutes to review this?",
        user_position=UserRecipientPosition.TO,
    )
    snap = make_snapshot(messages=[msg])
    out, reason = await classify(snap, settings, override_config=empty_overrides)
    # 0.7 confidence → below write threshold → should_create_task=False + review reason
    assert out.primary_bucket == ConversationBucket.Respond
    assert reason == "below_write_threshold"
    assert out.should_create_task is False


async def test_pipeline_user_sent_last(
    settings: Settings, empty_overrides: OverrideConfig
) -> None:
    inbound = make_message(
        graph_message_id="m1",
        from_address="pm@x.com",
        body_text="Can you send the draft?",
        is_from_user=False,
    )
    outbound = make_message(
        graph_message_id="m2",
        from_address="me@example.com",
        body_text="Draft attached.",
        is_from_user=True,
        user_position=UserRecipientPosition.NONE,
    )
    snap = make_snapshot(messages=[inbound, outbound], user_sent_last=True)
    out, _ = await classify(snap, settings, override_config=empty_overrides)
    assert out.primary_bucket == ConversationBucket.WaitingOn


async def test_pipeline_calendar_accept(
    settings: Settings, empty_overrides: OverrideConfig
) -> None:
    msg = make_message(
        from_address="bob@x.com",
        subject="Accepted: Q2 planning",
        body_text="ok",
    )
    snap = make_snapshot(messages=[msg])
    out, reason = await classify(snap, settings, override_config=empty_overrides)
    assert out.primary_bucket == ConversationBucket.FYI
    assert out.should_create_task is False
    assert reason is None  # conf ≥ 0.9


async def test_pipeline_vip_sender_ambiguous(settings: Settings) -> None:
    msg = make_message(
        from_address="ceo@company.com",
        body_text="Got a sec?",
        user_position=UserRecipientPosition.TO,
    )
    snap = make_snapshot(messages=[msg])
    vip_cfg = OverrideConfig(
        vip_senders=frozenset({"ceo@company.com"}),
        blocked_domains=frozenset(),
    )
    out, reason = await classify(snap, settings, override_config=vip_cfg)
    # Direct-ask-question → Respond @ 0.7 → VIP raises to ≥0.8 → passes gate.
    assert out.primary_bucket == ConversationBucket.Respond
    assert out.confidence >= 0.8
    assert reason is None
    assert "C-vip-allow" in out.reason_short


async def test_pipeline_blocked_domain(settings: Settings) -> None:
    msg = make_message(
        from_address="a@spam.biz",
        body_text="Can you help?",
        user_position=UserRecipientPosition.TO,
    )
    snap = make_snapshot(messages=[msg])
    cfg = OverrideConfig(
        vip_senders=frozenset(),
        blocked_domains=frozenset({"spam.biz"}),
    )
    out, _ = await classify(snap, settings, override_config=cfg)
    assert out.primary_bucket == ConversationBucket.DeleteOrUnsubscribe
    assert out.should_create_task is False


async def test_pipeline_due_date_extraction(
    settings: Settings, empty_overrides: OverrideConfig
) -> None:
    msg = make_message(
        from_address="pm@x.com",
        body_text="Please send the quarterly report by 2026-05-01, thanks.",
        user_position=UserRecipientPosition.TO,
    )
    snap = make_snapshot(messages=[msg])
    out, _ = await classify(snap, settings, override_config=empty_overrides)
    assert out.due_at is not None
    assert out.due_at.year == 2026 and out.due_at.month == 5 and out.due_at.day == 1
    assert out.primary_bucket == ConversationBucket.Act


async def test_pipeline_cc_only_fyi(
    settings: Settings, empty_overrides: OverrideConfig
) -> None:
    msg = make_message(
        from_address="a@corp.com",
        to_addresses=["other@corp.com"],
        cc_addresses=["me@example.com"],
        body_text="For your awareness: update on project.",
        user_position=UserRecipientPosition.CC,
    )
    snap = make_snapshot(messages=[msg])
    out, reason = await classify(snap, settings, override_config=empty_overrides)
    assert out.primary_bucket == ConversationBucket.FYI
    # 0.6 confidence → below_write_threshold
    assert reason == "below_write_threshold"
    assert out.should_create_task is False
