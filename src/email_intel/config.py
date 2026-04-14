"""Application configuration. See project-plan.md §7, §15."""

from __future__ import annotations

from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Central config loaded from environment / .env file."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=True,
    )

    DATABASE_URL: str = "sqlite+aiosqlite:///./email_intel.db"

    # Auth is pluggable (see graph/auth.py). Some tenants block self-service app
    # registration; `device` mode uses a well-known public client_id and does
    # not require MS_GRAPH_CLIENT_ID/TENANT_ID. `msal_app` mode is reserved for
    # the future case where a dedicated app registration is granted. `static`
    # takes a pre-acquired bearer token (bootstrap / testing).
    AUTH_MODE: Literal["device", "msal_app", "static"] = "device"
    MS_GRAPH_CLIENT_ID: str | None = None
    MS_GRAPH_TENANT_ID: str | None = None
    MS_GRAPH_REDIRECT_URI: str | None = None
    MS_GRAPH_STATIC_TOKEN: str | None = None
    MS_GRAPH_STATIC_TOKEN_PATH: str | None = None
    MS_GRAPH_TOKEN_STORE_PATH: str = ".email_intel/tokens.json"

    # project-plan §12.4 — rules-only v1
    LLM_PROVIDER: str = "none"

    SOFT_COMPLETE_WINDOW_DAYS: int = 7
    DONE_CATEGORY_POLICY: Literal["clear", "fyi", "preserve"] = "clear"

    LOG_LEVEL: str = "INFO"

    REDUCER_VERSION: str = "v1.0.0"
    CLASSIFIER_RULE_VERSION: str = "v1.0.0"


def get_settings() -> Settings:
    """Return a fresh Settings instance (env-driven)."""
    return Settings()
