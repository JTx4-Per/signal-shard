"""Pluggable auth for the Graph client.

Environment constraint: some tenants block creating custom app registrations.
Auth is therefore an **infrastructure concern** behind the `AuthProvider`
protocol; the Graph client never depends on any specific MSAL wiring.

Modes (selected via `Settings.AUTH_MODE`):

- ``device``   — MSAL public-client device-code flow using a well-known public
                 client ID (Azure CLI by default). No app-registration required.
                 Safest default for personal/solo use where the user cannot
                 create their own registration.
- ``msal_app`` — MSAL public-client device-code flow using a user-supplied
                 ``MS_GRAPH_CLIENT_ID`` + ``MS_GRAPH_TENANT_ID``. For the
                 future case where a dedicated app registration is granted.
                 **Never uses a client secret.**
- ``static``   — A pre-acquired bearer token (e.g. pasted from Graph Explorer).
                 Useful for bootstrap testing; no refresh support.

Reducer, classifier, ingestion, and writeback are unchanged — only the access
layer is adapted here.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

import msal  # type: ignore[import-untyped]

# Scopes intentionally exclude Mail.Send for safety (see plan §19).
DEFAULT_SCOPES: list[str] = [
    "Mail.ReadWrite",
    "MailboxSettings.Read",
    "Tasks.ReadWrite",
]

# Well-known Microsoft public client IDs that work in tenants which do not
# permit self-service app registration. Users whose tenants block this one
# must obtain a tenant-approved client_id and switch to `msal_app` mode.
DEFAULT_PUBLIC_CLIENT_ID: str = "04b07795-8ddb-461a-bbee-02f9e1bf7b46"  # Azure CLI
# "organizations" restricts to work/school accounts; safer than "common".
DEFAULT_AUTHORITY_TENANT: str = "organizations"


class GraphAuthError(RuntimeError):
    """Raised when token acquisition fails."""


# ----------------------------------------------------------------------------
# Protocol — all Graph client consumers depend only on this interface.
# ----------------------------------------------------------------------------


@runtime_checkable
class AuthProvider(Protocol):
    """Minimal bearer-token provider contract.

    Implementations must be safe to call from async code and must not raise
    during import. `get_access_token` returns a valid token or raises
    ``GraphAuthError``; `refresh_if_needed` is idempotent and may be a no-op
    for providers that cannot refresh (e.g. static tokens).
    """

    async def get_access_token(self) -> str: ...

    async def refresh_if_needed(self) -> None: ...


# ----------------------------------------------------------------------------
# MSAL public-client provider (covers both `device` and `msal_app` modes).
# ----------------------------------------------------------------------------


def _encrypt(data: bytes) -> bytes:
    # TODO: integrate cryptography.Fernet for encryption-at-rest (§19).
    return data


def _decrypt(data: bytes) -> bytes:
    # TODO: integrate cryptography.Fernet for encryption-at-rest (§19).
    return data


class MsalPublicAuthProvider:
    """Delegated device-code flow backed by ``msal.PublicClientApplication``.

    This class backs both `AUTH_MODE=device` (default client_id + authority) and
    `AUTH_MODE=msal_app` (user-supplied client_id + tenant_id). No client-secret
    flow is supported or exposed.
    """

    def __init__(
        self,
        client_id: str,
        tenant_id: str,
        redirect_uri: str | None = None,
        token_store_path: Path | str | None = None,
        scopes: list[str] | None = None,
    ) -> None:
        if token_store_path is None:
            raise GraphAuthError("token_store_path is required")
        self.client_id = client_id
        self.tenant_id = tenant_id
        # redirect_uri is only relevant for non-device flows; kept for compat.
        self.redirect_uri = redirect_uri or "http://localhost"
        self.token_store_path = Path(token_store_path)
        self.scopes = list(scopes) if scopes is not None else list(DEFAULT_SCOPES)
        self._lock = asyncio.Lock()
        self._cache = msal.SerializableTokenCache()
        self._load_cache()
        self._app = msal.PublicClientApplication(
            client_id=self.client_id,
            authority=f"https://login.microsoftonline.com/{self.tenant_id}",
            token_cache=self._cache,
        )

    # ---- cache persistence ----
    def _load_cache(self) -> None:
        if self.token_store_path.exists():
            raw = self.token_store_path.read_bytes()
            if raw:
                try:
                    self._cache.deserialize(_decrypt(raw).decode("utf-8"))
                except Exception:  # pragma: no cover - corrupt cache
                    pass

    def _save_cache(self) -> None:
        if not self._cache.has_state_changed:
            return
        payload = self._cache.serialize().encode("utf-8")
        self.token_store_path.parent.mkdir(parents=True, exist_ok=True)
        self.token_store_path.write_bytes(_encrypt(payload))
        self._cache.has_state_changed = False

    # ---- device-code flow ----
    async def start_device_flow(self) -> dict[str, Any]:
        loop = asyncio.get_running_loop()
        flow: dict[str, Any] = await loop.run_in_executor(
            None, lambda: self._app.initiate_device_flow(scopes=self.scopes)
        )
        if "user_code" not in flow:
            raise GraphAuthError(f"device flow init failed: {flow!r}")
        return flow

    async def complete_device_flow(self, flow: dict[str, Any]) -> None:
        loop = asyncio.get_running_loop()
        result: dict[str, Any] = await loop.run_in_executor(
            None, lambda: self._app.acquire_token_by_device_flow(flow)
        )
        if "access_token" not in result:
            raise GraphAuthError(f"device flow did not yield token: {result!r}")
        async with self._lock:
            self._save_cache()

    # ---- silent acquisition / refresh ----
    async def _acquire_silent(self) -> str | None:
        loop = asyncio.get_running_loop()

        def _work() -> dict[str, Any] | None:
            accounts = self._app.get_accounts()
            if not accounts:
                return None
            out = self._app.acquire_token_silent(self.scopes, account=accounts[0])
            return out if isinstance(out, dict) else None

        res = await loop.run_in_executor(None, _work)
        if not res:
            return None
        token = res.get("access_token")
        if not token:
            return None
        return str(token)

    async def get_access_token(self) -> str:
        async with self._lock:
            token = await self._acquire_silent()
            if token is None:
                raise GraphAuthError(
                    "no cached account; run start_device_flow/complete_device_flow first"
                )
            self._save_cache()
            return token

    async def refresh_if_needed(self) -> None:
        """Force a silent re-acquisition (MSAL uses refresh_token when needed)."""
        async with self._lock:
            token = await self._acquire_silent()
            if token is None:
                raise GraphAuthError("refresh failed: no account in cache")
            self._save_cache()


# Back-compat alias. Existing imports of ``GraphAuth`` keep working.
GraphAuth = MsalPublicAuthProvider


# ----------------------------------------------------------------------------
# Static token provider — manual bootstrap escape hatch.
# ----------------------------------------------------------------------------


class StaticTokenAuthProvider:
    """Returns a fixed bearer token. No refresh.

    Use when the user has a token from Graph Explorer or another CLI tool and
    just wants to run the pipeline once. ``refresh_if_needed`` re-reads the
    token source (env or file) so a restart-less refresh is possible by
    updating the source.
    """

    def __init__(self, token: str | None = None, *, token_path: Path | None = None) -> None:
        if not token and not token_path:
            raise GraphAuthError("StaticTokenAuthProvider requires token or token_path")
        self._token = token
        self._token_path = Path(token_path) if token_path else None
        self._lock = asyncio.Lock()

    def _read(self) -> str:
        if self._token_path and self._token_path.exists():
            return self._token_path.read_text().strip()
        if self._token is not None:
            return self._token.strip()
        raise GraphAuthError("static token source unavailable")

    async def get_access_token(self) -> str:
        async with self._lock:
            tok = self._read()
            if not tok:
                raise GraphAuthError("static token is empty")
            return tok

    async def refresh_if_needed(self) -> None:
        # Re-reading the file is as much "refresh" as we can do here.
        async with self._lock:
            self._read()


# ----------------------------------------------------------------------------
# Factory — the only function app.py / callers should need.
# ----------------------------------------------------------------------------


def build_auth_provider(
    settings: Any,
    *,
    token_store_path: Path | None = None,
) -> AuthProvider:
    """Construct an ``AuthProvider`` from :class:`Settings`.

    Mode selection:
      - ``device`` (default): ``MS_GRAPH_CLIENT_ID`` / ``MS_GRAPH_TENANT_ID`` are
        optional; falls back to ``DEFAULT_PUBLIC_CLIENT_ID`` and
        ``DEFAULT_AUTHORITY_TENANT``.
      - ``msal_app``: both ``MS_GRAPH_CLIENT_ID`` and ``MS_GRAPH_TENANT_ID``
        are required; raises ``GraphAuthError`` otherwise.
      - ``static``: requires ``MS_GRAPH_STATIC_TOKEN`` env value or a file at
        ``MS_GRAPH_STATIC_TOKEN_PATH``.
    """
    mode = getattr(settings, "AUTH_MODE", "device")
    store = token_store_path or Path(
        getattr(settings, "MS_GRAPH_TOKEN_STORE_PATH", ".email_intel/tokens.json")
    )

    if mode == "static":
        token = getattr(settings, "MS_GRAPH_STATIC_TOKEN", None)
        token_path_s = getattr(settings, "MS_GRAPH_STATIC_TOKEN_PATH", None)
        token_path = Path(token_path_s) if token_path_s else None
        if not token and not token_path:
            raise GraphAuthError(
                "AUTH_MODE=static requires MS_GRAPH_STATIC_TOKEN or MS_GRAPH_STATIC_TOKEN_PATH"
            )
        return StaticTokenAuthProvider(token=token, token_path=token_path)

    if mode == "msal_app":
        client_id = getattr(settings, "MS_GRAPH_CLIENT_ID", None)
        tenant_id = getattr(settings, "MS_GRAPH_TENANT_ID", None)
        if not client_id or not tenant_id:
            raise GraphAuthError(
                "AUTH_MODE=msal_app requires MS_GRAPH_CLIENT_ID and MS_GRAPH_TENANT_ID"
            )
        return MsalPublicAuthProvider(
            client_id=client_id,
            tenant_id=tenant_id,
            redirect_uri=getattr(settings, "MS_GRAPH_REDIRECT_URI", None),
            token_store_path=store,
        )

    if mode == "device":
        client_id = (
            getattr(settings, "MS_GRAPH_CLIENT_ID", None) or DEFAULT_PUBLIC_CLIENT_ID
        )
        tenant_id = (
            getattr(settings, "MS_GRAPH_TENANT_ID", None) or DEFAULT_AUTHORITY_TENANT
        )
        return MsalPublicAuthProvider(
            client_id=client_id,
            tenant_id=tenant_id,
            redirect_uri=getattr(settings, "MS_GRAPH_REDIRECT_URI", None),
            token_store_path=store,
        )

    raise GraphAuthError(f"unknown AUTH_MODE: {mode!r}")


__all__ = [
    "AuthProvider",
    "GraphAuth",
    "GraphAuthError",
    "MsalPublicAuthProvider",
    "StaticTokenAuthProvider",
    "build_auth_provider",
    "DEFAULT_SCOPES",
    "DEFAULT_PUBLIC_CLIENT_ID",
    "DEFAULT_AUTHORITY_TENANT",
]


# ---- internal helpers for tests ----
def _dump_cache_for_test(cache: msal.SerializableTokenCache) -> str:
    return json.dumps(json.loads(cache.serialize()))
