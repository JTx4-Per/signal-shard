# Project Plan: Personal Email Intelligence System with Microsoft Graph + Microsoft To Do

## 1. Project Summary

Build a personal-use email intelligence system that:

- Ingests Outlook email from Microsoft 365 via Microsoft Graph
- Classifies emails and threads into operational buckets:
  - `Act`
  - `Respond`
  - `Delegate`
  - `Defer`
  - `WaitingOn`
  - `FYI`
  - `DeleteOrUnsubscribe`
- Creates and maintains Microsoft To Do tasks via Microsoft Graph for actionable items
- Writes lightweight classification state back to the mailbox using Outlook categories and/or Graph extensions
- Tracks thread state over time so that tasks are updated, completed, snoozed, or suppressed as the conversation evolves
- Operates for one user only, with strong guardrails against destructive or noisy automation

This system is intended for **personal workflow augmentation**, not team-wide triage or enterprise shared-mailbox automation.

---

## 2. Primary Goals

### Functional Goals

1. Continuously ingest new and changed emails from Outlook using Microsoft Graph
2. Normalize emails into thread-aware records suitable for classification
3. Classify each email/thread into one primary operational bucket
4. Extract structured task metadata:
   - due date
   - urgency
   - action owner
   - waiting-on person
   - delegate candidate
   - escalation flag
5. Create/update/complete Microsoft To Do tasks via Graph for actionable threads
6. Persist classifier state so the system is incremental and idempotent
7. Provide reviewable visibility into model decisions and automation actions

### Non-Functional Goals

1. Idempotent sync and task creation
2. No duplicate tasks per active thread
3. Human-auditable reasoning for every classification
4. Safe defaults; no auto-send, no broad auto-delete
5. Operable on a personal mailbox with moderate email volume
6. Easy to extend with new rules, models, or summary views

---

## 3. Scope

## In Scope

- Outlook mail ingestion through Microsoft Graph
- Inbox + Sent Items sync
- Thread-level action state
- Classification and extraction
- Microsoft To Do task creation and lifecycle management through Microsoft Graph
- Outlook categories and optional Graph extensions
- Daily review and debugging surfaces
- Local persistence layer
- Manual override support

## Out of Scope for v1

- Auto-sending emails
- Auto-forwarding or true external delegation workflows
- Auto-deleting non-obvious mail
- Shared mailbox support
- Team assignment or project management integrations beyond Microsoft To Do
- Mobile app
- Complex UI; a local web console is sufficient

---

## 4. Taxonomy

## Primary Buckets

### 4.1 Act
The user owes a deliverable or substantive work item.

Examples:
- produce a document
- review a file
- investigate an issue
- prepare a deck
- send numbers or artifacts

### 4.2 Respond
The user owes a communication response.

Examples:
- answer a question
- confirm attendance
- provide a decision
- acknowledge and reply

### 4.3 Delegate
Someone else should own the task, but the user still wants to track it.

Note:
For personal-use v1, this should usually create a tracking task for the user, not attempt actual assignment automation.

### 4.4 Defer
The thread is actionable later, but not now.

Examples:
- follow up next week
- revisit after a meeting
- act on a future date

### 4.5 WaitingOn
Progress is blocked on another person or external dependency.

Examples:
- waiting for approval
- waiting for a response
- waiting for a document or signature

### 4.6 FYI
The user does not owe action. The email is useful context only.

### 4.7 DeleteOrUnsubscribe
The email is noise, promo, bulk, list mail, or otherwise not worth retaining as actionable workflow.

---

## 5. Design Principles

1. **Thread-aware over message-aware**
   - Action state belongs primarily to the conversation, not individual messages.

2. **Rules + model hybrid**
   - Deterministic logic should handle obvious cases.
   - Model inference should resolve ambiguity and extract metadata.

3. **Mailbox is source of truth for raw mail**
   - Local database is source of truth for computed state.

4. **Graph-native task lifecycle**
   - All To Do task creation, update, and completion must use Microsoft Graph.

5. **Visible + hidden state**
   - User-facing labels via Outlook categories.
   - Internal state via local DB and optionally one Graph extension payload.

6. **Idempotent automation**
   - Reprocessing the same item must not create duplicate side effects.

7. **Safe automation**
   - Auto-create tasks and labels.
   - Never auto-send.
   - Never auto-delete except possibly explicit future opt-in rules.

---

## 6. Architecture Overview

## 6.1 High-Level Components

1. **Graph Ingestion Service**
   - Pulls messages incrementally from Graph
   - Handles subscriptions and delta tokens
   - Normalizes email data

2. **Mail Normalizer**
   - Converts raw Graph message payloads into canonical internal records
   - Generates thread snapshots and derived features

3. **Classification Engine**
   - Runs deterministic rules
   - Runs model-based classification/extraction for ambiguous cases
   - Produces structured classification output

4. **Thread State Reducer**
   - Reconciles message-level changes into thread-level workflow state
   - Determines whether a To Do task should exist and in what state

5. **To Do Sync Service**
   - Creates, updates, completes, and reopens Microsoft To Do tasks via Graph
   - Maintains mapping between conversation and task

6. **Mailbox Annotation Service**
   - Applies Outlook categories
   - Optionally stores extension state on messages

7. **Persistence Layer**
   - Stores messages, conversations, classification runs, tasks, sync tokens, audit records

8. **Review Console**
   - Local/admin UI for review, overrides, and debugging

---

## 7. Technology Stack

Locked-in choices:

