"""One test per Stage A rule."""

from __future__ import annotations

from datetime import datetime, timezone

from email_intel.classify.rules_a import run_stage_a
from email_intel.db.models import ConversationBucket
from email_intel.schemas.snapshot import UserRecipientPosition

from tests.classify._builders import make_message, make_snapshot

RV = "test-v1"


def test_a_noreply_sender_fyi_high_conf() -> None:
    msg = make_message(
        from_address="noreply@service.com", subject="Receipt", body_text="thanks"
    )
    snap = make_snapshot(messages=[msg])
    result = run_stage_a(snap, rule_version=RV)
    assert "A-noreply-sender" in result.matched_rules
    assert result.provisional_bucket == ConversationBucket.FYI
    assert result.confidence >= 0.95
    assert result.extracted_signals["automated"] is True


def test_a_noreply_with_list_unsubscribe_sets_unsubscribe_signal() -> None:
    msg = make_message(
        from_address="no-reply@svc.com",
        headers={"List-Unsubscribe": "<mailto:u@svc.com>"},
    )
    snap = make_snapshot(messages=[msg])
    result = run_stage_a(snap, rule_version=RV)
    assert "A-noreply-sender" in result.matched_rules
    assert "A-list-unsubscribe" in result.matched_rules
    assert result.extracted_signals["unsubscribe_candidate"] is True


def test_a_list_unsubscribe_forces_delete_bucket() -> None:
    msg = make_message(
        from_address="someone@bulk.com",
        headers={"List-Unsubscribe": "<mailto:u@bulk.com>"},
    )
    snap = make_snapshot(messages=[msg])
    result = run_stage_a(snap, rule_version=RV)
    assert "A-list-unsubscribe" in result.matched_rules
    assert result.provisional_bucket == ConversationBucket.DeleteOrUnsubscribe
    assert 0.85 <= result.confidence <= 0.95
    assert result.extracted_signals["unsubscribe_candidate"] is True


def test_a_bulk_sender_marketing() -> None:
    msg = make_message(from_address="marketing@brand.com", subject="Deals")
    snap = make_snapshot(messages=[msg])
    result = run_stage_a(snap, rule_version=RV)
    assert "A-bulk-sender" in result.matched_rules
    assert result.provisional_bucket == ConversationBucket.DeleteOrUnsubscribe
    assert result.extracted_signals["newsletter"] is True
    assert 0.75 <= result.confidence < 0.9


def test_a_calendar_accept_fyi() -> None:
    msg = make_message(
        from_address="bob@x.com", subject="Accepted: Q2 planning", body_text="see u"
    )
    snap = make_snapshot(messages=[msg])
    result = run_stage_a(snap, rule_version=RV)
    assert "A-calendar-accept" in result.matched_rules
    assert result.provisional_bucket == ConversationBucket.FYI
    assert result.confidence >= 0.9


def test_a_automation_system_github() -> None:
    msg = make_message(
        from_address="notifications@github.com",
        subject="[repo] PR opened",
        body_text="A new pull request has been opened. View it on GitHub.",
    )
    snap = make_snapshot(messages=[msg])
    result = run_stage_a(snap, rule_version=RV)
    assert "A-automation-system" in result.matched_rules
    assert result.extracted_signals["automated"] is True


def test_a_direct_ask_question_to_user() -> None:
    msg = make_message(
        from_address="peer@x.com",
        body_text="Hey, do you have time to chat about the project?",
        user_position=UserRecipientPosition.TO,
    )
    snap = make_snapshot(messages=[msg])
    result = run_stage_a(snap, rule_version=RV)
    assert "A-direct-ask-question" in result.matched_rules
    assert result.provisional_bucket == ConversationBucket.Respond
    assert 0.65 <= result.confidence <= 0.8


def test_a_direct_ask_verb_imperative() -> None:
    msg = make_message(
        from_address="boss@x.com",
        body_text="Please prepare the Q2 summary deck by end of week.",
        user_position=UserRecipientPosition.TO,
    )
    snap = make_snapshot(messages=[msg])
    result = run_stage_a(snap, rule_version=RV)
    assert "A-direct-ask-verb" in result.matched_rules
    assert result.provisional_bucket == ConversationBucket.Act


def test_a_due_date_phrase_iso() -> None:
    msg = make_message(
        from_address="pm@x.com",
        body_text="Please deliver the report by 2026-05-01 thanks.",
        user_position=UserRecipientPosition.TO,
    )
    snap = make_snapshot(messages=[msg])
    result = run_stage_a(snap, rule_version=RV)
    assert "A-due-date-phrase" in result.matched_rules
    assert result.extracted_signals["due_at"] == datetime(
        2026, 5, 1, tzinfo=timezone.utc
    )


def test_a_defer_phrase_next_week() -> None:
    msg = make_message(
        from_address="peer@x.com",
        body_text="Let's circle back on this next week once numbers are in.",
    )
    snap = make_snapshot(messages=[msg])
    result = run_stage_a(snap, rule_version=RV)
    assert "A-defer-phrase" in result.matched_rules


def test_a_user_sent_last_waiting_on() -> None:
    inbound = make_message(
        graph_message_id="m1",
        from_address="peer@x.com",
        body_text="Can you send the file?",
        is_from_user=False,
    )
    outbound = make_message(
        graph_message_id="m2",
        from_address="me@example.com",
        body_text="Sent. Let me know.",
        is_from_user=True,
        user_position=UserRecipientPosition.NONE,
    )
    snap = make_snapshot(messages=[inbound, outbound], user_sent_last=True)
    result = run_stage_a(snap, rule_version=RV)
    assert "A-user-sent-last" in result.matched_rules
    assert result.provisional_bucket == ConversationBucket.WaitingOn


def test_a_cc_only_no_ask_fyi() -> None:
    msg = make_message(
        from_address="someone@x.com",
        to_addresses=["other@x.com"],
        cc_addresses=["me@example.com"],
        body_text="FYI, update on the plan.",
        user_position=UserRecipientPosition.CC,
    )
    snap = make_snapshot(messages=[msg])
    result = run_stage_a(snap, rule_version=RV)
    assert "A-cc-only-no-ask" in result.matched_rules
    assert result.provisional_bucket == ConversationBucket.FYI
    assert 0.55 <= result.confidence < 0.75


def test_a_meeting_invite_rsvp() -> None:
    msg = make_message(
        from_address="calendar@x.com",
        subject="Invitation: Sync @ 3pm",
        body_text="Join me on Thursday",
    )
    snap = make_snapshot(messages=[msg])
    result = run_stage_a(snap, rule_version=RV)
    assert "A-meeting-invite-rsvp" in result.matched_rules


def test_a_no_rules_fired_returns_empty() -> None:
    msg = make_message(
        from_address="friend@example.org",
        subject="Catchup",
        body_text="Nice weather today.",
    )
    snap = make_snapshot(messages=[msg])
    result = run_stage_a(snap, rule_version=RV)
    assert result.matched_rules == []
    assert result.provisional_bucket is None
    assert result.confidence == 0.0
    assert result.model_needed is True
