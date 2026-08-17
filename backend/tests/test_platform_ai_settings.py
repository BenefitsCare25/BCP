"""Platform AI settings — system-admin gate, limits roundtrip, platform key.

The platform key is the DEFAULT every company runs on, so the resolution-order
test here is the load-bearing one: BYOK overrides it per company, everyone else
falls through to it.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from unittest.mock import patch

import pytest

TEST_DB = Path(__file__).parent / "_test_platform_ai_settings.db"
os.environ["INSPRO_DATABASE_URL"] = f"sqlite:///{TEST_DB}"

from fastapi.testclient import TestClient  # noqa: E402

from app.core.ai_config import load_ai_config, pack_vertex_secret  # noqa: E402
from app.core.auth import (  # noqa: E402
    DEMO_BROKER_FIRM_ID,
    DEMO_CLIENT_ID,
    CurrentUser,
    get_current_user,
)
from app.core.crypto import encrypt_secret, fingerprint  # noqa: E402
from app.db.base import Base  # noqa: E402
from app.db.session import SessionLocal, engine  # noqa: E402
from app.main import app  # noqa: E402
from app.models import ClientAIConfig, PlatformAISetting  # noqa: E402
from app.models.platform_ai_settings import SINGLETON_ID  # noqa: E402
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


_LIMIT_KEYS = (
    "platform_monthly_token_cap",
    "default_monthly_token_budget",
    "max_concurrent_calls",
)


def _limits(payload: dict) -> dict:
    """Just the limit fields — the response also carries key status."""
    return {k: payload[k] for k in _LIMIT_KEYS}


def test_get_and_put_roundtrip_as_system_admin() -> None:
    app.dependency_overrides[get_current_user] = _system_admin
    client = TestClient(app)

    # Defaults (no row, no env) → all disabled (0), and no platform key stored.
    r = client.get("/api/v1/platform-ai-settings")
    assert r.status_code == 200
    assert _limits(r.json()) == {
        "platform_monthly_token_cap": 0,
        "default_monthly_token_budget": 0,
        "max_concurrent_calls": 0,
    }
    assert r.json()["credentials"]["configured"] is False

    body = {
        "platform_monthly_token_cap": 5_000_000,
        "default_monthly_token_budget": 250_000,
        "max_concurrent_calls": 15,
    }
    r = client.put("/api/v1/platform-ai-settings", json=body)
    assert r.status_code == 200
    assert _limits(r.json()) == body

    # Persisted — a fresh GET reflects it.
    assert _limits(client.get("/api/v1/platform-ai-settings").json()) == body


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


# ── Platform credentials (the default key every company runs on) ──────────────

PLATFORM_KEY = json.dumps(
    {
        "type": "service_account",
        "project_id": "inspro-platform",
        "private_key": "-----BEGIN PRIVATE KEY-----\nPPP\n-----END PRIVATE KEY-----\n",
        "client_email": "svc@inspro-platform.iam.gserviceaccount.com",
    }
)
BYOK_KEY = json.dumps(
    {
        "type": "service_account",
        "project_id": "inspro-tenant",
        "private_key": "-----BEGIN PRIVATE KEY-----\nTTT\n-----END PRIVATE KEY-----\n",
        "client_email": "svc@inspro-tenant.iam.gserviceaccount.com",
    }
)


def _put_platform_key(client: TestClient, **overrides) -> dict:
    body = {"service_account_json": PLATFORM_KEY, **overrides}
    r = client.put("/api/v1/platform-ai-settings/credentials", json=body)
    assert r.status_code == 200, r.text
    return r.json()


def test_only_stored_platform_probe_activates_saved_configuration() -> None:
    app.dependency_overrides[get_current_user] = _system_admin
    client = TestClient(app)
    _put_platform_key(client, model="gemini-2.5-flash")

    with patch(
        "app.api.v1.platform_ai_settings.probe_vertex",
        return_value=(None, 5, "gemini-draft"),
    ):
        draft = client.post(
            "/api/v1/platform-ai-settings/credentials/test",
            json={"model": "gemini-draft"},
        )
    assert draft.status_code == 200
    with SessionLocal() as db:
        row = db.get(PlatformAISetting, SINGLETON_ID)
        assert row.validation_status == "unvalidated"
        assert row.validated_model is None

    with patch(
        "app.api.v1.platform_ai_settings.probe_vertex",
        return_value=(None, 5, "gemini-2.5-flash"),
    ):
        stored = client.post("/api/v1/platform-ai-settings/credentials/test")
    assert stored.status_code == 200
    with SessionLocal() as db:
        row = db.get(PlatformAISetting, SINGLETON_ID)
        assert row.validation_status == "active"
        assert row.validated_model == "gemini-2.5-flash"
        assert row.validated_fingerprint == row.key_fingerprint
    client.delete("/api/v1/platform-ai-settings/credentials")


def test_credentials_roundtrip_and_never_leak_cleartext() -> None:
    app.dependency_overrides[get_current_user] = _system_admin
    client = TestClient(app)

    out = _put_platform_key(client, model="gemini-2.5-pro")["credentials"]
    assert out["configured"] is True
    assert out["provider"] == "vertex"
    assert out["location"] == "asia-southeast1"  # defaulted
    assert out["model"] == "gemini-2.5-pro"
    assert out["key_masked"].startswith("••••")
    # The cleartext key is never echoed back on any field.
    assert PLATFORM_KEY not in json.dumps(out)

    # Persisted across requests.
    assert client.get("/api/v1/platform-ai-settings").json()["credentials"]["configured"]


def test_saving_limits_does_not_clear_the_key() -> None:
    """Limits and credentials are separate endpoints precisely so this holds."""
    app.dependency_overrides[get_current_user] = _system_admin
    client = TestClient(app)
    _put_platform_key(client)

    r = client.put(
        "/api/v1/platform-ai-settings",
        json={
            "platform_monthly_token_cap": 9_000,
            "default_monthly_token_budget": 100,
            "max_concurrent_calls": 4,
        },
    )
    assert r.status_code == 200
    assert r.json()["credentials"]["configured"] is True

    # ...and clearing the key leaves the limits standing.
    r = client.delete("/api/v1/platform-ai-settings/credentials")
    assert r.status_code == 200
    assert r.json()["credentials"]["configured"] is False
    assert r.json()["platform_monthly_token_cap"] == 9_000


def test_credentials_reject_malformed_key() -> None:
    app.dependency_overrides[get_current_user] = _system_admin
    client = TestClient(app)

    # Not a service-account JSON.
    r = client.put(
        "/api/v1/platform-ai-settings/credentials",
        json={"service_account_json": "not-a-key-at-all"},
    )
    assert r.status_code == 422

    # Valid shape, but no project_id → 400 (schema can't require a value).
    keyless = json.dumps(
        {
            "type": "service_account",
            "project_id": "",
            "private_key": "-----BEGIN PRIVATE KEY-----\nX\n-----END PRIVATE KEY-----\n",
            "client_email": "svc@x.iam.gserviceaccount.com",
        }
    )
    r = client.put(
        "/api/v1/platform-ai-settings/credentials",
        json={"service_account_json": keyless},
    )
    assert r.status_code == 400


def test_credentials_reject_non_sg_region_in_prod(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Residency guard: claim PII must stay in Singapore. Dev only warns."""
    monkeypatch.setenv("INSPRO_ENV", "prod")
    app.dependency_overrides[get_current_user] = _system_admin
    client = TestClient(app)
    r = client.put(
        "/api/v1/platform-ai-settings/credentials",
        json={"service_account_json": PLATFORM_KEY, "location": "us-central1"},
    )
    assert r.status_code == 400
    assert "asia-southeast1" in r.json()["detail"]


