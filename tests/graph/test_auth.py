"""Tests for GraphAuth device flow + token cache roundtrip."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from email_intel.graph.auth import GraphAuth, GraphAuthError


@pytest.fixture
def token_path(tmp_path: Path) -> Path:
    return tmp_path / "cache.bin"


def _mk_msal_app_mock(device_flow: dict, token_result: dict | None) -> MagicMock:
    app = MagicMock()
    app.initiate_device_flow.return_value = device_flow
    app.acquire_token_by_device_flow.return_value = token_result
    app.get_accounts.return_value = [{"home_account_id": "a1", "username": "me"}]
    app.acquire_token_silent.return_value = token_result
    return app


async def test_device_flow_smoke(token_path: Path):
    flow = {"user_code": "ABCD", "message": "go here", "device_code": "dc"}
    token = {"access_token": "tok-1", "refresh_token": "r", "expires_in": 3600}
    mock_app = _mk_msal_app_mock(flow, token)
    with patch(
        "email_intel.graph.auth.msal.PublicClientApplication", return_value=mock_app
    ):
        auth = GraphAuth("cid", "tid", "http://localhost", token_path)
        payload = await auth.start_device_flow()
        assert payload["user_code"] == "ABCD"
        await auth.complete_device_flow(payload)
        got = await auth.get_access_token()
        assert got == "tok-1"


async def test_device_flow_failure_raises(token_path: Path):
    mock_app = _mk_msal_app_mock({"error": "nope"}, None)
    with patch(
        "email_intel.graph.auth.msal.PublicClientApplication", return_value=mock_app
    ):
        auth = GraphAuth("cid", "tid", "http://localhost", token_path)
        with pytest.raises(GraphAuthError):
            await auth.start_device_flow()


async def test_token_cache_roundtrip(token_path: Path):
    """Cache is serialized on completion and re-loadable by a fresh instance."""
    flow = {"user_code": "Z", "device_code": "dc"}
    token = {"access_token": "tok-A", "expires_in": 3600}

    # First instance: capture the cache mutation by letting MSAL write real state.
    mock_app_1 = _mk_msal_app_mock(flow, token)

    def _simulate_write(_flow):
        # Mutate the bound cache so has_state_changed becomes true.
        auth._cache.deserialize('{"AccessToken": {"k": {"secret": "tok-A"}}}')
        auth._cache.has_state_changed = True
        return token

    with patch(
        "email_intel.graph.auth.msal.PublicClientApplication", return_value=mock_app_1
    ):
        auth = GraphAuth("cid", "tid", "http://localhost", token_path)
        mock_app_1.acquire_token_by_device_flow.side_effect = _simulate_write
        await auth.complete_device_flow(flow)

    assert token_path.exists()
    raw = token_path.read_bytes()
    assert b"tok-A" in raw  # pass-through "encryption" stub

    # Second instance should load the same cache on construction.
    mock_app_2 = _mk_msal_app_mock(flow, token)
    with patch(
        "email_intel.graph.auth.msal.PublicClientApplication", return_value=mock_app_2
    ):
        auth2 = GraphAuth("cid", "tid", "http://localhost", token_path)
        # The cache was populated from disk; serialize should include our marker.
        assert "tok-A" in auth2._cache.serialize()


async def test_get_access_token_without_account_raises(token_path: Path):
    app = MagicMock()
    app.get_accounts.return_value = []
    app.acquire_token_silent.return_value = None
    with patch("email_intel.graph.auth.msal.PublicClientApplication", return_value=app):
        auth = GraphAuth("cid", "tid", "http://localhost", token_path)
        with pytest.raises(GraphAuthError):
            await auth.get_access_token()
