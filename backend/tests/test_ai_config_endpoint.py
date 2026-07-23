"""BYOK /ai-config endpoint — encryption, redaction, tenant isolation, role gate."""
from __future__ import annotations

import json
import os
from pathlib import Path

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
    }
)
OTHER_KEY = json.dumps(
    {
        "type": "service_account",
        "project_id": "inspro-test-b",
        "private_key": "-----BEGIN PRIVATE KEY-----\nBBB\n-----END PRIVATE KEY-----\n",
        "client_email": "svc@inspro-test-b.iam.gserviceaccount.com",
    }
)


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


def test_delete_clears_row(client_as_admin_a: AsUser) -> None:
    client_as_admin_a.put(
        "/api/v1/ai-config",
        json={"provider": "vertex", "api_key": REAL_KEY},
    )
    res = client_as_admin_a.delete("/api/v1/ai-config")
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
