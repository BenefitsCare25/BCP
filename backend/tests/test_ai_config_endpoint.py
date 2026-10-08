"""BYOK /ai-config endpoint — encryption, redaction, tenant isolation, role gate."""
from __future__ import annotations

import json
import logging
import os
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

import pytest

TEST_DB = Path(__file__).parent / "_test_ai_config.db"
os.environ["INSPRO_DATABASE_URL"] = f"sqlite:///{TEST_DB}"

from fastapi.testclient import TestClient  # noqa: E402

from app.core.auth import (  # noqa: E402
    DEMO_BROKER_FIRM_ID,
    DEMO_CLIENT_ID,
    CurrentUser,
    get_current_user,
)
from app.core.crypto import decrypt_secret  # noqa: E402
from app.db.base import Base  # noqa: E402
from app.db.session import SessionLocal, engine  # noqa: E402
from app.main import app  # noqa: E402
from app.models import AuditLog, Client, ClientAIConfig  # noqa: E402
from scripts.seed_demo import seed  # noqa: E402

CLIENT_B_ID = "00000000-0000-0000-0000-0000000000c0"

# Vertex BYOK key = a service-account JSON.
REAL_KEY = json.dumps(
    {
        "type": "service_account",
        "project_id": "inspro-test",
        "private_key": "-----BEGIN PRIVATE KEY-----\nAAA\n-----END PRIVATE KEY-----\n",
        "client_email": "svc@inspro-test.iam.gserviceaccount.com",
        "token_uri": "https://oauth2.googleapis.com/token",
    }
)
OTHER_KEY = json.dumps(
    {
        "type": "service_account",
        "project_id": "inspro-test-b",
        "private_key": "-----BEGIN PRIVATE KEY-----\nBBB\n-----END PRIVATE KEY-----\n",
        "client_email": "svc@inspro-test-b.iam.gserviceaccount.com",
        "token_uri": "https://oauth2.googleapis.com/token",
        "universe_domain": "googleapis.com",
    }
)


def _key_with(**overrides: str) -> str:
    return json.dumps({**json.loads(REAL_KEY), **overrides})


def _admin_a() -> CurrentUser:
    return CurrentUser(
        user_id="11111111-1111-1111-1111-111111111111",
        broker_firm_id=DEMO_BROKER_FIRM_ID,
        client_id=DEMO_CLIENT_ID,
        role="broker_admin",
    )


def _admin_b() -> CurrentUser:
    return CurrentUser(
        user_id="22222222-2222-2222-2222-222222222222",
        broker_firm_id=DEMO_BROKER_FIRM_ID,
        client_id=CLIENT_B_ID,
        role="broker_admin",
    )


def _viewer_a() -> CurrentUser:
    return CurrentUser(
        user_id="33333333-3333-3333-3333-333333333333",
        broker_firm_id=DEMO_BROKER_FIRM_ID,
        client_id=DEMO_CLIENT_ID,
        role="broker_viewer",
    )


def _system_admin_a() -> CurrentUser:
    return CurrentUser(
        user_id="44444444-4444-4444-4444-444444444444",
        broker_firm_id=None,
        client_id=DEMO_CLIENT_ID,
        role="system_admin",
    )


@pytest.fixture(scope="module", autouse=True)
def _setup_db():
    if TEST_DB.exists():
        TEST_DB.unlink()
    Base.metadata.create_all(bind=engine)
    seed()
    # A second tenant so we can prove isolation.
    db = SessionLocal()
    try:
        if db.get(Client, CLIENT_B_ID) is None:
            db.add(
                Client(
                    id=CLIENT_B_ID,
                    name="Client B (byok)",
                    broker_firm_id=DEMO_BROKER_FIRM_ID,
                )
            )
            db.commit()
    finally:
        db.close()
    yield
    engine.dispose()
    if TEST_DB.exists():
        TEST_DB.unlink()


