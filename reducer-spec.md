# Reducer Transition Table Spec

Authoritative behavioral spec for the Conversation Reducer. Every row in this document maps 1:1 to a test. If behavior disagrees with this spec, the spec wins; update the code or update the spec, never drift silently.

Companion to `project-plan.md` (§13) and `state-machine.html`. Invariants I1–I11 and F1 are defined in `project-plan.md` §29; this document references them by ID.

---

## 0 · How to read this document

- **Layer 1** (§3) maps raw evidence to a named **semantic outcome**. One snapshot may surface several outcomes; priority tiers resolve which one wins.
- **Layer 2** (§4) maps `(prior_state, winning_outcome) → (next_state, next_bucket, task_intent, category_intent, events)`.
- **Transition IDs** (`T###`) are stable. New transitions append; existing IDs never reused.
- A row is a complete behavioral contract. If any cell is ambiguous, that is a bug in the spec, not a coding judgment call.

---

## 1 · Global reducer rules

| # | Rule |
|---|---|
| G1 | Reducer signature: `reduce(snapshot, prior_state, now) -> ReducerResult`. Pure function. No I/O, no wall-clock reads, no RNG. `now` is always passed in. (I9) |
| G2 | `ReducerResult` = `{ next_state, next_bucket, task_intent, category_intent, events[], operation_keys[], review_flag }`. |
| G3 | Reducer output is **intent**, not action. A separate writeback layer consumes intents and calls Graph, protected by `operation_keys`. (I1, I6) |
| G4 | Snapshot window = `[conversation_start … latest_event_timestamp]`. Out-of-order delta pages re-run the reducer from scratch over the current window. (I10) |
| G5 | `(next_state, next_bucket)` must be one of the allowed pairs in `project-plan.md` §9 conversations table. Violations raise a `ReducerContractError` before any writeback. |
| G6 | Every reducer run appends at least one `conversation_events` row, even if the state did not change (type: `reducer_ran_noop`). Enables replay auditing. (I8) |
| G7 | When `prior_state = needs_review`, reducer runs as normal but `task_intent` and `category_intent` are forced to `noop` until `state_review_reason` is cleared. (I7) |
| G8 | Tier-③ evaluation requires `sent_items.cursor_ts >= latest_inbound.ts`. If lagging → emit `sent_items_lag` evidence → T080. (I3) |
| G9 | Determinism test: for any `(snapshot, prior_state, now)`, two calls return byte-identical `ReducerResult`. |

---

## 2 · Priority-tier resolution

Total order. **First matching tier wins.** Lower-tier outcomes are recorded in `events[].suppressed_evidence` but do not drive state.

| Tier | Name | Member outcomes (Layer 1) |
|---|---|---|
| ① | Resolution | `explicit_resolution`, `soft_resolution` |
| ② | New inbound ask | `new_inbound_ask_deliverable`, `new_inbound_ask_reply` |
| ③ | User sent last | `user_replied_satisfies_ask` |
| ④ | Explicit defer | `explicit_defer`, `defer_timer_fired` |
| ⑤ | FYI / noise | `fyi_only`, `bulk_noise` |

**Non-tier outcomes** (processed independently — they do not compete with tiers; they annotate or force review):

- `handoff_confirmed` — promotes `delegate_open → waiting_on` if present alongside a lower-tier outcome. Does not itself start a conversation.
- `due_date_update` — field-level update; no state transition.
- `manual_override` — forces `next_state` verbatim; bypasses tier resolution. Emits `override_applied`.
- `sent_items_lag` — forces `needs_review` if a tier-③ outcome would otherwise fire.
- `signal_conflict` — two high-confidence evidences in the same tier → `needs_review`.
- `writeback_failure_threshold` — writeback layer has failed N times for this conversation → `needs_review`.

**Tie-break within a tier:** newest evidence timestamp wins. If still tied, `reason_short` is sorted lexically for determinism.

---

## 3 · Layer 1 · Evidence → Semantic Outcome