- **Language:** Python 3.11+
- **API framework:** FastAPI (async, Pydantic v2 for the classifier JSON contract)
- **ORM:** SQLAlchemy 2.x (async) with Alembic migrations
- **Database:** SQLite (WAL mode) for v1 — solo prototype, single user, single machine. Sufficient for the expected volume and keeps the stack trivial to run. Keep the ORM layer (SQLAlchemy 2.x) and schema portable so a later Postgres cutover is a config change, not a rewrite.
  - SQLAlchemy 2.x with a SQLite-compatible async setup is used **for ergonomic consistency only**. Do not assume true parallel DB concurrency. SQLite is a single-writer store regardless of whether the driver is sync or async — "async ORM" does not mean "parallel writes are fine."
  - Treat every transaction as short and serialized. No long-lived sessions. No concurrent write transactions.
  - All reducer + writeback persistence for a given conversation must execute under an in-process asyncio lock keyed by `conversation_id`. Reads may be concurrent; writes must not.
  - SQLite-specific requirements: `PRAGMA journal_mode=WAL`, `PRAGMA synchronous=NORMAL`, `PRAGMA foreign_keys=ON`, `PRAGMA busy_timeout=5000`.
  - Reassess Postgres only if: multiple concurrent writers are introduced, dataset grows past ~10 GB, or full-text search needs outgrow FTS5.
- **Queue:** in-process async worker (ARQ or a small custom asyncio task queue) backed by SQLite for durability. Celery/Redis is overkill for solo use; defer until needed.
- **Cache / broker:** none required for v1. Add Redis only if/when a real broker becomes necessary.
- **Scheduler:** APScheduler in-process (subscription renewal, delta fallback poll, defer-timer sweeper)
- **UI (review console):** server-rendered FastAPI + htmx is sufficient for v1; React optional later
- **Auth:** Microsoft identity platform, delegated auth (MSAL for Python), single user

Preferred deployment model:
- single-process FastAPI app on an always-on workstation, SQLite file on local disk, systemd or `pm2`-equivalent to keep it running. No Docker required for v1.

Dev ergonomics:
- `ruff` + `mypy --strict` on the reducer and classifier contract modules
- `pytest` + `pytest-asyncio`; golden-file tests for the classifier JSON contract and reducer transition table

---

## 8. Microsoft Graph Integration Plan

## 8.1 Required Graph Resource Families

1. Mail
2. Mail folders
3. Change notifications/subscriptions
4. Microsoft To Do task lists and tasks
5. Linked resources
6. Extensions or Outlook custom properties
7. Outlook categories on messages

## 8.2 Permissions

Use least privilege wherever possible. Final permission set depends on auth model and exact feature set.

Expected delegated permissions will likely include mail read/write and tasks read/write scopes.

The implementation must document:
- required delegated scopes
- consent flow
- refresh token handling
- token encryption at rest
- token renewal and failure recovery

## 8.3 Graph Endpoints / Capabilities to Implement

### Mail Sync
- list mail folders
- incremental message sync using folder delta
- fetch full message shape as needed
- fetch Inbox
- fetch Sent Items
- optionally fetch Archive

### Change Detection
- webhook subscription for message changes
- renewal scheduler for subscriptions
- fallback periodic delta polling

### To Do
- list or create the target task lists
- create todo tasks
- update todo tasks
- complete/reopen todo tasks
- create linked resources on tasks so the task points back to the originating email/thread

### Mailbox Annotation
- patch message categories
- optionally store a compact app-owned extension payload

---

## 9. Data Model

## 9.1 Core Tables

### users
Single-user system but keep table for clean ownership boundaries.

Fields:
- id
- graph_user_id
- email
- display_name
- created_at
- updated_at

### mail_folders
Fields:
- id
- user_id
- graph_folder_id
- well_known_name
- display_name
- delta_token
- subscription_id
- subscription_expires_at
- last_sync_at
- created_at
- updated_at

### messages
Fields:
- id
- user_id
- graph_message_id
- internet_message_id
- graph_conversation_id
- folder_id
- subject
- from_address
- from_name
- sender_address
- to_recipients_json
- cc_recipients_json
- reply_to_json
- received_at
- sent_at
- is_read
- importance
- has_attachments
- categories_json
- body_text
- body_preview
- web_link
- parent_folder_graph_id
- etag
- change_key
- raw_headers_json
- is_deleted
- created_at
- updated_at

Indexes:
- unique(graph_message_id)
- index(graph_conversation_id)
- index(received_at)
- index(folder_id, received_at)

### conversations
The reducer is the **sole writer** to `open_action_*` fields. Classification writes evidence only.

Fields:
- id
- user_id
- graph_conversation_id
- canonical_subject
- latest_message_id
- latest_received_at
- last_sender_address
- last_direction
- open_action_state  (enum: none · act_open · respond_open · delegate_open · deferred · waiting_on · done · needs_review · noise_transient · fyi_context) — **workflow state machine state**; owned by the reducer
- open_action_bucket  (enum: Act · Respond · Delegate · Defer · WaitingOn · FYI · DeleteOrUnsubscribe, nullable) — **human-facing category / To Do list bucket**; what categories and list placement derive from
- open_action_task_id  (FK → todo_tasks; reverse side of the two-way map; only truth for "is there an active task")
- waiting_on_address
- deferred_until
- due_at
- escalate_flag
- state_review_reason  (nullable; set when state-level ambiguity suspends writeback — see §13.5; renamed from `needs_review_reason` for consistency with the classification counterpart)
- last_classified_at
- last_reducer_run_at
- created_at
- updated_at

**`open_action_state` vs `open_action_bucket`** — these are deliberately separate because the state machine has states that do not map 1:1 to a user-facing bucket:

| `open_action_state` | `open_action_bucket` (allowed) |
|---|---|
| `none` | null |
| `act_open` | `Act` |
| `respond_open` | `Respond` |
| `delegate_open` | `Delegate` |
| `deferred` | `Defer` (or the original bucket preserved — see §13.7) |
| `waiting_on` | `WaitingOn` |
| `done` | last bucket preserved, or null (per §15 policy) |
| `needs_review` | **unchanged from prior value** (no writeback while in review) |
| `fyi_context` | `FYI` |
| `noise_transient` | `DeleteOrUnsubscribe` |

Invariant: the pair `(open_action_state, open_action_bucket)` must always be one of the rows above. Violations are caught by a DB check constraint or an ORM validator.

Indexes:
- unique(user_id, graph_conversation_id)
- index(open_action_state)
- index(due_at)
- index(waiting_on_address)