@pytest.fixture(autouse=True)
def _restore_budgets():
    """Reset tenant budgets after each test.

    Test modules bind the shared engine at import time, so a mutated budget
    here would leak into later modules' spend/budget tests (e.g. the gateway
    suite assumes DEMO's default budget). Snapshot + restore keeps them clean.
    """
    from app.models.client import DEFAULT_AI_MONTHLY_TOKEN_BUDGET

    yield
    db = SessionLocal()
    try:
        for cid in (DEMO_CLIENT_ID, CLIENT_B_ID):
            client = db.get(Client, cid)
            if client is not None:
                client.ai_monthly_token_budget = DEFAULT_AI_MONTHLY_TOKEN_BUDGET
        db.commit()
    finally:
        db.close()


class AsUser:
    """Sends a single request as a specific user.

    Sets `app.dependency_overrides[get_current_user]` per call and clears it
    in the `finally`, so multiple `AsUser` instances in the same test do not
    fight over the shared dict.
    """

    def __init__(self, user_factory):
        self._user_factory = user_factory
        self._tc = TestClient(app)

    def _request(self, method: str, *args, **kwargs):
        try:
            app.dependency_overrides[get_current_user] = self._user_factory
            return self._tc.request(method, *args, **kwargs)
        finally:
            app.dependency_overrides.pop(get_current_user, None)

    def get(self, *args, **kwargs):
        return self._request("GET", *args, **kwargs)

    def put(self, *args, **kwargs):
        return self._request("PUT", *args, **kwargs)

    def post(self, *args, **kwargs):
        return self._request("POST", *args, **kwargs)

    def delete(self, *args, **kwargs):
        return self._request("DELETE", *args, **kwargs)


@pytest.fixture
def client_as_admin_a() -> AsUser:
    return AsUser(_admin_a)


@pytest.fixture
def client_as_admin_b() -> AsUser:
    return AsUser(_admin_b)


@pytest.fixture
def client_as_viewer_a() -> AsUser:
    return AsUser(_viewer_a)


@pytest.fixture(autouse=True)
def _clear_rows():
    db = SessionLocal()
    try:
        db.query(ClientAIConfig).delete()
        db.query(AuditLog).filter(AuditLog.entity_type == "client_ai_config").delete()
        db.commit()
    finally:
        db.close()


def test_get_returns_204_when_unset(client_as_admin_a: AsUser) -> None:
    res = client_as_admin_a.get("/api/v1/ai-config")
    assert res.status_code == 204


