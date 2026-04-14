"""Unit tests for E01–E15 in evidence.py."""

from __future__ import annotations

from datetime import timedelta

import pytest

from email_intel.config import Settings
from email_intel.db.models import ConversationBucket, ConversationState
from email_intel.reducer.evidence import detect_evidence
from email_intel.schemas.events import Evidence
from email_intel.schemas.snapshot import UserRecipientPosition

from .conftest import NOW, make_msg, make_snapshot


S = Settings()


def _detect(snapshot, prior_state=ConversationState.none, prior_bucket=None, now=NOW):
    return detect_evidence(snapshot, prior_state, prior_bucket, now, S)


def test_e01_via_classifier_act():
    snap = make_snapshot(
        messages=[make_msg(body="Please send the report.")],
        classifications=[
            {"primary_bucket": ConversationBucket.Act.value, "should_create_task": True,
             "confidence": 0.9, "reason_short": "act"}
        ],
    )
    assert Evidence.NEW_INBOUND_ASK_DELIVERABLE in _detect(snap)


def test_e01_via_imperative_verb():
    snap = make_snapshot(
        messages=[make_msg(body="Please send the final draft today.")],
    )
    # No classifier, imperative verb + user on TO.
    snap = snap.model_copy(update={"user_position_on_latest": UserRecipientPosition.TO})
    assert Evidence.NEW_INBOUND_ASK_DELIVERABLE in _detect(snap)


def test_e02_via_question():
    snap = make_snapshot(messages=[make_msg(body="Can you confirm attendance?")])
    assert Evidence.NEW_INBOUND_ASK_REPLY in _detect(snap)


def test_e02_via_classifier_respond():
    snap = make_snapshot(
        messages=[make_msg(body="hi")],
        classifications=[{"primary_bucket": ConversationBucket.Respond.value,
                          "should_create_task": False, "confidence": 0.9, "reason_short": "r"}],
    )
    assert Evidence.NEW_INBOUND_ASK_REPLY in _detect(snap)


def test_e03_user_replied_after_ask():
    snap = make_snapshot(
        messages=[make_msg(idx=0), make_msg(idx=1, is_from_user=True)],
        user_sent_last=True,
        latest_inbound_ts=NOW - timedelta(minutes=60),
        latest_outbound_ts=NOW - timedelta(minutes=59),
        classifications=[{"primary_bucket": ConversationBucket.Respond.value,
                          "should_create_task": False, "confidence": 0.9, "reason_short": "r"}],
    )
    assert Evidence.USER_REPLIED_SATISFIES_ASK in _detect(snap)


def test_e04_explicit_resolution_phrase():
    snap = make_snapshot(messages=[make_msg(body="This is done and closed.")])
    assert Evidence.EXPLICIT_RESOLUTION in _detect(snap)


def test_e05_soft_resolution():
    snap = make_snapshot(messages=[make_msg(body="sounds like we're good, thanks")])
    assert Evidence.SOFT_RESOLUTION in _detect(snap)


def test_e06_explicit_defer_future_language():
    snap = make_snapshot(messages=[make_msg(body="Let's talk next week.")])
    assert Evidence.EXPLICIT_DEFER in _detect(snap)


def test_e07_defer_timer_fired():
    snap = make_snapshot(deferred_until=NOW - timedelta(minutes=1))
    assert Evidence.DEFER_TIMER_FIRED in _detect(snap, prior_state=ConversationState.deferred)


def test_e08_bulk_noise_list_unsubscribe():
    snap = make_snapshot(
        messages=[make_msg(headers={"List-Unsubscribe": "<mailto:unsub@x.com>"}, body="promo")]
    )
    assert Evidence.BULK_NOISE in _detect(snap)


def test_e09_fyi_only_cc_no_question():
    msg = make_msg(body="FYI, heads up on the report.", user_position=UserRecipientPosition.CC)
    snap = make_snapshot(messages=[msg])
    snap = snap.model_copy(update={"user_position_on_latest": UserRecipientPosition.CC})
    assert Evidence.FYI_ONLY in _detect(snap)


def test_e10_handoff_confirmed_outbound():
    snap = make_snapshot(
        messages=[make_msg(idx=0), make_msg(idx=1, is_from_user=True, body="forwarding to Jane")]
    )
    assert Evidence.HANDOFF_CONFIRMED in _detect(snap)


def test_e11_due_date_update():
    snap = make_snapshot(
        messages=[make_msg()],
        latest_due_at=NOW,
        classifications=[{"primary_bucket": ConversationBucket.Act.value,
                          "should_create_task": True, "confidence": 0.9, "reason_short": "r",
                          "due_at": (NOW + timedelta(days=3)).isoformat()}],
    )
    # due_at stored as isoformat string vs latest_due_at datetime — evidence
    # detects difference via `!=`.
    assert Evidence.DUE_DATE_UPDATE in _detect(snap)


def test_e13_sent_items_lag():
    # Need a tier-3 candidate: user_sent_last=True after inbound ask.
    snap = make_snapshot(
        messages=[make_msg(idx=0), make_msg(idx=1, is_from_user=True)],
        user_sent_last=True,
        latest_inbound_ts=NOW - timedelta(minutes=30),
        latest_outbound_ts=NOW - timedelta(minutes=25),
        sent_items_cursor_ts=NOW - timedelta(hours=2),  # behind latest inbound
    )
    ev = _detect(snap)
    assert Evidence.SENT_ITEMS_LAG in ev
    assert Evidence.USER_REPLIED_SATISFIES_ASK in ev


def test_e14_signal_conflict():
    snap = make_snapshot(
        messages=[make_msg()],
        classifications=[
            {"primary_bucket": ConversationBucket.Act.value, "should_create_task": True,
             "confidence": 0.9, "reason_short": "a"},
            {"primary_bucket": ConversationBucket.Respond.value, "should_create_task": False,
             "confidence": 0.9, "reason_short": "b"},
        ],
    )
    assert Evidence.SIGNAL_CONFLICT in _detect(snap)


def test_e15_writeback_failure_threshold():
    snap = make_snapshot(
        messages=[make_msg()],
        classifications=[{"primary_bucket": None, "confidence": 0.5, "reason_short": "",
                          "writeback_failure_count": 10}],
    )
    assert Evidence.WRITEBACK_FAILURE_THRESHOLD in _detect(snap)


def test_empty_snapshot_no_evidence():
    snap = make_snapshot()
    # Should get no evidence E01–E15 (except possibly none).
    ev = _detect(snap)
    assert ev == set()
