"""Tests for the pluggable AuthProvider factory and implementations."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from email_intel.graph.auth import (
    DEFAULT_AUTHORITY_TENANT,
    DEFAULT_PUBLIC_CLIENT_ID,
    AuthProvider,
    GraphAuthError,
    MsalPublicAuthProvider,
    StaticTokenAuthProvider,
    build_auth_provider,
)


def _settings(**overrides: object) -> SimpleNamespace:
    base = {
        "AUTH_MODE": "device",
        "MS_GRAPH_CLIENT_ID": None,
        "MS_GRAPH_TENANT_ID": None,
        "MS_GRAPH_REDIRECT_URI": None,
        "MS_GRAPH_STATIC_TOKEN": None,
        "MS_GRAPH_STATIC_TOKEN_PATH": None,
        "MS_GRAPH_TOKEN_STORE_PATH": ".email_intel/tokens.json",
    }
    base.update(overrides)
    return SimpleNamespace(**base)


def test_device_mode_uses_defaults_without_client_id(tmp_path: Path) -> None:
    provider = build_auth_provider(_settings(), token_store_path=tmp_path / "t.json")
    assert isinstance(provider, MsalPublicAuthProvider)
    assert provider.client_id == DEFAULT_PUBLIC_CLIENT_ID
    assert provider.tenant_id == DEFAULT_AUTHORITY_TENANT


def test_device_mode_honors_user_supplied_ids(tmp_path: Path) -> None:
    provider = build_auth_provider(
        _settings(MS_GRAPH_CLIENT_ID="custom-id", MS_GRAPH_TENANT_ID="org.onmicrosoft.com"),
        token_store_path=tmp_path / "t.json",
    )
    assert isinstance(provider, MsalPublicAuthProvider)
    assert provider.client_id == "custom-id"
    assert provider.tenant_id == "org.onmicrosoft.com"


def test_msal_app_mode_requires_both_ids(tmp_path: Path) -> None:
    with pytest.raises(GraphAuthError, match="MS_GRAPH_CLIENT_ID"):
        build_auth_provider(
            _settings(AUTH_MODE="msal_app"), token_store_path=tmp_path / "t.json"
        )
    with pytest.raises(GraphAuthError):
        build_auth_provider(
            _settings(AUTH_MODE="msal_app", MS_GRAPH_CLIENT_ID="cid"),
            token_store_path=tmp_path / "t.json",
        )


def test_msal_app_mode_builds_when_ids_present(tmp_path: Path) -> None:
    provider = build_auth_provider(
        _settings(
            AUTH_MODE="msal_app",
            MS_GRAPH_CLIENT_ID="cid",
            MS_GRAPH_TENANT_ID="contoso.onmicrosoft.com",
        ),
        token_store_path=tmp_path / "t.json",
    )
    assert isinstance(provider, MsalPublicAuthProvider)
    assert provider.client_id == "cid"
    assert provider.tenant_id == "contoso.onmicrosoft.com"


def test_static_mode_with_inline_token() -> None:
    provider = build_auth_provider(
        _settings(AUTH_MODE="static", MS_GRAPH_STATIC_TOKEN="eyJ.fake.token")
    )
    assert isinstance(provider, StaticTokenAuthProvider)


def test_static_mode_requires_token_or_path() -> None:
    with pytest.raises(GraphAuthError, match="MS_GRAPH_STATIC_TOKEN"):
        build_auth_provider(_settings(AUTH_MODE="static"))


def test_unknown_mode_raises() -> None:
    with pytest.raises(GraphAuthError, match="unknown AUTH_MODE"):
        build_auth_provider(_settings(AUTH_MODE="oidc-pkce"))


@pytest.mark.asyncio
async def test_static_provider_returns_inline_token() -> None:
    provider = StaticTokenAuthProvider(token="abc.def.ghi")
    assert await provider.get_access_token() == "abc.def.ghi"
    await provider.refresh_if_needed()  # must not raise


@pytest.mark.asyncio
async def test_static_provider_reads_token_file(tmp_path: Path) -> None:
    p = tmp_path / "tok.txt"
    p.write_text("file.token.value\n")
    provider = StaticTokenAuthProvider(token_path=p)
    assert await provider.get_access_token() == "file.token.value"
    # Update file → refresh reads it back.
    p.write_text("rotated.token\n")
    await provider.refresh_if_needed()
    assert await provider.get_access_token() == "rotated.token"


@pytest.mark.asyncio
async def test_static_provider_empty_token_raises() -> None:
    provider = StaticTokenAuthProvider(token="   ")
    with pytest.raises(GraphAuthError):
        await provider.get_access_token()


def test_static_provider_requires_source() -> None:
    with pytest.raises(GraphAuthError):
        StaticTokenAuthProvider()


def test_protocol_is_satisfied_by_all_providers(tmp_path: Path) -> None:
    """AuthProvider is runtime_checkable; structural conformance verification."""
    msal_provider = build_auth_provider(_settings(), token_store_path=tmp_path / "t.json")
    static_provider = StaticTokenAuthProvider(token="x")
    assert isinstance(msal_provider, AuthProvider)
    assert isinstance(static_provider, AuthProvider)
