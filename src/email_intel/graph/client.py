"""Graph HTTP client with retries, paging, rate-limit handling. §16.1."""

from __future__ import annotations

import asyncio
import random
import time
from collections.abc import AsyncIterator
from typing import Any
from uuid import uuid4

import httpx
import structlog

from .auth import AuthProvider

log = structlog.get_logger(__name__)

_MAX_ATTEMPTS = 5
_BASE_BACKOFF = 1.0
_MAX_BACKOFF = 60.0
_RETRY_STATUSES = {429, 500, 502, 503, 504}


class GraphHTTPError(RuntimeError):
    """Non-retryable Graph API error."""

    def __init__(self, status: int, message: str, body: Any = None) -> None:
        super().__init__(f"{status}: {message}")
        self.status = status
        self.body = body


class EtagMismatch(GraphHTTPError):
    """Raised on 412 Precondition Failed for an If-Match request."""


def _compute_backoff(attempt: int, retry_after: str | None) -> float:
    if retry_after:
        try:
            parsed = float(retry_after)
        except ValueError:
            parsed = None
        if parsed is not None:
            return parsed if parsed < _MAX_BACKOFF else _MAX_BACKOFF
    exp = _BASE_BACKOFF * float(2 ** (attempt - 1))
    delay: float = exp if exp < _MAX_BACKOFF else _MAX_BACKOFF
    jitter = random.uniform(0.0, delay * 0.25)
    total = delay + jitter
    return total if total < _MAX_BACKOFF else _MAX_BACKOFF


class GraphClient:
    """Thin async wrapper over Graph with token injection, backoff, paging."""

    def __init__(
        self,
        auth: AuthProvider,
        base_url: str = "https://graph.microsoft.com/v1.0",
        *,
        transport: httpx.AsyncBaseTransport | None = None,
        timeout: float = 30.0,
    ) -> None:
        self.auth = auth
        self.base_url = base_url.rstrip("/")
        client_kwargs: dict[str, Any] = {
            "timeout": timeout,
            "http2": True,
            "base_url": self.base_url,
        }
        if transport is not None:
            # HTTP/2 over MockTransport isn't supported; callers pass a transport in tests.
            client_kwargs["transport"] = transport
            client_kwargs["http2"] = False
        self._http = httpx.AsyncClient(**client_kwargs)

    async def aclose(self) -> None:
        await self._http.aclose()

    async def __aenter__(self) -> GraphClient:
        return self

    async def __aexit__(self, *exc: Any) -> None:
        await self.aclose()

    # --- core ---
    def _url(self, path: str) -> str:
        if path.startswith("http://") or path.startswith("https://"):
            return path
        if not path.startswith("/"):
            path = "/" + path
        return path

    async def request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json: Any = None,
        headers: dict[str, str] | None = None,
    ) -> dict[str, Any] | None:
        url = self._url(path)
        correlation_id = str(uuid4())
        refreshed = False

        for attempt in range(1, _MAX_ATTEMPTS + 1):
            token = await self.auth.get_access_token()
            merged_headers = {
                "Authorization": f"Bearer {token}",
                "Accept": "application/json",
                "client-request-id": correlation_id,
            }
            if headers:
                merged_headers.update(headers)

            started = time.monotonic()
            try:
                resp = await self._http.request(
                    method,
                    url,
                    params=params,
                    json=json,
                    headers=merged_headers,
                )
            except httpx.HTTPError as e:
                log.warning(
                    "graph.request.network_error",
                    correlation_id=correlation_id,
                    method=method,
                    path=path,
                    attempt=attempt,
                    error=str(e),
                )
                if attempt >= _MAX_ATTEMPTS:
                    raise
                await asyncio.sleep(_compute_backoff(attempt, None))
                continue

            elapsed_ms = int((time.monotonic() - started) * 1000)
            log.info(
                "graph.request",
                correlation_id=correlation_id,
                method=method,
                path=path,
                status=resp.status_code,
                elapsed_ms=elapsed_ms,
                attempt=attempt,
            )

            status = resp.status_code

            if status == 401 and not refreshed:
                refreshed = True
                await self.auth.refresh_if_needed()
                continue

            if status == 404 and method.upper() == "GET":
                return None

            if status == 412:
                raise EtagMismatch(status, "precondition failed", _safe_json(resp))

            if status in _RETRY_STATUSES and attempt < _MAX_ATTEMPTS:
                delay = _compute_backoff(attempt, resp.headers.get("Retry-After"))
                await asyncio.sleep(delay)
                continue

            if status >= 400:
                raise GraphHTTPError(status, resp.text, _safe_json(resp))

            if status == 204 or not resp.content:
                return None
            return _safe_json(resp)

        raise GraphHTTPError(599, f"exhausted retries for {method} {path}")

    async def get(self, path: str, **kw: Any) -> dict[str, Any] | None:
        return await self.request("GET", path, **kw)

    async def post(self, path: str, **kw: Any) -> dict[str, Any] | None:
        return await self.request("POST", path, **kw)

    async def patch(self, path: str, **kw: Any) -> dict[str, Any] | None:
        return await self.request("PATCH", path, **kw)

    async def delete(self, path: str, **kw: Any) -> dict[str, Any] | None:
        return await self.request("DELETE", path, **kw)

    # --- paging ---
    async def paged(
        self, path: str, *, params: dict[str, Any] | None = None
    ) -> AsyncIterator[dict[str, Any]]:
        next_url: str | None = path
        first = True
        while next_url:
            if first:
                page = await self.get(next_url, params=params)
                first = False
            else:
                page = await self.get(next_url)
            if not page:
                return
            for item in page.get("value", []) or []:
                yield item
            next_url = page.get("@odata.nextLink")

    async def delta(
        self, path: str, *, delta_link: str | None = None
    ) -> AsyncIterator[tuple[dict[str, Any], str | None]]:
        """Yield (item, updated_delta_link).

        updated_delta_link is None for all items except those in the final page,
        where it equals the new @odata.deltaLink. Caller persists on terminator.
        """
        next_url: str | None = delta_link or path
        while next_url:
            page = await self.get(next_url)
            if not page:
                return
            items = page.get("value", []) or []
            new_delta = page.get("@odata.deltaLink")
            next_page = page.get("@odata.nextLink")
            is_last = next_page is None
            for item in items:
                yield item, (new_delta if is_last else None)
            if is_last:
                return
            next_url = next_page


def _safe_json(resp: httpx.Response) -> dict[str, Any] | None:
    if not resp.content:
        return None
    try:
        data = resp.json()
    except ValueError:
        return None
    return data if isinstance(data, dict) else {"value": data}


__all__ = ["GraphClient", "GraphHTTPError", "EtagMismatch"]
