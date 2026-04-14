# Runbook

Operational reference for day-2 issues. Companion to `project-plan.md` §18
(failures / resilience).

## Token issues

Tokens live at `MS_GRAPH_TOKEN_STORE_PATH` (default `.email_intel/tokens.json`,
MSAL `SerializableTokenCache`). Symptoms of a bad cache: every Graph call
raises `GraphAuthError("no cached account")` or `GraphHTTPError(401, ...)`
twice in a row.

Recovery — re-run the device flow using the configured AuthProvider:

```python
import asyncio
from email_intel.config import get_settings
from email_intel.graph.auth import build_auth_provider

async def main():
    a = build_auth_provider(get_settings())
    flow = await a.start_device_flow()   # MsalPublicAuthProvider only
    print(flow["message"])                # visit the URL, paste the code
    await a.complete_device_flow(flow)

asyncio.run(main())
```

If the cache is corrupt, delete the store file and repeat.

### Conditional-access block on the default public client

If device flow fails with `AADSTS` errors mentioning the application being
unapproved, the tenant's Conditional Access is rejecting the default public
client ID (Azure CLI's). Options:

1. Ask IT for an approved client ID, set `MS_GRAPH_CLIENT_ID` +
   `MS_GRAPH_TENANT_ID`, and flip `AUTH_MODE=msal_app`.
2. Acquire a bearer from Graph Explorer; set `AUTH_MODE=static` and paste it
   into `MS_GRAPH_STATIC_TOKEN` (bootstrap / short-lived only — no refresh).

No code changes required — `AuthProvider` is pluggable.

## Subscription expiry / renewal failures

Graph mailbox subscriptions expire at most ~3 days. The
`subscription_renewal` scheduler job runs every 2 hours and renews anything
expiring in the next 12 hours. Failures:

- Check `sync_events` rows with `event_type='subscription_renew_failed'`.
- Inspect `mail_folders.subscription_expires_at`; if already in the past,
  delete the row's `subscription_id` + `subscription_expires_at` and let
  the ingestion bootstrap re-create it on next startup.
- Manual one-shot:

```sql
UPDATE mail_folders
SET subscription_id = NULL, subscription_expires_at = NULL
WHERE id = <folder_id>;
```

## Delta reset (HTTP 410)

Delta-link invalidation triggers a one-shot reset: `delta_sync` clears
`mail_folders.delta_token` and re-runs. The event is recorded in
`sync_events` with `event_type='delta_token_invalid_reset'`.

To manually force a reset (e.g. after out-of-band mailbox rules):

```sql
UPDATE mail_folders SET delta_token = NULL WHERE id = <folder_id>;
```

Next scheduled `delta_fallback_poll` (or a webhook notification) will
re-hydrate messages for that folder.

## Duplicate task remediation

Symptoms: two `todo_tasks` rows for the same `conversation_id` with
`status IN ('notStarted','inProgress')`. The partial-unique index
`uq_todo_active_per_conv_slot` should prevent this, but bugs in operation
keys can produce orphans.

Inspect:

```sql
-- conversations with more than one active task
SELECT conversation_id, COUNT(*) FROM todo_tasks
WHERE status IN ('notStarted','inProgress')
GROUP BY conversation_id HAVING COUNT(*) > 1;

-- operation keys for a suspect conversation
SELECT * FROM operation_keys WHERE conversation_id = <cid>
ORDER BY first_applied_at DESC;
```

Remediate by soft-completing all but the freshest task via the Graph To Do
UI, then UPDATE `todo_tasks.status='completed'`. Re-run the reducer for
that conversation via the review console's "override" action.

## Dead-letter inspection

Reducer G7: conversations in `needs_review` block writeback. Find them:

```sql
SELECT id, canonical_subject, state_review_reason, updated_at
FROM conversations
WHERE open_action_state = 'needs_review'
ORDER BY updated_at DESC;
```

Inspect `conversation_events` for the `needs_review_raised` payload to
understand which guard fired (T080 = sent-items lag, T081 = signal
conflict, T082 = writeback failure threshold).

Clear via the review console:

- **Override** (`/review/conversations/<id>/override`): set a target state
  + bucket; records a `override_applied` event, clears
  `state_review_reason`.
- **Clear review** (`/review/conversations/<id>/clear-review`): clears the
  reason without changing state.

Both re-enable writeback on the next reducer run.

## Restoring from backup

Stop the service before copying the SQLite file. WAL + SHM must be copied
together or flushed first:

```bash
systemctl stop email-intel                  # or kill the uvicorn pid
sqlite3 email_intel.db "PRAGMA wal_checkpoint(TRUNCATE);"
cp email_intel.db /backup/email_intel.$(date +%F).db
# ... restore:
cp /backup/email_intel.2026-04-13.db email_intel.db
systemctl start email-intel
```

## Bumping `reducer_version` or `rule_version`

Both live in `config.py` as `REDUCER_VERSION` / `CLASSIFIER_RULE_VERSION`.

- **Classifier rule bumps invalidate `classifications.classifier_input_hash`
  uniqueness.** The hash embeds `rule_version`, so new runs compute a new
  hash and append a fresh classification row. Old rows remain as history.
- **Reducer version bumps** do not require cache invalidation — the reducer
  is a pure function and every run emits fresh events with the bumped
  version in its payload.

Procedure:

1. Bump the constant in `src/email_intel/config.py`.
2. Restart the service.
3. (Optional) Kick off `run_full_reducer_cycle` from a one-off script if
   you want every conversation re-evaluated immediately; otherwise the
   next webhook-driven sync handles it incrementally.

## Useful ad-hoc SQL

```sql
-- Recent reducer transitions
SELECT conversation_id, event_type, payload_json, occurred_at
FROM conversation_events
WHERE actor = 'reducer'
ORDER BY occurred_at DESC LIMIT 50;

-- Dead-letter retry candidates (failures in last 24h)
SELECT conversation_id, COUNT(*) FROM conversation_events
WHERE event_type = 'needs_review_raised'
  AND occurred_at > datetime('now','-1 day')
GROUP BY conversation_id;
```
