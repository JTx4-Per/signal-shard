"""Priority-tier resolver tests — reducer-spec §2."""

from __future__ import annotations

from email_intel.reducer.priority import resolve
from email_intel.schemas.events import Evidence, PriorityTier


def test_multi_tier_tier1_wins():
    ev = {Evidence.EXPLICIT_RESOLUTION, Evidence.NEW_INBOUND_ASK_DELIVERABLE,
          Evidence.USER_REPLIED_SATISFIES_ASK}
    r = resolve(ev)
    assert r.winning == Evidence.EXPLICIT_RESOLUTION
    assert r.tier == PriorityTier.TIER_1
    assert Evidence.NEW_INBOUND_ASK_DELIVERABLE in r.suppressed
    assert Evidence.USER_REPLIED_SATISFIES_ASK in r.suppressed


def test_manual_override_bypass():
    ev = {Evidence.EXPLICIT_RESOLUTION, Evidence.MANUAL_OVERRIDE,
          Evidence.NEW_INBOUND_ASK_DELIVERABLE}
    r = resolve(ev)
    assert r.winning == Evidence.MANUAL_OVERRIDE
    assert r.tier == PriorityTier.TIER_NONE
    assert r.suppressed == ()


def test_sent_items_lag_suppresses_tier3():
    ev = {Evidence.USER_REPLIED_SATISFIES_ASK, Evidence.SENT_ITEMS_LAG}
    r = resolve(ev)
    assert r.winning is None
    assert r.tier == PriorityTier.TIER_NONE
    assert Evidence.SENT_ITEMS_LAG in r.non_tier_modifiers


def test_non_tier_modifiers_collected():
    ev = {Evidence.NEW_INBOUND_ASK_DELIVERABLE, Evidence.DUE_DATE_UPDATE,
          Evidence.HANDOFF_CONFIRMED}
    r = resolve(ev)
    assert r.winning == Evidence.NEW_INBOUND_ASK_DELIVERABLE
    assert Evidence.DUE_DATE_UPDATE in r.non_tier_modifiers
    assert Evidence.HANDOFF_CONFIRMED in r.non_tier_modifiers


def test_empty_evidence():
    r = resolve(set())
    assert r.winning is None
    assert r.tier == PriorityTier.TIER_NONE
    assert r.suppressed == ()


def test_tier2_tiebreak_deterministic():
    ev = {Evidence.NEW_INBOUND_ASK_DELIVERABLE, Evidence.NEW_INBOUND_ASK_REPLY}
    r1 = resolve(ev)
    r2 = resolve(ev)
    assert r1.winning == r2.winning
    assert r1.suppressed == r2.suppressed
