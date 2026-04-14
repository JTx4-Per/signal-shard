"""Fixtures for Graph tests.

Uses httpx.MockTransport so no real network calls occur. `respx` is available
for tests that prefer its routing DSL.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any
from unittest.mock import AsyncMock

import httpx
import pytest

class FakeAuth:
    """Minimal stand-in for GraphAuth used in GraphClient tests."""

    def __init__(self, tokens: list[str] | None = None) -> None:
        self._tokens = tokens or ["access-token-1"]
        self._idx = 0
        self.refresh_if_needed = AsyncMock(side_effect=self._on_refresh)
        self.get_token_calls = 0

    async def _on_refresh(self) -> None:
        if self._idx < len(self._tokens) - 1:
            self._idx += 1

    async def get_access_token(self) -> str:
        self.get_token_calls += 1
        return self._tokens[self._idx]


@pytest.fixture
def fake_auth() -> FakeAuth:
    return FakeAuth(tokens=["tok-a", "tok-b"])


Handler = Callable[[httpx.Request], httpx.Response]


@pytest.fixture
def make_client() -> Callable[..., Any]:
    """Factory that builds a GraphClient bound to an httpx.MockTransport handler."""
    from email_intel.graph.client import GraphClient

    def _factory(handler: Handler, auth: Any) -> GraphClient:
        transport = httpx.MockTransport(handler)
        return GraphClient(
            auth=auth,
            base_url="https://graph.microsoft.com/v1.0",
            transport=transport,
        )

    return _factory


@pytest.fixture(autouse=True)
def _patch_sleep(monkeypatch: pytest.MonkeyPatch) -> None:
    """Make backoff sleeps instant so retry tests don't wait."""
    import email_intel.graph.client as client_mod

    async def _fast_sleep(_d: float) -> None:
        return None

    monkeypatch.setattr(client_mod.asyncio, "sleep", _fast_sleep)
