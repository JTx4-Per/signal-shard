"""Priority-tier resolver — reducer-spec §2.

Total order; first matching tier wins. Lower-tier outcomes are returned in
`suppressed` so the reducer can record them in the event payload.

Non-tier evidences (E10/E11/E12/E13/E14/E15) never compete with tiers —
they appear in `non_tier_modifiers` so downstream logic can apply them.
"""

from __future__ import annotations

from dataclasses import dataclass

from email_intel.schemas.events import Evidence, PriorityTier, tier_for

__all__ = ["PriorityResolution", "resolve", "NON_TIER_EVIDENCE"]


NON_TIER_EVIDENCE: frozenset[Evidence] = frozenset(
    {
        Evidence.HANDOFF_CONFIRMED,
        Evidence.DUE_DATE_UPDATE,
        Evidence.MANUAL_OVERRIDE,
        Evidence.SENT_ITEMS_LAG,
        Evidence.SIGNAL_CONFLICT,
        Evidence.WRITEBACK_FAILURE_THRESHOLD,
    }
)


@dataclass(frozen=True)
class PriorityResolution:
    winning: Evidence | None
    tier: PriorityTier
    suppressed: tuple[Evidence, ...]
    non_tier_modifiers: tuple[Evidence, ...]


def _evidence_sort_key(e: Evidence) -> tuple[int, str]:
    # Deterministic tiebreak: rely on fixed enum declaration order by name.
    order = list(Evidence)
    return (order.index(e), e.value)


def resolve(evidence_set: set[Evidence] | frozenset[Evidence]) -> PriorityResolution:
    """Total-order resolve; first matching tier wins.

    `manual_override` bypasses all tiers (winning=MANUAL_OVERRIDE, tier=TIER_NONE).
    """
    evidence_set = set(evidence_set)

    if Evidence.MANUAL_OVERRIDE in evidence_set:
        non_tier_override = tuple(
            sorted(
                (e for e in evidence_set if e in NON_TIER_EVIDENCE and e != Evidence.MANUAL_OVERRIDE),
                key=_evidence_sort_key,
            )
        )
        return PriorityResolution(
            winning=Evidence.MANUAL_OVERRIDE,
            tier=PriorityTier.TIER_NONE,
            suppressed=(),
            non_tier_modifiers=non_tier_override,
        )

    tier_candidates: dict[PriorityTier, list[Evidence]] = {}
    non_tier: list[Evidence] = []
    for ev in evidence_set:
        if ev in NON_TIER_EVIDENCE:
            non_tier.append(ev)
            continue
        tier = tier_for(ev)
        tier_candidates.setdefault(tier, []).append(ev)

    winning: Evidence | None = None
    winning_tier = PriorityTier.TIER_NONE
    suppressed: list[Evidence] = []

    for tier in sorted(tier_candidates.keys(), key=lambda t: t.value):
        members = sorted(tier_candidates[tier], key=_evidence_sort_key)
        if winning is None:
            winning = members[0]
            winning_tier = tier
            # other members in the same tier are suppressed too
            suppressed.extend(members[1:])
        else:
            suppressed.extend(members)

    # If SENT_ITEMS_LAG fired and a tier-3 candidate existed, suppress tier-3
    # contribution: the reducer will use non_tier_modifiers to force review.
    if Evidence.SENT_ITEMS_LAG in non_tier and winning_tier == PriorityTier.TIER_3:
        if winning is not None:
            suppressed.append(winning)
        winning = None
        winning_tier = PriorityTier.TIER_NONE

    suppressed_sorted = tuple(sorted(set(suppressed), key=_evidence_sort_key))
    non_tier_sorted = tuple(sorted(non_tier, key=_evidence_sort_key))

    return PriorityResolution(
        winning=winning,
        tier=winning_tier,
        suppressed=suppressed_sorted,
        non_tier_modifiers=non_tier_sorted,
    )
