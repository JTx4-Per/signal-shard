# ADR-003: Reducer as a pure function

**Status:** Accepted — 2026-Q1
**Context:** reducer-spec §1 (G1, G9), project-plan §10.3

## Decision

`email_intel.reducer.reducer.reduce(inp, settings) -> ReducerResult` is a
**pure function**: no I/O, no wall-clock reads, no RNG, no database access.
All inputs arrive via `ReducerInput` (snapshot + prior state + evidence +
`now` + optional manual override); all outputs flow through
`ReducerResult` (next state/bucket + intents + events + operation keys).

## Rationale

- **Determinism.** Replaying the same snapshot with the same
  `reducer_version` produces identical output byte-for-byte. This lets us
  back-test rule changes against the event log before cutting a new
  version.
- **Testability.** Every T001–T104 transition is unit-testable without a
  database, without a mock Graph client, without a scheduler.
- **Auditability.** Evidence detection, priority resolution, and
  transition selection are separable phases — a `no_matching_transition`
  log line points at exactly the (prior_state, winning_evidence) pair to
  inspect.
- **Safety.** The reducer never writes. Writeback consumes the intent
  envelope under the per-conversation lock. If the writeback fails, the
  reducer's output is still a fully-formed `ReducerResult` that can be
  retried or replayed.

## Consequences

- The reducer may not consult the database, not even for a convenience
  lookup. Anything it needs must first be projected into the snapshot by
  `ingestion/snapshot_builder.py`.
- `now` must be passed in explicitly; we inject it from the pipeline
  wrapper so tests can freeze time.
- Side-effects (event inserts, task CRUD, category patches) live entirely
  in `writeback/` — and are idempotent via `operation_keys`.