Each row answers: *what does this evidence mean?* The reducer calls `classify_evidence(snapshot) -> set[Outcome]`.

| ID | Outcome | Evidence pattern (any-of) | Notes |
|---|---|---|---|
| E01 | `new_inbound_ask_deliverable` | classifier `primary_bucket = Act` with `should_create_task=true`; or rule match: imperative verb targeting user + artifact noun ("send", "prepare", "review the deck") | Tier ② |
| E02 | `new_inbound_ask_reply` | classifier `primary_bucket = Respond`; or direct question to user ("can you confirm?", "?"); or meeting invite awaiting RSVP | Tier ② |
| E03 | `user_replied_satisfies_ask` | latest message is from user (Sent Items), sent *after* the last inbound ask, and covers the ask per classifier `should_create_task=false` | Tier ③. Blocked by G8 |
| E04 | `explicit_resolution` | explicit language match: "done", "closed", "resolved", "signed", "cancelled"; OR counterparty confirms completion of the active ask | Tier ① → `hard_complete` |
| E05 | `soft_resolution` | classifier infers completion but no explicit signal ("sounds like we're good", "ok, looks fine") | Tier ① → `soft_complete` |
| E06 | `explicit_defer` | future-date language ("next week", "after the Friday meeting", "Q3"); or `extracted_defer_until` set | Tier ④ |
| E07 | `defer_timer_fired` | synthetic evidence: `deferred_until <= now` and prior state was `deferred` | Tier ④ (special: reopens) |
| E08 | `bulk_noise` | List-Unsubscribe header; sender matches bulk/promo heuristic; `primary_bucket = DeleteOrUnsubscribe` | Tier ⑤ |
| E09 | `fyi_only` | user on Cc only, no direct ask, no unsubscribe header; or `primary_bucket = FYI` | Tier ⑤ |
| E10 | `handoff_confirmed` | **outbound** message contains forward/assignment language ("forwarding to X", "Y will own this"); OR user explicitly named a new owner | Non-tier. (I11) |
| E11 | `due_date_update` | new `extracted_due_at` differs from `conversations.due_at` by > 0 | Non-tier |
| E12 | `manual_override` | row in review console with `resolved_override`; carries target state | Non-tier, bypasses tiers |
| E13 | `sent_items_lag` | `sent_items.cursor_ts < latest_inbound.ts` AND a tier-③ candidate exists | Non-tier, forces review |
| E14 | `signal_conflict` | two outcomes in the same tier, both above high-confidence threshold, and disagree on bucket | Non-tier, forces review |
| E15 | `writeback_failure_threshold` | `operation_keys` shows ≥ N failed attempts for this conversation in last window | Non-tier, forces review |

---

## 4 · Layer 2 · State Transition Matrix

Format: `T### | prior_state | winning_outcome | → | next_state | next_bucket | task_intent | category_intent | events`

**Intent vocabularies:**

- `task_intent`: `noop` · `create` · `update_fields` · `move_list(new_bucket)` · `soft_complete` · `hard_complete` · `reopen` · `suppress` · `dead_letter`
- `category_intent`: `noop` · `apply(bucket)` · `clear` · `preserve`
- `events`: canonical `conversation_events.event_type` values

### 4.1 From `none`

| ID | Winning outcome | next_state | next_bucket | task_intent | category_intent | events |
|---|---|---|---|---|---|---|
| T001 | `new_inbound_ask_deliverable` | `act_open` | `Act` | `create` | `apply(Act)` | `state_changed`, `task_created` |
| T002 | `new_inbound_ask_reply` | `respond_open` | `Respond` | `create` | `apply(Respond)` | `state_changed`, `task_created` |
| T003 | `user_replied_satisfies_ask` | `waiting_on` | `WaitingOn` | `create` | `apply(WaitingOn)` | `state_changed`, `task_created` (user replied before we processed) |
| T004 | `explicit_defer` | `deferred` | `Defer` | `noop` (armed; no list yet) | `apply(Defer)` | `state_changed` |
| T005 | `bulk_noise` | `noise_transient` | `DeleteOrUnsubscribe` | `noop` | `apply(DeleteOrUnsubscribe)` | `state_changed` |
| T006 | `fyi_only` | `fyi_context` | `FYI` | `noop` | `apply(FYI)` | `state_changed` |
| T007 | `explicit_resolution` | `none` | `null` | `noop` | `noop` | `reducer_ran_noop` (nothing to resolve) |
| T008 | `soft_resolution` | `none` | `null` | `noop` | `noop` | `reducer_ran_noop` |
| T009 | `handoff_confirmed` (alone) | `none` | `null` | `noop` | `noop` | `reducer_ran_noop` (handoff without a prior delegate_open is ignored) |

