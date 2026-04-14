"""Evidence + tier enums for the reducer. See reducer-spec.md §2, §3."""

from __future__ import annotations

import enum

from email_intel.db.models import ConversationEventType

__all__ = [
    "Evidence",
    "PriorityTier",
    "ConversationEventType",
    "tier_for",
]


class Evidence(str, enum.Enum):
    """Layer-1 semantic outcomes. reducer-spec §3 rows E01–E15."""

    NEW_INBOUND_ASK_DELIVERABLE = "new_inbound_ask_deliverable"  # E01 · tier ②
    NEW_INBOUND_ASK_REPLY = "new_inbound_ask_reply"  # E02 · tier ②
    USER_REPLIED_SATISFIES_ASK = "user_replied_satisfies_ask"  # E03 · tier ③
    EXPLICIT_RESOLUTION = "explicit_resolution"  # E04 · tier ①
    SOFT_RESOLUTION = "soft_resolution"  # E05 · tier ①
    EXPLICIT_DEFER = "explicit_defer"  # E06 · tier ④
    DEFER_TIMER_FIRED = "defer_timer_fired"  # E07 · tier ④
    BULK_NOISE = "bulk_noise"  # E08 · tier ⑤
    FYI_ONLY = "fyi_only"  # E09 · tier ⑤
    HANDOFF_CONFIRMED = "handoff_confirmed"  # E10 · non-tier
    DUE_DATE_UPDATE = "due_date_update"  # E11 · non-tier
    MANUAL_OVERRIDE = "manual_override"  # E12 · non-tier
    SENT_ITEMS_LAG = "sent_items_lag"  # E13 · non-tier → review
    SIGNAL_CONFLICT = "signal_conflict"  # E14 · non-tier → review
    WRITEBACK_FAILURE_THRESHOLD = "writeback_failure_threshold"  # E15 · non-tier → review


class PriorityTier(enum.IntEnum):
    """Total-ordered tiers; lower wins. reducer-spec §2."""

    TIER_1 = 1  # Resolution
    TIER_2 = 2  # New inbound ask
    TIER_3 = 3  # User sent last
    TIER_4 = 4  # Explicit defer
    TIER_5 = 5  # FYI / noise
    TIER_NONE = 99  # Non-tier / annotations


_TIER_MAP: dict[Evidence, PriorityTier] = {
    Evidence.EXPLICIT_RESOLUTION: PriorityTier.TIER_1,
    Evidence.SOFT_RESOLUTION: PriorityTier.TIER_1,
    Evidence.NEW_INBOUND_ASK_DELIVERABLE: PriorityTier.TIER_2,
    Evidence.NEW_INBOUND_ASK_REPLY: PriorityTier.TIER_2,
    Evidence.USER_REPLIED_SATISFIES_ASK: PriorityTier.TIER_3,
    Evidence.EXPLICIT_DEFER: PriorityTier.TIER_4,
    Evidence.DEFER_TIMER_FIRED: PriorityTier.TIER_4,
    Evidence.FYI_ONLY: PriorityTier.TIER_5,
    Evidence.BULK_NOISE: PriorityTier.TIER_5,
}


def tier_for(evidence: Evidence) -> PriorityTier:
    """Return the priority tier for a piece of evidence, or TIER_NONE."""
    return _TIER_MAP.get(evidence, PriorityTier.TIER_NONE)
