# ADR-002: Rules-only bootstrap (no model dependency)

**Status:** Accepted — 2026-Q1
**Context:** project-plan §12.4

## Decision

The v1 classifier runs **Stage A (rules) → Stage C (override rules) →
Gate** only. `LLM_PROVIDER=none`; there is no Stage B (model) call.

## Rationale

- A deterministic rules engine gives us a cheap, auditable seed path while
  we bootstrap real traffic. Every decision is traceable to a rule row.
- Adding a model dependency on day one would force us to ship an API
  contract we don't yet understand (which fields, which latency, which
  failure modes). Better to measure Stage A accuracy first and let the
  shape of Stage B emerge from real misclassifications.
- Testing is faster and fully offline. `classify()` output is stable
  given `(snapshot, rule_version)` — see `classifier_input_hash`.

## Consequences

- Stage A `confidence` is rule-scored, not probability-calibrated; the
  Gate threshold (`review_threshold`) is hand-tuned.
- The classifier `model_version` is literally `"rules-only-v1"` for every
  row. A later model wave will cut a new constant and re-hash inputs.
- `should_create_task` is a heuristic (actionable bucket + confidence ≥
  0.75). Until Stage B lands, marginal cases go through the review queue
  rather than auto-generating tasks.
