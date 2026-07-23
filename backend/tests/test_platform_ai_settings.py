"""Platform AI settings endpoint — system-admin gate + persistence roundtrip."""
from __future__ import annotations

import os
from pathlib import Path

import pytest

TEST_DB = Path(__file__).parent / "_test_platform_ai_settings.db"
os.environ["INSPRO_DATABASE_URL"] = f"sqlite:///{TEST_DB}"

from fastapi.testclient import TestClient  # noqa: E402

from app.core.auth import (  # noqa: E402
    DEMO_BROKER_FIRM_ID,
    DEMO_CLIENT_ID,
    CurrentUser,
    get_current_user,
)
from app.db.base import Base  # noqa: E402
from app.db.session import engine  # noqa: E402
from app.main import app  # noqa: E402
from scripts.seed_demo import seed  # noqa: E402


def _system_admin() -> CurrentUser:
    return CurrentUser(
        user_id="55555555-5555-5555-5555-555555555555",
        broker_firm_id=DEMO_BROKER_FIRM_ID,
        client_id=DEMO_CLIENT_ID,
        role="system_admin",
    )


def _broker_admin() -> CurrentUser:
    return CurrentUser(
        user_id="66666666-6666-6666-6666-666666666666",
        broker_firm_id=DEMO_BROKER_FIRM_ID,
        client_id=DEMO_CLIENT_ID,
        role="broker_admin",
    )


@pytest.fixture(scope="module", autouse=True)
def _setup_db():
    if TEST_DB.exists():
        TEST_DB.unlink()
    Base.metadata.create_all(bind=engine)
    seed()
    yield
    app.dependency_overrides.clear()
    engine.dispose()
    if TEST_DB.exists():
        TEST_DB.unlink()


def test_get_and_put_roundtrip_as_system_admin() -> None:
    app.dependency_overrides[get_current_user] = _system_admin
    client = TestClient(app)

    # Defaults (no row, no env) → all disabled (0).
    r = client.get("/api/v1/platform-ai-settings")
    assert r.status_code == 200
    assert r.json() == {
        "platform_monthly_token_cap": 0,
        "default_monthly_token_budget": 0,
        "max_concurrent_calls": 0,
    }

    body = {
        "platform_monthly_token_cap": 5_000_000,
        "default_monthly_token_budget": 250_000,
        "max_concurrent_calls": 15,
    }
    r = client.put("/api/v1/platform-ai-settings", json=body)
    assert r.status_code == 200
    assert r.json() == body

    # Persisted — a fresh GET reflects it.
    assert client.get("/api/v1/platform-ai-settings").json() == body


def test_broker_admin_forbidden() -> None:
    app.dependency_overrides[get_current_user] = _broker_admin
    client = TestClient(app)
    assert client.get("/api/v1/platform-ai-settings").status_code == 403
    assert (
        client.put(
            "/api/v1/platform-ai-settings",
            json={
                "platform_monthly_token_cap": 1,
                "default_monthly_token_budget": 1,
                "max_concurrent_calls": 1,
            },
        ).status_code
        == 403
    )


def test_put_rejects_negative_values() -> None:
    app.dependency_overrides[get_current_user] = _system_admin
    client = TestClient(app)
    r = client.put(
        "/api/v1/platform-ai-settings",
        json={
            "platform_monthly_token_cap": -1,
            "default_monthly_token_budget": 0,
            "max_concurrent_calls": 0,
        },
    )
    assert r.status_code == 422