### classifications
Evidence rows produced by the pipeline in §12. Never directly cause side effects — the reducer reads these and decides.

Fields:
- id
- conversation_id
- message_id
- model_version
- rule_version
- primary_bucket
- confidence
- extracted_due_at
- extracted_defer_until
- extracted_waiting_on_address
- extracted_action_owner
- extracted_escalate_flag
- extracted_newsletter_flag
- extracted_bulk_flag
- should_create_task
- reason_short
- reasoning_json
- classifier_input_hash
- classification_review_reason  (nullable; set when confidence is below the write threshold or the rule/model pass disagreed after final override — this is the **classification-level** review flag, distinct from `conversations.state_review_reason`)
- review_status  (enum: `none` · `pending` · `resolved_accept` · `resolved_override`; default `none`)
- created_at

Indexes:
- index(conversation_id, created_at desc)
- index(message_id)
- index(review_status) where review_status != 'none'

**Two distinct review concepts** (keep them separate in code and in the review console):
- **Classification review** — "I'm not sure what bucket this is." Lives on `classifications.classification_review_reason`. Does not block Graph writes that don't depend on this classification.
- **State review** — "The reducer cannot safely advance this conversation." Lives on `conversations.state_review_reason`. Blocks **all** Graph writes for the conversation (§13.5, Invariant I7).

The review console filters by `review_type ∈ {classification, state}` — no separate table is needed in v1.

### todo_lists
Fields:
- id
- user_id
- graph_todo_list_id
- display_name
- purpose
- created_at
- updated_at

### todo_tasks
Two-way map with `conversations`: `todo_tasks.conversation_id ↔ conversations.open_action_task_id`. Both directions are persisted to avoid Graph lookups for idempotency checks.

Fields:
- id
- user_id
- conversation_id
- action_slot  (default `"primary"`; reserved for v2 multi-action decomposition — see §14.7)
- graph_todo_task_id
- graph_todo_list_id
- title
- status  (notStarted · inProgress · completed)
- completion_kind  (null · `soft` · `hard`; see §14.6)
- soft_complete_until  (nullable; window during which soft-completed tasks may auto-reopen)
- importance
- due_at
- reminder_at
- body_markdown
- linked_resource_external_id
- linked_resource_web_url
- last_synced_at
- created_at
- updated_at

Indexes:
- unique(graph_todo_task_id)
- unique(conversation_id, action_slot) where status in active states
  (v1: always `action_slot = 'primary'`, giving the effective "one active task per conversation" invariant without schema changes for v2)

### conversation_events
Append-only log per conversation. The reducer's source of truth for replay, debugging, and future ML training data. Never mutated — corrections are written as compensating events.

Fields:
- id
- user_id
- conversation_id
- event_type  (enum: `message_added` · `classified` · `state_changed` · `task_created` · `task_updated` · `task_soft_complete` · `task_hard_complete` · `task_reopened` · `override_applied` · `needs_review_raised` · `needs_review_resolved`)
- before_state
- after_state
- payload_json
- actor  (`system` · `reducer` · `user_override`)
- occurred_at
- created_at

Indexes:
- index(conversation_id, occurred_at)
- index(event_type, occurred_at)

### operation_keys
Deterministic idempotency keys for every side-effecting operation. Prevents duplicate task creation under webhook storms, retries, and out-of-order delta pages.

Fields:
- key  (primary key, string)
- operation_type  (`task_create` · `task_update` · `task_complete` · `classification` · `category_patch`)
- conversation_id
- payload_hash
- result_json
- first_applied_at

Key formulas:
- `task_create_key = sha256(conversation_id + action_slot + task_kind)`
- `classification_key = sha256(message_id + model_version + rule_version)`
- `writeback_key = sha256(conversation_id + intent + target_state + action_slot)`

A second call with the same key is a no-op that returns the stored `result_json`.

### sync_events
Fields:
- id
- user_id
- source_type
- source_id
- event_type
- cursor_or_token
- payload_json
- processed_at
- created_at

### audit_log
Fields:
- id
- user_id
- entity_type
- entity_id
- action
- before_json
- after_json
- source
- created_at

---

## 10. Canonical Internal Objects

## 10.1 Canonical Message Record

Every raw Graph message should be normalized into a canonical internal shape containing:

- message identity
- thread identity
- sender/recipient signals
- folder
- timestamps
- message body text
- user position in recipients
- reply-state
- categories
- attachment indicators
- web link / deep link if available

## 10.2 Canonical Thread Snapshot

A derived object for classification and task logic:

- latest incoming message
- latest outgoing message
- whether user sent last message
- whether user is in To or Cc on latest incoming
- unresolved asks
- latest due date
- current waiting-on person
- last meaningful action state
- task existence and status
- stale duration

---

## 11. Mail Ingestion Flow

## 11.1 Initial Backfill

1. Authenticate the user against Microsoft identity platform
2. Resolve user mailbox folders
3. Identify well-known folders:
   - Inbox
   - **Sent Items** (first-class — see §11.4; backfill must complete for both Inbox and Sent before the reducer runs, otherwise `respond_open → waiting_on` will be systematically wrong)
   - optionally Archive
4. Perform bounded historical backfill
   - recommended: last 90 to 180 days
5. Persist all messages locally
6. Build conversations
7. Run initial classification
8. Do not enable writeback/task automation until review gate passes

## 11.2 Ongoing Sync

Primary mechanism:
- Graph subscription triggers webhook notification
- notification enqueues folder sync job
- sync job runs delta for the affected folder
- changed messages are upserted
- impacted conversations are recomputed
- task sync runs for affected conversations

Fallback mechanism:
- periodic delta polling every N minutes
- used for resilience if webhook events are missed

## 11.3 Delta Sync Rules

- Persist delta token per folder
- Never assume ordering of delta pages
- Merge by Graph message ID
- Handle soft delete / move / updates correctly
- If token expires or becomes invalid, schedule controlled resync
- Keep full sync jobs observable and rate-limited

## 11.4 Sent Items is First-Class (not optional)

