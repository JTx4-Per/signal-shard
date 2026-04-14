# ADR-004 · Pluggable auth (`AuthProvider`) with device mode as default

## Status
Accepted.

## Context
During auth setup we discovered the work tenant blocks self-service creation
of Entra / Azure AD app registrations. The original design assumed we would
own an app registration and hard-coded MSAL wiring into the Graph client.

Graph access itself is still viable via delegated/device-based auth using
well-known Microsoft public client IDs (e.g. Azure CLI's), but:
- We cannot rely on having our own client_id/tenant_id.
- A tenant that blocks app registration today may approve one later — we
  should not have to rewrite the Graph client when that happens.
- Engineers and tests should not be entangled with MSAL specifics.

## Decision
Auth is a pluggable infrastructure concern behind the `AuthProvider` protocol:

```python
class AuthProvider(Protocol):
    async def get_access_token(self) -> str: ...
    async def refresh_if_needed(self) -> None: ...
```

Three backends ship in v1, selected via `AUTH_MODE`:

- **`device`** (default) — `MsalPublicAuthProvider` with a well-known public
  client ID (Azure CLI) and `organizations` authority. Requires no env config.
- **`msal_app`** — same class, but with user-supplied `MS_GRAPH_CLIENT_ID` /
  `MS_GRAPH_TENANT_ID`. Reserved for when an app registration is granted.
- **`static`** — `StaticTokenAuthProvider`; returns a pre-acquired bearer
  token. Bootstrap / tests only; no refresh.

**No client-secret flow** is supported or exposed.

## Consequences
- `GraphClient` depends only on `AuthProvider`. Tests mock the protocol, not
  MSAL. Reducer / classifier / ingestion / writeback are untouched.
- Switching auth mode is a config change, not a code change.
- The `GraphAuth` name remains as an alias to `MsalPublicAuthProvider` for
  back-compat with existing imports.
- If the Azure CLI public client is blocked by Conditional Access, operators
  can pivot to `msal_app` with an IT-approved client_id or to `static` for a
  short-lived bootstrap — without any code changes.

## Non-goals
- We do **not** ship a broker-based or WAM-backed provider in v1.
- We do **not** ship Fernet-encrypted token storage in v1 (stubbed TODO, §19).