### 4.2 From `act_open`

| ID | Winning outcome | next_state | next_bucket | task_intent | category_intent | events |
|---|---|---|---|---|---|---|
| T010 | `new_inbound_ask_deliverable` | `act_open` | `Act` | `update_fields` | `noop` | `task_updated` |
| T011 | `new_inbound_ask_reply` | `respond_open` | `Respond` | `move_list(Respond)` | `apply(Respond)` | `state_changed`, `task_updated` (canonical act↔respond drift, §13.3 #9) |
| T012 | `user_replied_satisfies_ask` | `waiting_on` | `WaitingOn` | `move_list(WaitingOn)` | `apply(WaitingOn)` | `state_changed`, `task_updated` |
| T013 | `explicit_resolution` | `done` | *preserve* (`Act`) | `hard_complete` | per `done_category_policy` | `state_changed`, `task_hard_complete` |
| T014 | `soft_resolution` | `done` | *preserve* (`Act`) | `soft_complete` (sets `soft_complete_until = now + 7d`) | per `done_category_policy` | `state_changed`, `task_soft_complete` |
| T015 | `explicit_defer` | `deferred` | `Defer` | `update_fields` (due_at → deferred_until; keep task) | `apply(Defer)` | `state_changed`, `task_updated` |
| T016 | `fyi_only` | `act_open` | `Act` | `noop` | `noop` | `reducer_ran_noop` (§13.3 #8 — don't disturb active task on FYI) |
| T017 | `due_date_update` (non-tier, any state) | *unchanged* | *unchanged* | `update_fields` | `noop` | `task_updated` |

### 4.3 From `respond_open`

| ID | Winning outcome | next_state | next_bucket | task_intent | category_intent | events |
|---|---|---|---|---|---|---|
| T020 | `new_inbound_ask_reply` | `respond_open` | `Respond` | `update_fields` | `noop` | `task_updated` |
| T021 | `new_inbound_ask_deliverable` | `act_open` | `Act` | `move_list(Act)` | `apply(Act)` | `state_changed`, `task_updated` (scope escalates) |
| T022 | `user_replied_satisfies_ask` | `waiting_on` | `WaitingOn` | `move_list(WaitingOn)` | `apply(WaitingOn)` | `state_changed`, `task_updated` |
| T023 | `explicit_resolution` | `done` | *preserve* (`Respond`) | `hard_complete` | per `done_category_policy` | `state_changed`, `task_hard_complete` |
| T024 | `soft_resolution` | `done` | *preserve* (`Respond`) | `soft_complete` | per `done_category_policy` | `state_changed`, `task_soft_complete` |
| T025 | `explicit_defer` | `deferred` | `Defer` | `update_fields` | `apply(Defer)` | `state_changed`, `task_updated` |
| T026 | `fyi_only` | `respond_open` | `Respond` | `noop` | `noop` | `reducer_ran_noop` |

### 4.4 From `delegate_open`

| ID | Winning outcome | next_state | next_bucket | task_intent | category_intent | events |
|---|---|---|---|---|---|---|
| T030 | `handoff_confirmed` (non-tier) | `waiting_on` | `WaitingOn` | `move_list(WaitingOn)` | `apply(WaitingOn)` | `state_changed`, `task_updated` (Invariant I11) |
| T031 | `user_replied_satisfies_ask` *without* `handoff_confirmed` | `delegate_open` | `Delegate` | `noop` | `noop` | `reducer_ran_noop` (user reply alone is insufficient evidence) |
| T032 | `new_inbound_ask_deliverable` | `act_open` | `Act` | `move_list(Act)` | `apply(Act)` | `state_changed`, `task_updated` (user reclaims ownership) |
| T033 | `explicit_resolution` | `done` | *preserve* (`Delegate`) | `hard_complete` | per policy | `state_changed`, `task_hard_complete` |
| T034 | `soft_resolution` | `delegate_open` | `Delegate` | `noop` | `noop` | `reducer_ran_noop` (delegate-soft-resolution is too weak; wait for handoff or explicit) |
| T035 | `explicit_defer` | `deferred` | `Defer` | `update_fields` | `apply(Defer)` | `state_changed`, `task_updated` |

### 4.5 From `deferred`

| ID | Winning outcome | next_state | next_bucket | task_intent | category_intent | events |
|---|---|---|---|---|---|---|
| T040 | `defer_timer_fired` (original ask was `Act`) | `act_open` | `Act` | `create` **or** `reopen` if task row exists | `apply(Act)` | `state_changed`, `task_created` or `task_reopened` |
| T041 | `defer_timer_fired` (original ask was `Respond`) | `respond_open` | `Respond` | `create` or `reopen` | `apply(Respond)` | `state_changed`, `task_created` or `task_reopened` |
| T042 | `new_inbound_ask_deliverable` | `act_open` | `Act` | `create` or `reopen` | `apply(Act)` | `state_changed`, `task_reopened` |
| T043 | `new_inbound_ask_reply` | `respond_open` | `Respond` | `create` or `reopen` | `apply(Respond)` | `state_changed`, `task_reopened` |
| T044 | `explicit_resolution` | `done` | *preserve* | `hard_complete` | per policy | `state_changed`, `task_hard_complete` |
| T045 | `fyi_only` | `deferred` | `Defer` | `noop` | `noop` | `reducer_ran_noop` |

### 4.6 From `waiting_on`

| ID | Winning outcome | next_state | next_bucket | task_intent | category_intent | events |
|---|---|---|---|---|---|---|
| T050 | `explicit_resolution` | `done` | *preserve* (`WaitingOn`) | `hard_complete` | per policy | `state_changed`, `task_hard_complete` |
| T051 | `soft_resolution` | `done` | *preserve* (`WaitingOn`) | `soft_complete` | per policy | `state_changed`, `task_soft_complete` |
| T052 | `new_inbound_ask_deliverable` | `act_open` | `Act` | `move_list(Act)` | `apply(Act)` | `state_changed`, `task_updated` (counterparty chase with new ask) |
| T053 | `new_inbound_ask_reply` | `respond_open` | `Respond` | `move_list(Respond)` | `apply(Respond)` | `state_changed`, `task_updated` |
| T054 | `user_replied_satisfies_ask` | `waiting_on` | `WaitingOn` | `update_fields` | `noop` | `task_updated` (user nudged again) |
| T055 | `fyi_only` | `waiting_on` | `WaitingOn` | `noop` | `noop` | `reducer_ran_noop` |

### 4.7 From `done` (reopen rules)

Reopen eligibility depends on `completion_kind` and soft-window. See §7.

| ID | Winning outcome | Guards | next_state | next_bucket | task_intent | category_intent | events |
|---|---|---|---|---|---|---|---|
| T060 | `new_inbound_ask_deliverable` | `completion_kind = hard` OR (`soft` AND `now <= soft_complete_until`) | `act_open` | `Act` | `reopen` + `update_fields` | `apply(Act)` | `state_changed`, `task_reopened` |
| T061 | `new_inbound_ask_reply` | same as T060 | `respond_open` | `Respond` | `reopen` + `update_fields` | `apply(Respond)` | `state_changed`, `task_reopened` |
| T062 | `new_inbound_ask_*` | `completion_kind = soft` AND `now > soft_complete_until` | `act_open`/`respond_open` | bucket | `create` (new task; old one stays closed) | `apply(bucket)` | `state_changed`, `task_created` |
| T063 | any inbound **within** soft window, classifier says "not a new ask" | `completion_kind = soft` AND within window | `act_open` **or** `respond_open` (prior kind) | prior bucket | `reopen` | `apply(prior)` | `state_changed`, `task_reopened` (soft window auto-reopen, §13.6) |
| T064 | `fyi_only` | `completion_kind = hard` | `done` | *preserve* | `noop` | `noop` | `reducer_ran_noop` (hard-complete is sticky; FYI never reopens) |
| T065 | `fyi_only` | `completion_kind = soft`, within window | `done` | *preserve* | `noop` | `noop` | `reducer_ran_noop` (FYI alone does not auto-reopen; needs content signal) |
| T066 | archive window elapsed (synthetic) | always | `none` | `null` | `noop` | `clear` (if not already) | `state_changed` |

### 4.8 From `fyi_context` / `noise_transient`

| ID | prior_state | Winning outcome | next_state | next_bucket | task_intent | category_intent | events |
|---|---|---|---|---|---|---|---|
| T070 | `fyi_context` | `new_inbound_ask_*` | `act_open`/`respond_open` | bucket | `create` | `apply(bucket)` | `state_changed`, `task_created` |
| T071 | `fyi_context` | `fyi_only` | `fyi_context` | `FYI` | `noop` | `noop` | `reducer_ran_noop` |
| T072 | `noise_transient` | `new_inbound_ask_*` | `needs_review` | *preserve* | `noop` | `noop` | `needs_review_raised` (unusual: promo→ask is suspicious; force human review) |
| T073 | `noise_transient` | `bulk_noise` | `noise_transient` | `DeleteOrUnsubscribe` | `noop` | `noop` | `reducer_ran_noop` |

### 4.9 needs_review transitions

| ID | prior_state | Trigger | next_state | next_bucket | task_intent | category_intent | events |
|---|---|---|---|---|---|---|---|
| T080 | any | `sent_items_lag` (G8) | `needs_review` | *preserve* | `noop` (I7) | `noop` | `needs_review_raised(reason=sent_items_lag)` |
| T081 | any | `signal_conflict` | `needs_review` | *preserve* | `noop` | `noop` | `needs_review_raised(reason=signal_conflict)` |
| T082 | any | `writeback_failure_threshold` | `needs_review` | *preserve* | `dead_letter` | `noop` | `needs_review_raised(reason=writeback_dead_letter)` |
| T083 | `needs_review` | classifier-level `resolved_override` → override target | target state | target bucket | per target | per target | `needs_review_resolved`, `override_applied`, plus normal state events |
| T084 | `needs_review` | later disambiguating message clears conflict | run normal reducer on new snapshot | — | — | — | `needs_review_resolved`, then normal events |
| T085 | `needs_review` | any other evidence while review is open | `needs_review` | *preserve* | `noop` | `noop` | `reducer_ran_noop` (review blocks advancement — G7) |

### 4.10 Manual override (bypasses tiers)

| ID | prior_state | Trigger | next_state | next_bucket | task_intent | category_intent | events |
|---|---|---|---|---|---|---|---|
| T090 | any | `manual_override(target_state, target_bucket)` | target | target | computed as if normal transition | `apply(target_bucket)` | `override_applied`, `state_changed`, applicable task event |

### 4.11 Guarded no-ops (explicit, not defaults)

These rows exist to **assert the absence** of a transition. Tests must verify these do *not* fire side effects.

| ID | prior_state | Event | Assertion |
|---|---|---|---|
| T100 | `done` (`hard`) | `fyi_only` | no reopen; `task_intent=noop` (§13.6) |
| T101 | `done` (`soft`, expired window) | `fyi_only` | no reopen; `task_intent=noop` |
| T102 | `waiting_on` | `user_replied_satisfies_ask` again | `task_intent=update_fields` only; never `create` (I2) |
| T103 | `needs_review` | any tier evidence | writeback intents all `noop` (G7, I7) |
| T104 | `delegate_open` | classifier hint "looks delegated" without E10 | no transition (I11) |

---

## 5 · Task side-effect matrix (condensed)

Derived from Layer 2. Useful as a quick reference.

| `task_intent` | Preconditions | Graph call | `operation_keys` key | Post-state |
|---|---|---|---|---|
| `create` | no active task for `(conversation_id, 'primary')` | `POST /me/todo/lists/{list}/tasks` + `POST linkedResources` | `task_create_key` | `todo_tasks.status = notStarted`, reverse-mapped on conversations |
| `update_fields` | active task exists | `PATCH /tasks/{id}` (title/due/body) | `writeback_key` | `last_synced_at` bumped |
| `move_list(new_bucket)` | active task exists; bucket differs | `POST` new task in target list + `DELETE`/`PATCH` old, preserving `linkedResource`. Reuse Graph ID if same list supports it; otherwise swap. | `writeback_key` | new row linked; old archived |
| `soft_complete` | active task exists | `PATCH status=completed` | `writeback_key` | `completion_kind=soft`, `soft_complete_until=now+7d` |
| `hard_complete` | active task exists | `PATCH status=completed` | `writeback_key` | `completion_kind=hard`, `soft_complete_until=null` |
| `reopen` | completed task exists; reopen eligibility met (§7) | `PATCH status=notStarted` + optional fields | `writeback_key` | `completion_kind=null` |
| `suppress` | no task desired; existing task would be incorrect | if task exists, `PATCH status=completed` + event `task_soft_complete(reason=suppressed)` | `writeback_key` | — |
| `dead_letter` | writeback failure threshold reached | no Graph call | — | conversation goes to `needs_review` |
| `noop` | — | — | — | — |

---

## 6 · Category side-effect matrix

| `category_intent` | Preconditions | Graph call | Notes |
|---|---|---|---|
| `apply(X)` | `open_action_state != needs_review` | `PATCH /messages/{id}` — set categories = (existing non-`AI-*`) ∪ {`AI-X`} | Strip any other `AI-*` before applying |
| `clear` | `open_action_state != needs_review` | `PATCH /messages/{id}` — set categories = (existing non-`AI-*`) | Preserves user-created categories |
| `preserve` | — | no call | Explicit no-op; keeps current category |
| `noop` | — | no call | Category writeback is dormant (typical in `needs_review`, `done`-with-preserve policy) |

Policy table for `done`:

| `done_category_policy` | Behavior on transition into `done` |
|---|---|
| `clear` (default) | `category_intent = clear` |
| `fyi` | `category_intent = apply(FYI)` |
| `preserve` | `category_intent = preserve` |

---

## 7 · Reopen / soft-complete / hard-complete rules

Formalizes §13.6.

```
eligible_to_reopen(task, now, trigger_outcome) =
  task.status == completed AND
  (
    task.completion_kind == 'hard'
      AND trigger_outcome ∈ {new_inbound_ask_deliverable, new_inbound_ask_reply}   // I5
    OR
    task.completion_kind == 'soft'
      AND now <= task.soft_complete_until
      AND trigger_outcome ∈ {
           new_inbound_ask_deliverable,
           new_inbound_ask_reply,
           user_replied_satisfies_ask_continuation,   // a reply that references the old thread
           due_date_update
         }
  )
```

Derived behaviors:

- Hard-complete + `fyi_only` → **never** reopens (T064, T100).
- Soft-complete within window + `fyi_only` → does **not** reopen (T065) — FYI alone is too weak; needs content signal.
- Soft-complete expired window + new ask → `create` a fresh task (T062). Preserves history without dragging old completion baggage.
- Soft→hard promotion: if an explicit resolution arrives for a `soft`-completed task inside the window, emit `task_hard_complete` event and flip `completion_kind` without a new state transition.

---

## 8 · `needs_review` triggers (summary)

| Trigger | Source | Resolves when |
|---|---|---|
| `sent_items_lag` | G8 (Layer 0) | Sent cursor catches up; reducer re-runs → T084 |
| `signal_conflict` | E14 | Later message disambiguates, or manual override |
| `writeback_failure_threshold` | E15 | Underlying Graph issue fixed + manual resolve |
| `noise_transient → new_inbound_ask` | T072 | Manual override (looks suspicious — might be a genuine response on a newsletter thread) |

While `state = needs_review`: all `task_intent` and `category_intent` are forced to `noop` (G7, I7).

---

## 9 · Invariants mapped to tests

Every invariant in `project-plan.md` §29 maps to at least one assertion below. Tests live in `tests/reducer/` and are the authoritative fixture for behavior.

| Invariant | Test file | Asserts |
|---|---|---|
| I1 · Reducer is sole task author | `test_no_direct_task_writes.py` | Classifier code path imports no Graph write client; grep-test fails the build if it does |
| I2 · One active task per `(conversation, action_slot)` | `test_one_active_task.py` | Running T001 twice back-to-back on same conversation produces 1 active row, 1 `task_create` op-key hit + 1 no-op |
| I3 · Sent Items must be in snapshot | `test_sent_gating.py` | Snapshot with `sent.cursor_ts < latest_inbound.ts` + tier-③ evidence → T080 `needs_review`, never T012/T022/T054 |
| I4 · Priority tiers are total-ordered | `test_priority_ordering.py` | Crafted snapshot emitting {E04, E01, E03} simultaneously → T013 wins, E01/E03 appear in `suppressed_evidence` |
| I5 · Soft reversible; hard sticky | `test_reopen_rules.py` | Matrix of `(completion_kind, age, trigger)` → expected reopen/no-reopen per §7 |
| I6 · Idempotency | `test_idempotency.py` | Every writeback intent carries an `operation_key`; duplicate call returns stored `result_json` without Graph POST |
| I7 · `needs_review` blocks writeback | `test_review_blocks_writeback.py` | For every state × every evidence combo while `needs_review=true`, assert `task_intent=noop` and `category_intent=noop` (T103) |
| I8 · `conversation_events` append-only | `test_event_log_integrity.py` | DB trigger rejects UPDATE/DELETE on `conversation_events`; corrections arrive as compensating rows |
| I9 · Determinism | `test_determinism.py` | Property test: `reduce(s, p, t) == reduce(s, p, t)` for 1000 random snapshots |
| I10 · Bounded time window | `test_out_of_order_delta.py` | Inject delta pages in reverse order → final state matches linear-order replay |
| I11 · Handoff evidence is narrow | `test_delegate_handoff.py` | `delegate_open + classifier-hint-only` → T031 (no-op). Only E10 triggers T030 |
| F1 · Multi-action schema hook | `test_action_slot.py` | Insert second row with `action_slot='secondary'` for same conversation → succeeds; v1 reducer never emits anything but `'primary'` |

**Non-invariant test classes** (also required):

- **Golden per-transition tests.** One test per row T001–T104. Inputs: `(prior_state, prior_bucket, evidence_set, now)`. Expected: full `ReducerResult` snapshot, compared byte-for-byte.
- **Replay tests.** Real (anonymized) email thread fixtures in `tests/fixtures/threads/*.jsonl`; assert final state matches expected annotation file.
- **Contract-invariant suite.** Runs after every golden test: checks `(next_state, next_bucket)` is in the allowed-pair table (G5).

---

## 10 · Spec maintenance

- Every PR that changes reducer behavior must: update this file, update the test map in §9, and bump `reducer_version` (appears in `conversation_events.payload_json`).
- New transitions append at the next free `T###`; never renumber.
- Removing a transition: mark the row `DEPRECATED (reducer_version >= N)` and keep the test with `@deprecated`. Historical events must remain replayable.
- `state-machine.html` Panel 3 is a visual mirror of this file. If the two disagree, this file wins.