Sent Items participation is mandatory, not a nice-to-have. The entire `respond_open → waiting_on` transition depends on it. Without Sent Items:

- every thread where the user has already replied stays stuck in `respond_open`
- the Respond list grows unboundedly
- user loses trust in the system within days

Implementation requirements:

- Sent Items has its own delta token and its own webhook subscription — treat it exactly like Inbox
- The reducer **refuses** to run tier ③ (user-sent-last) for a conversation unless the Sent Items sync cursor is at least as recent as the conversation's `latest_received_at`. If Sent lags, the reducer either waits or raises `needs_review` rather than writing a wrong state.
- Backfill completes Sent Items before arming writeback (§11.1 step 8)

---

## 12. Classification Strategy

## 12.1 Pipeline (Three Stages + Gate)

Pipeline order: **Rules (hard) → Model → Rules (final override) → Confidence Gate**. Classification output is evidence only — it never writes tasks or categories directly (see §13 invariants).

### Stage A: Deterministic Pre-Classifier (Rules — hard)

Purpose:
Cheaply handle obvious classes and enrich features before any model call.

Rules include:
- noreply or bulk sender heuristics
- unsubscribe/list header patterns
- recipient-position rules
- direct-ask phrase detection
- due-date pattern detection
- automation/system mail detection
- thread-reply-state rules
- sent-last-message => likely waiting-on
- only cc’d and no direct ask => likely FYI

Output:
- provisional bucket
- extracted signals
- confidence band
- model-needed flag

### Stage B: Model Classifier + Extractor

Runs only when:
- deterministic confidence is below threshold
- thread state is ambiguous
- action metadata extraction is incomplete
- thread state changed materially

Model output must include:
- primary bucket
- confidence
- short rationale
- action owner
- due date
- defer date
- waiting-on entity
- escalation flag
- task recommendation
- candidate task title

### Stage C: Rule Final Override

Purpose:
Catch obvious model mistakes that should never make it to the gate, regardless of what the model said. The model can be wrong in ways deterministic rules will not be — and cheap rules are the right place to correct it.

Override rules fire *after* the model and *before* the gate. Examples:

- sender is `noreply@` / `no-reply@` / `donotreply@` → force `FYI` or `Noise`, `should_create_task=false`
- List-Unsubscribe header present and no direct ask in body → force `DeleteOrUnsubscribe`
- sender domain on user's explicit block list → force `Noise`
- sender on user's VIP allow list → raise confidence floor; never suppress to `Noise`
- message is a pure calendar accept/decline → force `FYI`
- bucket is `Respond` but latest message is user-authored and no new inbound since → force `WaitingOn`

Override rules must be small, auditable, and each tagged with a `rule_id` that appears in `reason_short` when it fires.

## 12.2 Model Output Contract

The classifier service must return structured JSON only.

Required fields:
- primary_bucket
- confidence
- reason_short
- should_create_task
- task_kind
- task_title
- due_at
- defer_until
- waiting_on
- action_owner
- escalate
- newsletter
- automated
- delete_candidate
- unsubscribe_candidate

## 12.3 Classification Thresholds

Recommended thresholds:
- High-confidence deterministic => no model call
- Medium-confidence deterministic => model confirm
- Low-confidence => model required
- Low final confidence => no writeback, queue for review

## 12.4 Rules-Only Bootstrap (valid v1 path)

The model stage is **deferrable**. A rules-only classifier is a valid first shipped version and de-risks the project by unblocking everything downstream (reducer, writeback, lifecycle) without committing to an LLM provider, data-handling policy, or prompt tuning.

Rules-only mode:

- Stage B (model) is configured off; every classification runs Rules(hard) → Rules(final override) → Gate.
- Confidence comes from rule specificity — high for decisive rules (noreply, unsubscribe header, explicit due date), medium otherwise, low when no rule matched cleanly.
- Low-confidence classifications flow to `classification_review` (the console surfaces them) instead of to writeback.
- The reducer still runs normally — state-level logic is independent of whether classification came from rules or model.

Graduation criteria before enabling the model:
- Replay precision on the curated scenario set (§21.4) is acceptable under rules-only
- Data-handling policy for the chosen LLM provider is documented
- Prompt + JSON-contract version is pinned (§12.2) and tested against golden fixtures

This matters because it separates two decisions that don't need to be coupled: "ship the system" and "pick an LLM." Either can happen first.

---

## 13. Thread State Reduction Logic

This is the most important business logic layer. The reducer is the **single brain** of the system. Classification produces evidence; only the reducer decides.

## 13.1 Principles

1. **Reducer is sole task author.** Classifier output never writes tasks or categories directly. Only the reducer, after priority resolution and idempotency checks, emits writeback intents.
2. **Conversation state is derived** from the ordered event history of the thread plus the prior `open_action_state`.
3. **Deterministic.** Same inputs → same transition, always. No wall-clock reads, no RNG, no network calls inside the reducer. `now` is passed in as a parameter.
4. **Bounded time window.** The reducer operates on the snapshot `[conversation_start … latest_event_timestamp]`. Out-of-order Graph delta pages must not flip state; late messages re-run the reducer from scratch.
5. **Append-only audit.** Every reducer run emits a `conversation_events` row. State is reconstructible by replay.

## 13.2 Priority Resolver (total-ordered, first match wins)

A single inbound email can carry contradictory signals: "thanks, done" *and* a new ask *and* the user has already replied. The reducer resolves this with a fixed priority ladder — **not a weighted score**. First matching tier wins; lower tiers are recorded as suppressed evidence in `conversation_events.payload_json`.

Tiers:

