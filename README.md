<div align="center">

<img src="docs/banner.svg" alt="Obsidian SignalShard" width="900">

</div>

<details>
<summary>Plain-text banner (for terminals / <code>cat README.md</code>)</summary>

```
        ◆◆◆◆◆◆◆◆◆
      ╱▓▓▓▓▓▓▓▓▓▓▓▓▓╲
     ╱▓▓▒▒▒▒▒▒▒▒▒▓▓╲
    ╱▓▓▒░░░░░░░░░▒▓▓╲         O B S I D I A N
   ╱▓▓▒░░░░░░░░░░░▒▓▓╲
   ╲▓▓▒░░░░░░░░░░░▒▓▓╱    ╔═╗╦╔═╗╔╗╔╔═╗╦  ╔═╗╦ ╦╔═╗╦═╗╔╦╗
    ╲▓▓▒░░░░░░░░░▒▓▓╱     ╚═╗║║ ╦║║║╠═╣║  ╚═╗╠═╣╠═╣╠╦╝ ║║
     ╲▓▓▒▒▒▒▒▒▒▒▒▓▓╱      ╚═╝╩╚═╝╝╚╝╩ ╩╩═╝╚═╝╩ ╩╩ ╩╩╚══╩╝
      ╲▓▓▓▓▓▓▓▓▓▓▓▓▓╱
        ◆◆◆◆◆◆◆◆◆      ── conversation state as the source of truth ──
```

</details>

**Turn your inbox into a task system that maintains itself.**

SignalShard watches your Outlook mailbox, understands what you owe, and keeps
your task list in sync — automatically.

No rules to maintain. No manual triage. No "I'll get to that later."

Under the hood: a deterministic reducer + state machine that converts
unstructured communication into actionable state. See `project-plan.md` §1
for full scope.

## What happens when you run this

A concrete walkthrough of one conversation's lifecycle:

1. **An email arrives** asking you to send over a signed document.
   → SignalShard classifies it as **Act** and creates a task in your
   Microsoft To Do list.
2. **You reply** with the attachment.
   → The reducer sees your outgoing message, transitions the conversation
   to **WaitingOn**, and updates the task's category accordingly.
3. **They confirm receipt.**
   → State moves to **Done**; the task is completed for you.
4. **A thank-you-only reply lands later.**
   → Classified as **Noise**; no task churn, no notification.

You never touched the task list. Conversation state *is* the task state.