def test_credentials_forbidden_for_broker_admin() -> None:
    app.dependency_overrides[get_current_user] = _broker_admin
    client = TestClient(app)
    assert (
        client.put(
            "/api/v1/platform-ai-settings/credentials",
            json={"service_account_json": PLATFORM_KEY},
        ).status_code
        == 403
    )
    assert client.delete("/api/v1/platform-ai-settings/credentials").status_code == 403
    assert (
        client.post("/api/v1/platform-ai-settings/credentials/test").status_code == 403
    )


@pytest.fixture
def _no_env_provider(monkeypatch: pytest.MonkeyPatch):
    """Isolate from env-configured Vertex.

    Other suites set `VERTEX_PROJECT`, and env is the LAST resort in the
    resolution chain — leaving it set would mask "nothing is configured".
    """
    monkeypatch.delenv("VERTEX_PROJECT", raising=False)
    monkeypatch.delenv("INSPRO_AI_PROVIDER", raising=False)


def test_resolution_order_byok_over_platform_over_none(_no_env_provider) -> None:
    """The whole point: a company with no key still gets AI from the platform."""
    app.dependency_overrides[get_current_user] = _system_admin
    client = TestClient(app)
    client.delete("/api/v1/platform-ai-settings/credentials")

    db = SessionLocal()
    try:
        # No platform key, no BYOK, no env → nothing resolves.
        assert load_ai_config(db, DEMO_CLIENT_ID) is None
    finally:
        db.close()

    _put_platform_key(client)

    db = SessionLocal()
    try:
        cfg = load_ai_config(db, DEMO_CLIENT_ID)
        assert cfg is not None
        assert cfg.source == "platform"
        assert cfg.gcp_project == "inspro-platform"
        assert cfg.api_key == PLATFORM_KEY  # decrypted SA JSON reaches the adapter

        # A background job with no tenant still resolves the platform key.
        assert load_ai_config(db).source == "platform"

        # A per-company BYOK row overrides it for that company only.
        db.add(
            ClientAIConfig(
                client_id=DEMO_CLIENT_ID,
                provider="vertex",
                endpoint="asia-southeast1",
                model="gemini-2.5-flash",
                encrypted_api_key=encrypt_secret(
                    pack_vertex_secret("inspro-tenant", BYOK_KEY)
                ),
                key_fingerprint=fingerprint(BYOK_KEY),
            )
        )
        db.commit()
        cfg = load_ai_config(db, DEMO_CLIENT_ID)
        assert cfg is not None and cfg.source == "byok"
        assert cfg.gcp_project == "inspro-tenant"
        # ...while every other company keeps falling through to the platform.
        assert load_ai_config(db, "some-other-client").source == "platform"
    finally:
        db.query(ClientAIConfig).delete()
        db.commit()
        db.close()


def test_corrupt_platform_secret_falls_through_instead_of_raising(
    _no_env_provider,
) -> None:
    app.dependency_overrides[get_current_user] = _system_admin
    client = TestClient(app)
    _put_platform_key(client)

    db = SessionLocal()
    try:
        row = db.get(PlatformAISetting, SINGLETON_ID)
        row.encrypted_service_account = b"not-a-fernet-token"
        db.commit()
        # Corrupt row must not 500 the AI surface — it resolves to None (no env).
        assert load_ai_config(db, DEMO_CLIENT_ID) is None
    finally:
        db.close()
    client.delete("/api/v1/platform-ai-settings/credentials")