1. **Resolution signals.** Explicit completion language ("thanks, got it" / "done" / "closed" / "resolved" / "signed"), or counterparty confirmation that matches the active ask. → `done` (see §13.6 for soft vs hard).
2. **New inbound ask.** A direct question or work request addressed to the user. → `act_open` or `respond_open` (`Act` for deliverables, `Respond` for communication-only).
3. **User sent last (waiting_on).** The user's most recent reply satisfies or acknowledges the ask, and no new inbound ask has arrived since. → `waiting_on`. Requires Sent Items synced up to `latest_received_at` (§11.4).
4. **Explicit defer.** Language or metadata indicating the thread is actionable only later ("next week", "after the Friday meeting", a future date). → `deferred` with `deferred_until` set.
5. **FYI / noise.** No obligation. → `fyi_context` (useful context) or `noise_transient` (promo/bulk/newsletter).

Tie-breaking *within* a tier: newer evidence wins. Blending across tiers is forbidden.

## 13.3 Required Reducer Behaviors

1. **New inbound ask** → create or update `act_open` / `respond_open` (via tier ②).
2. **User replied last** → transition open `respond_open` to `waiting_on` (tier ③) only if Sent Items is caught up and the reply reasonably satisfies the ask.
3. **Counterparty confirms completion** → close the active task (tier ①; `soft_complete` unless the language is explicit — see §13.6).
4. **Later message changes due date** → update task metadata in place; no state transition.
5. **Earlier ask superseded** → close obsolete task, reopen only if a new ask appears (tier ②).
6. **Delegation occurs** → `delegate_open` → `waiting_on` **only with narrow handoff evidence**: an *outbound* email containing forward/assignment language OR explicit mention of a new owner. Model hint alone is insufficient (prevents hallucinated delegations).
7. **Defer date reached** → when the defer sweeper fires and `deferred_until ≤ now`, reopen to `act_open` or `respond_open` based on the original ask kind.
8. **Pure FYI follow-up** → do not disturb active task unless ownership or urgency changes.
9. **`act_open ↔ respond_open` drift is canonical** — not an edge case. Threads routinely shift between deliverable-owed and reply-owed as scope clarifies. Both directions are first-class transitions.

## 13.4 Conversation State Machine

States:
- `none` — entry / idle, no open obligation
- `act_open` — user owes a deliverable
- `respond_open` — user owes a reply
- `delegate_open` — handoff candidate, not yet transferred
- `deferred` — actionable later, `deferred_until` armed
- `waiting_on` — blocked on counterparty
- `done` — resolved (with `completion_kind = soft | hard`)
- `needs_review` — state-level ambiguity; writeback suspended (§13.5)
- `fyi_context` — useful context, no action
- `noise_transient` — promo/bulk/newsletter

Transitions must be explicit, auditable, and enumerated in code as a transition table (not scattered `if` branches). The transition table is the authoritative spec — visual diagram in `state-machine.html` mirrors it.

## 13.5 State-level `needs_review`

Distinct from classification-level review queue (§12). `needs_review` is raised by the reducer when state itself is unsafe to advance, for example:

- rules and model disagree on the bucket *after* the final override pass
- Sent Items cursor lags `latest_received_at` so tier ③ cannot be safely evaluated
- two contradictory high-confidence signals in the same tier
- existing active task conflicts with the proposed transition (dedupe violation)
- writeback has failed repeatedly for this conversation (see dead-letter, §18.3)

While in `needs_review`:

- **all Graph writes for the conversation are suspended** (no task create/update, no category patch)
- the conversation surfaces in the review console with the reason
- resolution is either manual override or a later disambiguating message that lets the reducer re-run cleanly

## 13.6 Soft vs Hard Completion

Two kinds of `done`:

- **`soft_complete`** — model-inferred resolution ("sounds like we're good"). Reversible. A later inbound message within `soft_complete_until` can auto-reopen the task without requiring a new explicit ask.
- **`hard_complete`** — explicit resolution language ("done" / "closed" / "resolved" / "signed" / counterparty confirmation matching the active ask). Sticky. Only a **new ask** (tier ②) reopens it — FYI follow-ups never reopen a hard-complete.

Default soft window: 7 days, configurable. `soft_complete_until` is persisted on the task row.

## 13.7 Multi-Action Threads (v1 vs v2)

v1 enforces **one active task per conversation** (`unique(conversation_id, action_slot='primary') where active`). This is correct for v1 and covers the vast majority of threads.

The schema already reserves `todo_tasks.action_slot` so v2 can decompose a mixed ask ("Can you send the deck **and** confirm the timeline?") into two parallel active tasks without migration. v1 must not add code that assumes "at most one row per conversation" — only "at most one row per `(conversation, action_slot)`".

---

## 14. Microsoft To Do Design

## 14.1 Core Requirement

All task creation and lifecycle changes must be performed through **Microsoft Graph To Do APIs**, not local-only reminders and not Outlook flagging as a substitute.

## 14.2 Task Lists

Create or reconcile the following lists:

- `Email - Act`
- `Email - Respond`
- `Email - Waiting On`
- `Email - Delegated`

Optional:
- `Email - Review`
- `Email - Deferred`

The system should:
- discover existing lists by name
- create missing lists
- persist Graph list IDs locally

## 14.3 Task Creation Rules

Create a To Do task when:
- primary bucket is `Act`, `Respond`, or `WaitingOn`
- `Delegate` is enabled as a tracked personal bucket
- confidence is above write threshold
- no active task exists for that conversation

Do not create a task when:
- bucket is `FYI`
- bucket is `DeleteOrUnsubscribe`
- confidence is below threshold
- action is already completed/resolved by later thread state

## 14.4 Task Payload Design

Each To Do task should include:

### Title
Short imperative title, e.g.:
- `Reply to Sarah about budget approval`
- `Prepare revised contract for vendor`
- `Waiting on Tom for signed SOW`

### Body
Structured markdown/plain text summary containing:
- source email subject
- latest sender
- received date
- classifier reason
- extracted due date
- waiting-on field
- conversation/web link
- internal conversation ID for debugging

### Metadata
- importance from urgency mapping
- dueDateTime if extracted with confidence
- reminderDateTime only if explicitly enabled in policy

### Linked Resource
Every created task must include a `linkedResource` to the originating email or conversation URL when available.

Store:
- externalId = stable internal conversation key or Graph message ID
- webUrl = Outlook web link or deep link to the email/thread
- applicationName = app identifier
- displayName = email subject or concise label