> **SignalShard runs fully deterministic in v1 — no LLM required.**
> The classifier is rules + overrides + a gate. Replays are cheap,
> behavior is auditable, and there is no model dependency to babysit.
> See [Rules-only v1](#rules-only-v1) below.

## Requirements

- Python 3.11 or newer
- A Microsoft 365 mailbox
- A writable directory for the SQLite database + token cache

> **App registration is optional.** Some enterprise tenants block users from
> creating their own Entra ID (Azure AD) app registrations. This project
> works in either case — see **Auth modes** below.

## Install and first run

```bash
git clone <repo> && cd email-intelligence
python -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt -e .       # pinned versions (recommended)
# or, unpinned from pyproject extras:
# pip install -e .[dev]

cp .env.example .env
# Default .env works out of the box (AUTH_MODE=device).
# Only set MS_GRAPH_CLIENT_ID / MS_GRAPH_TENANT_ID if you have your own
# app registration and want AUTH_MODE=msal_app.

alembic upgrade head                        # apply schema
uvicorn email_intel.app:app --reload        # start the app
```

## Auth modes

Auth is pluggable behind an `AuthProvider` protocol (`src/email_intel/graph/auth.py`).
Graph client, reducer, classifier, and writeback never depend on the concrete
backend — swapping modes is a config change.

| `AUTH_MODE` | When to use | What it requires |
|---|---|---|
| `device` (default) | Tenants that block self-service app registration, or anyone who just wants to start | Nothing — falls back to a well-known public client ID (Azure CLI) and the `organizations` authority |
| `msal_app` | You have an IT-approved app registration with the required delegated scopes | `MS_GRAPH_CLIENT_ID` and `MS_GRAPH_TENANT_ID` in `.env` |
| `static` | You pasted a bearer token from Graph Explorer or another CLI; one-shot bootstrap / testing | `MS_GRAPH_STATIC_TOKEN` or `MS_GRAPH_STATIC_TOKEN_PATH` |

**No client-secret flow is supported or exposed.** Delegated auth only.

### Device-flow bootstrap (one-time)

```python
# python -c ... or a small bootstrap script
import asyncio
from email_intel.config import get_settings
from email_intel.graph.auth import build_auth_provider

async def main():
    auth = build_auth_provider(get_settings())
    flow = await auth.start_device_flow()
    print(flow["message"])  # visit https://microsoft.com/devicelogin, paste code
    await auth.complete_device_flow(flow)

asyncio.run(main())
```

Tokens persist to `MS_GRAPH_TOKEN_STORE_PATH` (default `.email_intel/tokens.json`);
re-bootstrap only on revocation.

### If the default public client is blocked

Some tenants restrict even Microsoft's own public client IDs via Conditional
Access. If device-flow consent fails with `AADSTS-` errors, either:

1. Ask IT for the client ID of an approved tool (often Microsoft Graph
   PowerShell or an internally-registered app) and set `MS_GRAPH_CLIENT_ID` +
   `AUTH_MODE=msal_app`, or
2. Acquire a short-lived token via Graph Explorer and run with
   `AUTH_MODE=static` while you figure out a durable arrangement.

## Rules-only v1

Per project-plan §12.4 the classifier runs **Stage A (rules) → Stage C
(override rules) → Gate** with `LLM_PROVIDER=none`. There is no Stage B
(model) dependency; everything is deterministic, which makes replays and
unit tests cheap. Adding an LLM is a future wave.

## Background workers

The scheduler starts inside the FastAPI lifespan, so there is **no separate
worker process**. Four jobs run automatically (`src/email_intel/scheduler.py`):

| Job                    | Cadence | Purpose                                                |
|------------------------|---------|--------------------------------------------------------|
| `subscription_renewal` | 2 h     | Renew Graph subscriptions expiring within 12 h         |
| `delta_fallback_poll`  | 10 m    | Full `sync_all_folders` when no webhook activity seen  |
| `defer_sweeper`        | 1 m     | Re-run reducer for conversations whose defer timer fired |
| `dead_letter_health`   | 30 m    | Log count of conversations stuck in `needs_review`     |

Webhook handoff: `/graph/notifications` enqueues jobs on the in-process
`JobQueue`. The single-writer lock (`acquire_conversation_lock`) keeps
SQLite safe.

## Review console

Once the app is running, the operator UI lives at:

```
http://127.0.0.1:8000/review/
```

Two queues: reducer state-review and classifier classification-review, plus
a recent-events timeline. Overrides record intent via `conversation_events`
(project-plan §16).

## Testing

```bash
pytest                              # full suite (222 tests)
mypy --strict src/email_intel/      # strict type check
ruff check src/email_intel/         # lint
```

## SQLite single-writer note

SQLite + aiosqlite is effectively single-writer. All reducer + writeback
code paths acquire an in-process asyncio lock keyed by conversation ID:

```python
from email_intel.db.session import acquire_conversation_lock

async with acquire_conversation_lock(cid):
    # reducer + writeback for this conversation
    ...
```

PRAGMAs applied on every connection: `journal_mode=WAL`,
`synchronous=NORMAL`, `foreign_keys=ON`, `busy_timeout=5000`.

## Why this exists

Inboxes are reactive systems. Tasks drift out of sync with the threads that
spawned them. Humans are bad at tracking state across dozens of parallel
conversations — "did I reply?", "am I waiting on them?", "is this still
alive?" — and the cost of getting it wrong is quiet dropped balls.

SignalShard treats the conversation itself as the source of truth for task
state. The reducer derives what you owe from what was actually said, so your
task list can't drift — because nothing is typed into it by hand.

## Where to go next

- `project-plan.md` — full specification: data flows (§11), end-to-end
  pipeline (§17), deliverables (§26)
- `reducer-spec.md` — transition matrix reference
- `state-machine.html` — rendered state diagram
- `docs/runbook.md` — operator troubleshooting
- `docs/architecture-decision-records/` — ADRs for SQLite, rules-only,
  pure reducer

## License

Licensed under Apache 2.0. See [LICENSE](LICENSE) for details.

## Layout

```
src/email_intel/
  app.py              # FastAPI entrypoint + lifespan wiring
  pipeline.py         # end-to-end classify → reduce → writeback orchestrator
  scheduler.py        # APScheduler job registration
  config.py           # pydantic-settings (env-driven)
  db/                 # Base, async engine, session, ORM models
  schemas/            # snapshot, classifier, reducer, intents, events
  reducer/            # transition table + pure reducer
  graph/              # Microsoft Graph client (mail + todo + subs)
  ingestion/          # delta sync, webhook, normalizer, snapshot builder
  classify/           # rules pipeline + override + gate
  writeback/          # task/category writeback + operation keys + dead-letter
  review/             # review console routes, templates, static
```