def test_put_then_get_roundtrip(client_as_admin_a: AsUser) -> None:
    res = client_as_admin_a.put(
        "/api/v1/ai-config",
        json={
            "provider": "vertex",
            "endpoint": "asia-southeast1",
            "model": "gemini-2.5-flash",
            "api_key": REAL_KEY,
        },
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["provider"] == "vertex"
    assert body["model"] == "gemini-2.5-flash"
    assert body["endpoint"] == "asia-southeast1"
    # Masked tail is derived from the fingerprint, not the plaintext key, so
    # the value is stable across reads without ever decrypting.
    assert body["key_masked"].endswith(body["key_fingerprint"][-4:])
    assert body["key_fingerprint"] and len(body["key_fingerprint"]) == 16
    # Cleartext key never appears in response.
    assert REAL_KEY not in res.text

    res2 = client_as_admin_a.get("/api/v1/ai-config")
    assert res2.status_code == 200
    body2 = res2.json()
    assert body2["key_fingerprint"] == body["key_fingerprint"]
    assert body2["key_masked"] == body["key_masked"]


def test_system_admin_can_manage_selected_client_ai_config() -> None:
    client = AsUser(_system_admin_a)
    res = client.put(
        "/api/v1/ai-config",
        json={
            "provider": "vertex",
            "endpoint": "asia-southeast1",
            "model": "gemini-2.5-flash",
            "api_key": REAL_KEY,
        },
    )
    assert res.status_code == 200, res.text
    assert client.get("/api/v1/ai-config").status_code == 200


def test_only_stored_probe_activates_saved_configuration(
    client_as_admin_a: AsUser,
) -> None:
    client_as_admin_a.put(
        "/api/v1/ai-config",
        json={
            "provider": "vertex",
            "endpoint": "asia-southeast1",
            "model": "gemini-2.5-flash",
            "api_key": REAL_KEY,
        },
    )
    with patch(
        "app.api.v1.ai_config.probe_vertex",
        return_value=(None, 5, "gemini-draft"),
    ):
        draft = client_as_admin_a.post(
            "/api/v1/ai-config/test",
            json={"model": "gemini-draft"},
        )
    assert draft.status_code == 200
    with SessionLocal() as db:
        row = db.query(ClientAIConfig).filter_by(client_id=DEMO_CLIENT_ID).one()
        assert row.validation_status == "unvalidated"
        assert row.validated_model is None

    with patch(
        "app.api.v1.ai_config.probe_vertex",
        return_value=(None, 5, "gemini-2.5-flash"),
    ):
        stored = client_as_admin_a.post("/api/v1/ai-config/test")
    assert stored.status_code == 200
    with SessionLocal() as db:
        row = db.query(ClientAIConfig).filter_by(client_id=DEMO_CLIENT_ID).one()
        assert row.validation_status == "active"
        assert row.validated_model == "gemini-2.5-flash"
        assert row.validated_fingerprint == row.key_fingerprint


def test_db_stores_ciphertext_not_plaintext(client_as_admin_a: AsUser) -> None:
    client_as_admin_a.put(
        "/api/v1/ai-config",
        json={"provider": "vertex", "api_key": REAL_KEY},
    )
    db = SessionLocal()
    try:
        row = (
            db.query(ClientAIConfig)
            .filter(ClientAIConfig.client_id == DEMO_CLIENT_ID)
            .one()
        )
        assert REAL_KEY.encode() not in row.encrypted_api_key
        # Vertex packs {project_id, service_account} into the encrypted secret;
        # the raw SA JSON is the service_account half.
        packed = json.loads(decrypt_secret(row.encrypted_api_key))
        assert packed["service_account"] == REAL_KEY
        assert packed["project_id"] == "inspro-test"
    finally:
        db.close()


def test_tenant_isolation(
    client_as_admin_a: AsUser,
    client_as_admin_b: AsUser,
) -> None:
    client_as_admin_a.put(
        "/api/v1/ai-config",
        json={"provider": "vertex", "api_key": REAL_KEY},
    )
    # Client B sees no config.
    res_b = client_as_admin_b.get("/api/v1/ai-config")
    assert res_b.status_code == 204

    # Client B writes their own — different fingerprint.
    client_as_admin_b.put(
        "/api/v1/ai-config",
        json={"provider": "vertex", "api_key": OTHER_KEY},
    )
    fp_a = client_as_admin_a.get("/api/v1/ai-config").json()["key_fingerprint"]
    fp_b = client_as_admin_b.get("/api/v1/ai-config").json()["key_fingerprint"]
    assert fp_a != fp_b


def test_delete_clears_row(client_as_admin_a: AsUser, system_admin_request) -> None:
    client_as_admin_a.put(
        "/api/v1/ai-config",
        json={"provider": "vertex", "api_key": REAL_KEY},
    )
    res = system_admin_request(client_as_admin_a, "DELETE", "/api/v1/ai-config")
    assert res.status_code == 204
    assert client_as_admin_a.get("/api/v1/ai-config").status_code == 204


def test_role_gate_blocks_viewer(client_as_viewer_a: AsUser) -> None:
    res = client_as_viewer_a.get("/api/v1/ai-config")
    assert res.status_code == 403
    res2 = client_as_viewer_a.put(
        "/api/v1/ai-config",
        json={"provider": "vertex", "api_key": REAL_KEY},
    )
    assert res2.status_code == 403


def test_audit_log_never_contains_raw_key(client_as_admin_a: AsUser) -> None:
    client_as_admin_a.put(
        "/api/v1/ai-config",
        json={"provider": "vertex", "api_key": REAL_KEY},
    )
    db = SessionLocal()
    try:
        rows = (
            db.query(AuditLog)
            .filter(AuditLog.entity_type == "client_ai_config")
            .all()
        )
        assert rows
        for r in rows:
            # before is None for create; after carries fingerprint + masked tail
            blob = (r.before or {}, r.after or {})
            for d in blob:
                for v in d.values():
                    assert REAL_KEY not in str(v)
            fp = (r.after or {}).get("key_fingerprint", "")
            assert fp
            # Masked tail mirrors the response masking — fingerprint-derived,
            # not plaintext-derived, so a leaked audit row reveals no key tail.
            assert (r.after or {}).get("key_masked", "").endswith(fp[-4:])
    finally:
        db.close()


@pytest.mark.parametrize(
    ("override", "message"),
    [
        ({"token_uri": "http://169.254.169.254/computeMetadata/v1/token"}, "token_uri"),
        ({"token_uri": "https://oauth2.googleapis.com.evil.example/token"}, "token_uri"),
        ({"universe_domain": "evil.example"}, "universe_domain"),
    ],
)
def test_service_account_endpoints_are_pinned_to_google(
    client_as_admin_a: AsUser, override: dict[str, str], message: str
) -> None:
    """google-auth posts the token request to the key's own token_uri, so a
    crafted key would make the server call any URL (SSRF). Both the save and
    the draft test refuse it before anything is stored or sent."""
    saved = client_as_admin_a.put(
        "/api/v1/ai-config",
        json={
            "provider": "vertex",
            "endpoint": "asia-southeast1",
            "api_key": _key_with(**override),
        },
    )
    assert saved.status_code == 422
    assert message in saved.text

    with patch("app.api.v1.ai_config.probe_vertex") as probe:
        drafted = client_as_admin_a.post(
            "/api/v1/ai-config/test", json={"api_key": _key_with(**override)}
        )
    assert drafted.status_code == 422
    assert message in drafted.text
    probe.assert_not_called()
    with SessionLocal() as db:
        assert db.query(ClientAIConfig).count() == 0


def test_key_without_token_uri_is_refused(client_as_admin_a: AsUser) -> None:
    key = json.loads(REAL_KEY)
    key.pop("token_uri")
    res = client_as_admin_a.put(
        "/api/v1/ai-config", json={"provider": "vertex", "api_key": json.dumps(key)}
    )
    assert res.status_code == 422
    assert "token_uri" in res.text


@pytest.fixture
def _env_and_platform_would_resolve(monkeypatch: pytest.MonkeyPatch):
    """Make the fallbacks available, so a None result proves no fallback ran."""
    monkeypatch.setenv("INSPRO_AI_PROVIDER", "vertex")
    monkeypatch.setenv("VERTEX_PROJECT", "env-project")
    monkeypatch.setenv("INSPRO_AI_CONFIG_VALIDATED", "true")


def _store_byok(**fields: object) -> None:
    from app.core.ai_config import pack_vertex_secret
    from app.core.crypto import encrypt_secret
    from app.core.crypto import fingerprint as _fp

    values: dict[str, object] = {
        "client_id": DEMO_CLIENT_ID,
        "provider": "vertex",
        "endpoint": "asia-southeast1",
        "model": "gemini-2.5-flash",
        "encrypted_api_key": encrypt_secret(pack_vertex_secret("proj-x", REAL_KEY)),
        "key_fingerprint": _fp(REAL_KEY),
    }
    values.update(fields)
    with SessionLocal() as db:
        db.add(ClientAIConfig(**values))
        db.commit()


@pytest.mark.parametrize(
    "row",
    [
        {"encrypted_api_key": b"not-a-fernet-token"},  # cannot be decrypted
        {"provider": "bedrock"},  # legacy / unsupported provider
    ],
)
def test_unusable_byok_never_falls_back(_env_and_platform_would_resolve, row) -> None:
    """A company that brought its own key must not silently run on the shared
    one: AI is off for that company (claims go to manual review) instead."""
    from app.core.ai_config import load_ai_config
    from app.services.ai_extractor import AINotConfiguredError
    from app.services.ai_gateway import _require_ai_config

    _store_byok(**row)
    with SessionLocal() as db:
        assert load_ai_config(db, DEMO_CLIENT_ID) is None
        with pytest.raises(AINotConfiguredError):
            _require_ai_config(db, DEMO_CLIENT_ID)
        # A company with no BYOK row still falls back as before.
        other = load_ai_config(db, CLIENT_B_ID)
        assert other is not None and other.source == "env"


@pytest.fixture
def _production(monkeypatch: pytest.MonkeyPatch) -> None:
    """Resolve the environment as production for the AI configuration guards.
    They read the cached settings, and a full production configuration (Entra,
    secrets) is not what these tests are about."""
    from app.core import ai_config
    from app.core.settings import get_settings

    production = replace(get_settings(), env="prod")
    monkeypatch.setattr(ai_config, "get_settings", lambda: production)


def test_review_queue_fails_closed_on_an_unvalidated_key_in_production(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The queue reads the resolved environment, so every spelling the settings
    accept for production ("prod", "production") arms the gate."""
    from app.core.settings import get_settings
    from app.services.claims_review import queue

    _store_byok(validation_status="unvalidated")
    with SessionLocal() as db:
        assert queue.configuration_ready(db, DEMO_CLIENT_ID) is True  # dev: no Vertex needed
        production = replace(get_settings(), env="prod")
        monkeypatch.setattr(queue, "get_settings", lambda: production)
        assert queue.configuration_ready(db, DEMO_CLIENT_ID) is False


@pytest.mark.usefixtures("_production")
def test_inactive_byok_in_production_never_falls_back(_env_and_platform_would_resolve) -> None:
    from app.core.ai_config import load_ai_config

    _store_byok(validation_status="invalid")
    with SessionLocal() as db:
        assert load_ai_config(db, DEMO_CLIENT_ID) is None
        fallback = load_ai_config(db, CLIENT_B_ID)
        assert fallback is not None and fallback.source == "env"


def test_a_refused_stored_key_says_why(client_as_admin_a: AsUser) -> None:
    """A key saved before the endpoint check is refused when its credentials are
    built; the test button reports that reason, not "Unexpected probe error"."""
    from app.core.ai_config import pack_vertex_secret
    from app.core.crypto import encrypt_secret

    foreign = _key_with(token_uri="http://169.254.169.254/computeMetadata/v1/token")
    _store_byok(encrypted_api_key=encrypt_secret(pack_vertex_secret("proj-x", foreign)))

    res = client_as_admin_a.post("/api/v1/ai-config/test")
    assert res.status_code == 200, res.text
    assert res.json()["ok"] is False
    assert "token_uri must be https://oauth2.googleapis.com/token" in res.json()["error"]
    with SessionLocal() as db:
        row = db.query(ClientAIConfig).filter_by(client_id=DEMO_CLIENT_ID).one()
        assert row.validation_status == "invalid"
        assert "token_uri" in (row.last_validation_error or "")


# ── The company's cache and breaker follow its own key ────────────────────────


def _cached_result(client_id: str) -> str:
    from app.services import ai_cache

    key = ai_cache.make_key(
        "test-v1", "gemini-2.5-flash", {"input": "same"}, scope=ai_cache.cache_scope(client_id)
    )
    ai_cache.get_cache().set(key, {"result": client_id})
    return key


def test_replacing_or_deleting_a_key_purges_the_companys_ai_cache(
    client_as_admin_a: AsUser, system_admin_request
) -> None:
    """Cache keys name the credential source but not the key, so results
    computed under a replaced or deleted key would otherwise keep being served."""
    from app.services import ai_cache

    ai_cache.reset_cache_for_tests()
    put = {"provider": "vertex", "api_key": REAL_KEY}
    assert client_as_admin_a.put("/api/v1/ai-config", json=put).status_code == 200
    mine, theirs = _cached_result(DEMO_CLIENT_ID), _cached_result(CLIENT_B_ID)

    replaced = client_as_admin_a.put(
        "/api/v1/ai-config", json={"provider": "vertex", "api_key": OTHER_KEY}
    )
    assert replaced.status_code == 200, replaced.text
    assert not ai_cache.is_warm(mine)
    assert ai_cache.is_warm(theirs)

    mine = _cached_result(DEMO_CLIENT_ID)
    assert system_admin_request(client_as_admin_a, "DELETE", "/api/v1/ai-config").status_code == 204
    assert not ai_cache.is_warm(mine)
    assert ai_cache.is_warm(theirs)


def test_an_unreachable_cache_does_not_undo_the_key_change(
    client_as_admin_a: AsUser,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    from app.core.crypto import fingerprint
    from app.services import ai_cache

    put = {"provider": "vertex", "api_key": REAL_KEY}
    assert client_as_admin_a.put("/api/v1/ai-config", json=put).status_code == 200

    def unreachable(client_id: str) -> int:
        raise RuntimeError("Redis is unavailable; shared AI cache entries were not purged.")

    monkeypatch.setattr(ai_cache, "purge_client", unreachable)
    with caplog.at_level(logging.WARNING, logger="app.api.v1.ai_config"):
        res = client_as_admin_a.put(
            "/api/v1/ai-config", json={"provider": "vertex", "api_key": OTHER_KEY}
        )
    assert res.status_code == 200, res.text
    assert res.json()["key_fingerprint"] == fingerprint(OTHER_KEY)
    assert any("Could not purge the AI cache" in r.getMessage() for r in caplog.records)


def test_ai_status_reports_the_breaker_the_company_counts_against(
    client_as_admin_a: AsUser, client_as_admin_b: AsUser
) -> None:
    """A company on its own key has its own breaker; showing it the platform
    key's would read "closed" while every one of its calls fails fast."""
    from app.services import ai_breaker

    ai_breaker.reset_breaker_for_tests()
    _store_byok()
    company = ai_breaker.get_breaker(ai_breaker.breaker_scope("byok", DEMO_CLIENT_ID))
    for _ in range(company.threshold):
        company.record_failure()
    try:
        mine = client_as_admin_a.get("/api/v1/system/ai-status").json()
        assert (mine["source"], mine["breaker_state"]) == ("byok", "open")
        assert client_as_admin_b.get("/api/v1/system/ai-status").json()["breaker_state"] == (
            "closed"
        )
    finally:
        ai_breaker.reset_breaker_for_tests()


def test_load_ai_config_byok_takes_precedence() -> None:
    """When BYOK is set, ``load_ai_config(db, client_id)`` returns BYOK
    regardless of env vars."""
    from app.core.ai_config import load_ai_config, pack_vertex_secret
    from app.core.crypto import encrypt_secret
    from app.core.crypto import fingerprint as _fp

    db = SessionLocal()
    try:
        row = ClientAIConfig(
            client_id=DEMO_CLIENT_ID,
            provider="vertex",
            endpoint="asia-southeast1",
            model="gemini-2.5-flash",
            encrypted_api_key=encrypt_secret(pack_vertex_secret("proj-x", REAL_KEY)),
            key_fingerprint=_fp(REAL_KEY),
        )
        db.add(row)
        db.commit()

        cfg = load_ai_config(db, DEMO_CLIENT_ID)
        assert cfg is not None
        assert cfg.source == "byok"
        assert cfg.provider == "vertex"
        assert cfg.gcp_project == "proj-x"
        assert cfg.gcp_location == "asia-southeast1"
        assert cfg.api_key == REAL_KEY
        assert cfg.model == "gemini-2.5-flash"

        # Without tenant context, falls back to env.
        env_cfg = load_ai_config()
        assert env_cfg is None or env_cfg.source == "env"
    finally:
        db.close()


# ── Monthly AI budget endpoint ────────────────────────────────────────────────


def test_set_budget_updates_and_unlimited():
    admin = AsUser(_admin_a)
    # Set a concrete limit.
    r = admin.put("/api/v1/ai-spend/budget", json={"monthly_token_budget": 5_000_000})
    assert r.status_code == 200
    assert r.json()["monthly_token_budget"] == 5_000_000

    summary = admin.get("/api/v1/ai-spend/summary")
    assert summary.status_code == 200
    assert summary.json()["monthly_token_budget"] == 5_000_000

    # 0 = unlimited.
    r = admin.put("/api/v1/ai-spend/budget", json={"monthly_token_budget": 0})
    assert r.status_code == 200
    assert r.json()["monthly_token_budget"] == 0

    db = SessionLocal()
    try:
        assert db.get(Client, DEMO_CLIENT_ID).ai_monthly_token_budget == 0
    finally:
        db.close()


def test_set_budget_requires_broker_admin():
    r = AsUser(_viewer_a).put(
        "/api/v1/ai-spend/budget", json={"monthly_token_budget": 1000}
    )
    assert r.status_code == 403


def test_system_admin_can_manage_selected_client_budget():
    r = AsUser(_system_admin_a).put(
        "/api/v1/ai-spend/budget", json={"monthly_token_budget": 1234}
    )
    assert r.status_code == 200
    assert r.json()["monthly_token_budget"] == 1234


def test_set_budget_rejects_negative():
    r = AsUser(_admin_a).put(
        "/api/v1/ai-spend/budget", json={"monthly_token_budget": -5}
    )
    assert r.status_code == 422


def test_set_budget_is_tenant_scoped():
    # Admin B setting their budget must not touch tenant A's.
    AsUser(_admin_a).put("/api/v1/ai-spend/budget", json={"monthly_token_budget": 111})
    AsUser(_admin_b).put("/api/v1/ai-spend/budget", json={"monthly_token_budget": 222})
    db = SessionLocal()
    try:
        assert db.get(Client, DEMO_CLIENT_ID).ai_monthly_token_budget == 111
        assert db.get(Client, CLIENT_B_ID).ai_monthly_token_budget == 222
    finally:
        db.close()


def test_gemini_pricing_is_accurate():
    from app.services.ai_gateway import _estimate_cost_usd, _price_for

    assert _price_for("gemini-2.5-flash") == (0.30, 2.50)
    # ~30k in + 6k out per claim → ~$0.024 (guide: ~$0.03), NOT the Claude
    # default of ~$0.18 the model would fall through to without the entry.
    cost = _estimate_cost_usd("gemini-2.5-flash", 30_000, 6_000)
    assert abs(cost - 0.024) < 0.001


def test_unlisted_gemini_model_gets_gemini_pricing_not_claude():
    """A Gemini release with no price row must not be billed as Claude Sonnet.

    Newer Gemini models land here the moment one is selected as the default,
    before anyone adds a `_PRICE_TABLE` row. Falling through to `_DEFAULT_PRICE`
    overstates spend ~10x on every call, and that inflated figure is what
    operators size the platform cap against.
    """
    from app.core.ai_config import DEFAULT_VERTEX_MODEL
    from app.services.ai_gateway import _DEFAULT_PRICE, _PRICE_TABLE, _price_for

    # Synthetic UNLISTED ids only. Asserting a specific rate for
    # DEFAULT_VERTEX_MODEL would pin the default to having no price row, and
    # `_family_price` tells you to add one once list pricing is published —
    # the test would then fail for doing exactly what it prescribes.
    for model in ("gemini-4-flash", "gemini-9-flash"):
        assert model not in _PRICE_TABLE, f"{model} is listed; pick an unlisted id"
        assert _price_for(model) == (0.30, 2.50), model

    assert _price_for("gemini-9-flash-lite") == (0.10, 0.40)
    assert _price_for("gemini-9-pro") == (1.25, 10.0)
    # Non-Gemini models are untouched by the family fallback.
    assert _price_for("some-unknown-model") == _DEFAULT_PRICE

    # What actually matters for the default, whether or not it gains a row:
    # never Claude's rate. This holds both before and after one is added.
    assert _price_for(DEFAULT_VERTEX_MODEL) != _DEFAULT_PRICE


def test_summary_reports_input_output_split():
    from app.models import AISpendLog

    # NOTE: test modules bind the shared engine at import time, so a leftover
    # spend row here pollutes later gateway spend-count tests. This test both
    # sets up and tears down its own DEMO_CLIENT_ID rows.
    db = SessionLocal()
    try:
        db.query(AISpendLog).filter(AISpendLog.client_id == DEMO_CLIENT_ID).delete(
            synchronize_session=False
        )
        db.add(
            AISpendLog(
                client_id=DEMO_CLIENT_ID,
                operation="ai_claim_extract",
                model="gemini-2.5-flash",
                input_tokens=1024,
                output_tokens=2856,
                cost_estimate_usd=0.0074,
                cache_hit=False,
            )
        )
        db.commit()
    finally:
        db.close()

    try:
        body = AsUser(_admin_a).get("/api/v1/ai-spend/summary").json()
        assert body["month_to_date_input_tokens"] == 1024
        assert body["month_to_date_output_tokens"] == 2856
        assert body["month_to_date_tokens"] == 3880
        op = next(
            o for o in body["by_operation"] if o["operation"] == "ai_claim_extract"
        )
        assert op["input_tokens"] == 1024
        assert op["output_tokens"] == 2856
        assert op["tokens"] == 3880
    finally:
        db = SessionLocal()
        try:
            db.query(AISpendLog).filter(
                AISpendLog.client_id == DEMO_CLIENT_ID
            ).delete(synchronize_session=False)
            db.commit()
        finally:
            db.close()