## 14.5 Task Update Rules

If active task exists for conversation:

- update title if the primary ask materially changed
- update due date if new due date is more recent/reliable
- update body summary on meaningful thread changes
- move between task lists if bucket changes
- mark completed when thread resolves (soft vs hard — §14.6)
- reopen if later thread reactivates work (subject to soft/hard rules)

## 14.6 Completion: Soft vs Hard

Two completion kinds are persisted on `todo_tasks.completion_kind`:

- **`soft`** — model-inferred resolution. Graph task is marked completed, but `soft_complete_until` is set (default: +7 days). If a new inbound message arrives within that window, the reducer auto-reopens the task (re-activates the Graph task via PATCH) rather than creating a new one. Prevents premature closes from producing annoying "new task" churn when the thread was actually still live.
- **`hard`** — explicit resolution signal (counterparty confirmation matching the active ask, or language like "done / closed / resolved / signed"). Sticky. Only a **new ask** (priority tier ②) reopens it; FYI-class follow-ups do not.

Implementation notes:
- Graph-side, both look identical (`status=completed`). The soft/hard distinction is local state.
- Reopens reuse the same `graph_todo_task_id` when possible; a brand-new Graph task is only created if the old one has been user-deleted.
- Every soft→hard promotion and every reopen emits a `conversation_events` row.

## 14.7 Task Deduplication and Multi-Action

Dedupe key: `unique(conversation_id, action_slot) where status in active states`.

- v1: `action_slot` is always `"primary"` — this collapses to the "one active task per conversation" invariant.
- Dedupe lookup order: (1) `operation_keys.task_create_key`, (2) active task row for `(conversation_id, "primary")`, (3) fallback to normalized subject + participants + time window only if `graph_conversation_id` is missing.
- v2 (future, non-blocking): the `action_slot` column allows decomposing mixed asks ("send the deck **and** confirm the timeline") into parallel active tasks without schema migration. v1 code must treat `action_slot` as a real key, not hardcode the single-row assumption.

---

## 15. Mailbox Writeback Design

## 15.1 Categories

Apply user-visible Outlook categories. The category set mirrors `conversations.open_action_bucket`, not `open_action_state`:

| `open_action_bucket` | Category applied |
|---|---|
| `Act` | `AI-Act` |
| `Respond` | `AI-Respond` |
| `WaitingOn` | `AI-Waiting` |
| `Delegate` | `AI-Delegate` |
| `Defer` | `AI-Deferred` (optional; fallback to prior bucket's category if `AI-Deferred` not desired) |
| `FYI` | `AI-FYI` |
| `DeleteOrUnsubscribe` | `AI-Noise` |

Writeback rules:

- **When `open_action_state = needs_review`**: **skip category writeback entirely.** The mailbox keeps whatever category it had. Consistent with Invariant I7 (needs_review blocks all Graph writes).
- **When `open_action_state = done`**: behavior is policy-configurable.
  - `done_category_policy = "clear"` (default): remove all `AI-*` categories on the thread's latest message; preserve user categories.
  - `done_category_policy = "fyi"`: replace with `AI-FYI` to keep the thread visibly classified.
  - `done_category_policy = "preserve"`: leave the prior `AI-*` category as-is.
- Before applying a new `AI-*` category, remove every other `AI-*` category to avoid stacking.
- Non-`AI-*` categories (user-created) are never touched.
- Category writeback is subject to the same idempotency envelope (`operation_keys.writeback_key`) as task writes — repeated reducer runs with the same intent must not issue redundant Graph PATCHes.

## 15.2 Optional Graph Extension Payload

Use one compact app-owned extension payload if mailbox-resident state is needed.

Recommended contents:
- classifier_version
- last_bucket
- last_confidence
- linked_task_id
- last_processed_at

Do not create many separate extensions.
Do not store large model traces.
Local DB remains primary state store.

---

## 16. API and Service Boundaries

## 16.1 Graph Client Module

Responsibilities:
- auth token injection
- retries with backoff
- paging
- delta token handling
- subscription creation/renewal
- task and message patch wrappers
- rate limit handling

## 16.2 Ingestion Service

Responsibilities:
- folder discovery
- full sync
- delta sync
- message upsert
- enqueue downstream recompute jobs

## 16.3 Classification Service

Responsibilities:
- feature derivation
- deterministic rules
- model invocation
- classification persistence
- confidence gating

## 16.4 Conversation Reducer

Responsibilities:
- aggregate message history
- infer conversation state
- produce desired task state and mailbox label state

## 16.5 Task Sync Service

Responsibilities:
- task list discovery/creation
- task create/update/complete/reopen
- linked resource creation
- task audit logging

## 16.6 Review API / UI

Responsibilities:
- list recent classifications
- show why a task exists
- allow manual override of bucket
- allow suppress/reopen
- show sync errors and Graph failures

---

## 17. End-to-End Processing Flows

## 17.1 New Incoming Email Creates Action

1. Graph subscription event arrives
2. Folder delta job runs
3. Message upserted
4. Conversation snapshot rebuilt
5. Deterministic rules fire
6. Model confirms `Respond`
7. Reducer decides active task required
8. To Do Sync creates task in `Email - Respond`
9. Linked resource added
10. Message category patched to `AI-Respond`
11. Audit log written

## 17.2 User Sends Reply, Thread Becomes WaitingOn

1. Sent Items delta fetches outgoing message
2. Conversation snapshot rebuilt
3. Reducer sees user sent latest substantive reply
4. Existing `Respond` task updated or moved to `Email - Waiting On`
5. Category changed to `AI-Waiting`
6. Audit log written

## 17.3 Counterparty Resolves Thread

1. Inbound reply arrives
2. Classifier detects completion/acknowledgment
3. Reducer closes active state
4. To Do task marked completed via Graph
5. Category changed to `AI-FYI` or cleared based on policy

---

## 18. Error Handling and Recovery

## 18.1 Graph API Failures

Handle:
- token expiration
- permission failures
- throttling
- transient 5xx
- subscription expiration
- invalid delta token
- item not found after move/delete

Recovery rules:
- retry with exponential backoff for transient failures
- force token refresh when auth failures indicate expiry
- recreate subscriptions before expiry
- resync folder when delta token invalidates
- mark entities as tombstoned on permanent delete

## 18.2 Idempotency

Every write action must be safe to retry.

Use:
- local operation keys
- conversation-level locks
- compare-before-update
- persisted task mapping
- audit records for replay investigation

## 18.3 Dead Letter / Review Queue

Any of the following should send a conversation to review:
- repeated Graph write failures
- low-confidence classification with pending automation
- conflicting reducer outcomes
- duplicate active task detection
- missing linked resource / malformed web link

---

## 19. Security and Privacy

1. Single-user delegated access only
2. Encrypt refresh tokens and secrets at rest
3. Minimize retained message body if desired by policy
4. Separate raw content from derived features
5. Log only safe excerpts in audit tables
6. Provide data purge utility
7. Keep destructive actions disabled by default
8. Do not transmit mailbox data to third-party services unless explicitly configured

If using external LLM APIs:
- define data retention policy
- redact unnecessary sensitive content where feasible
- allow local-model fallback in future

---

## 20. Observability

## 20.1 Metrics

Track:
- messages synced per hour
- delta job duration
- webhook lag
- classifier invocation count
- deterministic vs model classification rate
- active tasks by bucket
- duplicate task prevention count
- task create/update/complete counts
- Graph error rates by endpoint
- human override rate

## 20.2 Logs

Structured logs with:
- correlation ID
- conversation ID
- Graph request class
- reducer transition
- task sync outcome
- writeback outcome
- retry count

## 20.3 Audit Events

Must record:
- task created
- task updated
- task completed
- category changed
- classification overridden
- conversation state transition

---

## 21. Testing Strategy

## 21.1 Unit Tests

Cover:
- deterministic classification rules
- due date extraction
- recipient-position logic
- reducer transitions
- dedupe logic
- category mapping
- task title generation

## 21.2 Integration Tests

Against Graph test tenant or isolated mailbox:
- full mailbox backfill
- delta sync paging
- subscription renewal
- To Do list discovery/creation
- task creation/update/completion
- linked resource creation
- category patching
- extension write/read if enabled

## 21.3 Replay Tests

Build a replay harness with captured anonymized email threads to verify:
- classification output stability
- reducer correctness
- no duplicate tasks across reruns

## 21.4 Manual Review Tests

Curate scenario sets:
- direct ask to user
- cc-only informational mail
- newsletter noise
- user replied and is waiting
- due date moved
- thread resolved
- delegate candidate
- ambiguous mixed ask

---

## 22. Rollout Plan

## Phase 0: Setup
- App registration
- Auth flow
- Database schema
- Graph client
- Folder discovery

## Phase 1: Read-Only Sync
- Inbox + Sent Items backfill
- delta sync
- local persistence
- no automation writes

Deliverable:
- stable message/conversation store

## Phase 2: Offline Classification
- implement rules
- implement model extraction
- review console for recent decisions
- no mailbox/task writeback

Deliverable:
- acceptable classification precision on historical mail

## Phase 3: Mailbox Annotation
- apply Outlook categories
- optional extension writeback
- still no task creation

Deliverable:
- user can validate classifications in Outlook

## Phase 4: Microsoft To Do Creation via Graph
- create/reconcile To Do lists
- create/update/complete tasks
- add linked resources
- enforce dedupe

Deliverable:
- active actionable threads appear in Microsoft To Do correctly

## Phase 5: Full Thread Lifecycle
- Sent Items aware waiting-on transitions
- reopen/complete behavior
- defer logic
- daily review summary

Deliverable:
- low-noise autonomous task maintenance

## Phase 6: Productivity Enhancements
- stale waiting-on reminders
- unsubscribe suggestion queue
- summary reports
- manual override learning loop

---

## 23. Acceptance Criteria

## Must Have

1. Sync new and changed mail incrementally without full rescans
2. Maintain accurate local conversation state
3. Classify conversations into one primary bucket
4. Create Microsoft To Do tasks via Graph for `Act`, `Respond`, and `WaitingOn`
5. Add linked resources from task back to source email/thread
6. Prevent duplicate active tasks per conversation
7. Mark tasks complete when thread resolves
8. Apply and maintain Outlook categories
9. Provide auditability for each automation decision
10. Recover safely from Graph token/subscription/delta issues

## Quality Thresholds for Beta

- >90% precision on actionable vs non-actionable
- <2% duplicate active task rate
- >95% successful Graph write rate after retries
- <5 minutes average end-to-end lag from new email to task appearance
- manual override rate low enough to make the system trustworthy

---

## 24. Open Decisions for the Implementer

Decided (see §7, §11, §13, §14):
- ~~Backend language/framework~~ → **Python 3.11+ / FastAPI / SQLAlchemy 2.x async**
- ~~Database~~ → **SQLite (WAL)** for v1 solo prototype; portable to Postgres if scale changes
- ~~Local vs hosted~~ → **single-process on an always-on workstation**; no Docker required
- ~~Sent Items optional~~ → **mandatory, first-class** (§11.4)
- ~~Delegate as tracked bucket~~ → **yes, first-class list in v1**

Still open:

1. LLM provider and data handling policy (Anthropic / OpenAI / local) — deferrable; v1 ships rules-only (§12.4)
2. Exact threshold values for auto-write vs review (tune empirically on replay set)
3. Graph open extensions in v1 vs DB-only internal state
4. Per-sender / per-domain allow/block tuning surface
5. Soft-complete window default (initial: 7 days)
6. `done_category_policy` default (`clear` vs `fyi` vs `preserve`)
7. Whether to parse attachments for action extraction in v2
8. Whether to implement v2 multi-action decomposition at all, or keep one-task-per-conversation permanently

### 24.1 Rollout Conservatism for `Delegate`

`Delegate` is the least stable bucket in personal email because handoff intent is inherently ambiguous. The schema keeps it first-class, but rollout policy should be conservative:

- `delegate_open` is classified freely, but task behavior defaults to tracking the user, not the delegatee.
- Transition to `waiting_on` requires the narrow handoff evidence defined in §13.3 #6 and Invariant I11 (outbound forward/assignment language OR explicit new-owner mention).
- Low-confidence delegate classifications go to `classification_review` rather than writeback.
- Re-evaluate after N weeks of production use whether to relax.

---

## 25. Suggested Milestone Breakdown

### Milestone 1: Foundations
- app registration
- delegated auth
- graph client
- DB schema
- folder discovery
- full sync pipeline

### Milestone 2: Incremental Ingestion
- folder delta implementation
- webhook subscription endpoint
- subscription renewal job
- sync observability

### Milestone 3: Classification Core
- canonical thread snapshot
- rules engine (Stage A) + rule final override (Stage C)
- confidence gate + classifications table
- persisted classifications with `classification_review_reason`
- **ship rules-only first (§12.4); model adapter is a later, independently-scoped milestone**

### Milestone 3b (optional, deferred): Model Adapter
- LLM provider selection + data-handling policy
- Stage B model adapter with versioned prompts and Pydantic JSON contract
- golden-file tests for the classifier JSON contract
- enable only after replay precision under rules-only is acceptable

### Milestone 4: Conversation Reducer
- open action state machine
- due date and waiting-on logic
- dedupe and reopening rules

### Milestone 5: Microsoft To Do Sync
- task list creation/reconciliation
- task create/update/complete
- linked resource support
- task mapping persistence

### Milestone 6: Mailbox Writeback
- category patching
- optional extension payload
- AI category cleanup logic

### Milestone 7: Review Console + Hardening
- review UI
- manual override
- replay harness
- operational dashboards
- recovery jobs

---

## 26. Deliverables

1. Source repository
2. Deployment instructions
3. Environment variable manifest
4. Database migrations
5. Graph app registration instructions
6. Permissions/consent documentation
7. Runbook:
   - token issues
   - subscription expiry
   - delta reset
   - duplicate task remediation
8. Admin/review UI
9. Test suite
10. Replay dataset harness
11. Architecture decision records
12. Production readiness checklist

---

## 27. Implementation Notes for the Senior Developer

- Treat the mailbox as event-sourced input and the conversation reducer as the domain core.
- Do not let classifier outputs directly create tasks without reducer mediation (Invariant I1).
- Build dedupe and idempotency (`operation_keys` table) before enabling task writes.
- Write the reducer as a **pure function**: `(snapshot, prior_state, now) -> (next_state, side_effect_intents, events)`. No I/O, no wall-clock reads, no RNG. All side effects are applied by the outer writeback layer via `operation_keys`.
- Express transitions as an explicit transition table, not scattered conditionals. Golden-file test every cell.
- Keep the first shipped behavior conservative; default to `needs_review` on ambiguity.
- Ensure Sent Items participates from day one (§11.4); without it, waiting-on behavior will be materially wrong.
- Keep the classifier contract versioned (`model_version`, `rule_version`); persist on every classification row.
- Keep Graph interaction wrappers thin and observable. Every PATCH/POST carries an operation key and logs request/response summaries.
- v1 = one active task per `(conversation_id, 'primary')`. Do not hardcode the "one row per conversation" assumption — code against `action_slot`.
- SQLite specifics: one writer at a time. Serialize writes per-conversation with an in-process asyncio lock keyed by `conversation_id`. Keep transactions short.

---

## 28. Definition of Done

The project is done for v1 when:

- a new actionable email arrives,
- the system ingests it through Graph,
- classifies it,
- creates or updates the correct Microsoft To Do task through Graph,
- links that task back to the originating message/thread,
- applies the correct Outlook category,
- and later completes or transitions that task automatically as the thread evolves,

with reliable observability, no duplicate tasks, and safe recovery from normal Microsoft Graph operational failures.

---

## 29. Hard Invariants

Enforce in code, not just in docs. Each invariant should have a test that would fail if it were violated.

- **I1 · Reducer is sole task author.** Classification never writes tasks. Only the reducer, after priority resolution and idempotency checks, emits writeback intents.
- **I2 · One active task per `(conversation_id, action_slot)`.** v1: `action_slot = 'primary'` always. Enforced by DB unique index and by `operation_keys.task_create_key`.
- **I3 · Sent Items must be in the snapshot.** Reducer refuses `respond_open → waiting_on` unless Sent Items sync cursor ≥ `latest_received_at`.
- **I4 · Priority tiers are total-ordered.** Tier ① short-circuits every lower tier. No weighted blending across tiers.
- **I5 · Soft-complete is reversible within `soft_complete_until`.** A later inbound within the window auto-reopens. Hard-complete is sticky — only a new ask reopens it.
- **I6 · Every writeback is idempotent.** Each side effect carries an `operation_key`; a second call with the same key is a no-op returning the stored result.
- **I7 · `needs_review` blocks writeback.** All Graph writes suspended for the conversation until resolved (manual or by a later disambiguating message).
- **I8 · `conversation_events` is append-only.** State is derivable by replay. Corrections are compensating events, not edits.
- **I9 · Reducer is deterministic.** Same `(snapshot, prior_state, now)` → same `(next_state, intents, events)`. No wall-clock reads, no RNG, no network calls inside the reducer.
- **I10 · Reducer operates on a bounded time window.** Snapshot = `[conversation_start … latest_event_timestamp]`. Out-of-order delta pages must not flip state; late messages re-run the reducer from scratch.
- **I11 · Handoff evidence is narrow.** `delegate_open → waiting_on` requires outbound email with forward/assignment language OR explicit new-owner mention. Model hint alone is insufficient.

- **F1 · Future multi-action support is not blocked.** `todo_tasks.action_slot` exists in v1 even though only `'primary'` is used, so a future v2 can decompose mixed asks into parallel tasks without migration.