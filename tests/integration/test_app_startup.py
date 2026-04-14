"""Smoke test: the wired FastAPI app starts and serves /healthz + /review/."""

from __future__ import annotations

import asyncio

import pytest
from fastapi.testclient import TestClient

from email_intel.app import app
from email_intel.db.base import Base


@pytest.fixture
def tmp_env(tmp_path, monkeypatch):
    db = tmp_path / "integration.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite+aiosqlite:///{db}")
    # clear any cached settings from other tests
    yield


@pytest.fixture
def client(tmp_env):
    with TestClient(app) as c:
        # create tables on the per-app engine
        engine = app.state.engine

        async def _create() -> None:
            async with engine.begin() as conn:
                await conn.run_sync(Base.metadata.create_all)

        asyncio.new_event_loop().run_until_complete(_create())
        yield c


def test_healthz_ok(client: TestClient) -> None:
    resp = client.get("/healthz")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert "reducer_version" in body


def test_review_dashboard_renders_with_empty_db(client: TestClient) -> None:
    resp = client.get("/review/")
    assert resp.status_code == 200
    assert "text/html" in resp.headers.get("content-type", "")
