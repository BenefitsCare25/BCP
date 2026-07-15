"""Identity & client-switching: /me, the X-Inspro-Client header, the broker-
firm hard boundary, and the per-role access rules in app.core.identity.

Mock auth resolves the active client from the DB when an X-Inspro-Client header
is present, so the HTTP tests exercise the real resolution path without
overriding get_current_user.
"""
from __future__ import annotations

import os
from pathlib import Path

TEST_DB = Path(__file__).parent / "_test_identity_session.db"
os.environ["INSPRO_DATABASE_URL"] = f"sqlite:///{TEST_DB}"

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.core.auth import (  # noqa: E402
    DEMO_BROKER_FIRM_ID,
    DEMO_CLIENT_ID,
)
from app.core.identity import (  # noqa: E402
    accessible_clients,
    assert_client_accessible,
    resolve_active_client_id,
)
from app.db.base import Base  # noqa: E402
from app.db.session import SessionLocal, engine  # noqa: E402
from app.main import app  # noqa: E402
from app.models import BrokerFirm, Client, User, UserClientAccess  # noqa: E402
from scripts.seed_demo import DEMO_CLIENT_2_ID, seed  # noqa: E402

FIRM2_ID = "00000000-0000-0000-0000-0000000000f2"
CLIENT_OTHER_ID = "00000000-0000-0000-0000-0000000000f3"
CLIENT_USER_ID = "00000000-0000-0000-0000-0000000000e1"


@pytest.fixture(scope="module", autouse=True)
def _setup_db():
    if TEST_DB.exists():
        TEST_DB.unlink()
    Base.metadata.create_all(bind=engine)
    seed()
    with SessionLocal() as s:
        s.add(BrokerFirm(id=FIRM2_ID, name="Rival Broker Firm"))
        s.flush()
        s.add(Client(id=CLIENT_OTHER_ID, name="Other-firm client", broker_firm_id=FIRM2_ID))
        # A client-scoped user in the demo firm, granted only DEMO_CLIENT_ID.
        s.add(
            User(
                id=CLIENT_USER_ID,
                email="hr@inspro.test",
                display_name="HR User",
                broker_firm_id=DEMO_BROKER_FIRM_ID,
                role="client_hr",
                status="active",
            )
        )
        s.flush()
        s.add(UserClientAccess(user_id=CLIENT_USER_ID, client_id=DEMO_CLIENT_ID))
        s.commit()
    yield
    engine.dispose()
    if TEST_DB.exists():
        TEST_DB.unlink()


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


# ── /me ──────────────────────────────────────────────────────────────────────
def test_me_defaults_to_demo_client(client: TestClient) -> None:
    res = client.get("/api/v1/me")
    assert res.status_code == 200
    body = res.json()
    assert body["active_client_id"] == DEMO_CLIENT_ID
    assert body["role"] == "broker_admin"
    ids = {c["id"] for c in body["accessible_clients"]}
    # Broker reaches every client in their firm; never another firm's.
    assert {DEMO_CLIENT_ID, DEMO_CLIENT_2_ID} <= ids
    assert CLIENT_OTHER_ID not in ids


def test_me_honours_client_switch_header(client: TestClient) -> None:
    res = client.get("/api/v1/me", headers={"X-Inspro-Client": DEMO_CLIENT_2_ID})
    assert res.status_code == 200
    assert res.json()["active_client_id"] == DEMO_CLIENT_2_ID


def test_switch_to_other_firm_client_falls_back(client: TestClient) -> None:
    # An inaccessible selection falls back to the user's default (no 403 — a
    # hard error would lock out anyone with a stale stored client), and must
    # NOT adopt the other firm's client.
    res = client.get("/api/v1/me", headers={"X-Inspro-Client": CLIENT_OTHER_ID})
    assert res.status_code == 200
    body = res.json()
    assert body["active_client_id"] != CLIENT_OTHER_ID
    assert body["active_client_id"] in {c["id"] for c in body["accessible_clients"]}


def test_switch_to_unknown_client_falls_back(client: TestClient) -> None:
    res = client.get(
        "/api/v1/me", headers={"X-Inspro-Client": "00000000-0000-0000-0000-000000000999"}
    )
    assert res.status_code == 200
    body = res.json()
    assert body["active_client_id"] in {c["id"] for c in body["accessible_clients"]}


def test_resource_endpoint_scopes_to_active_client(client: TestClient) -> None:
    """Switching the active client changes which policy years are visible."""
    own = client.get("/api/v1/policy-years").json()
    assert all(isinstance(r["id"], str) for r in own)
    # Demo client has a seeded 2026 policy year; the empty second client has none.
    switched = client.get(
        "/api/v1/policy-years", headers={"X-Inspro-Client": DEMO_CLIENT_2_ID}
    ).json()
    assert switched == [] or all(r["client_id"] == DEMO_CLIENT_2_ID for r in switched)


# ── identity access rules (unit) ─────────────────────────────────────────────
def test_broker_reaches_whole_firm_only() -> None:
    with SessionLocal() as db:
        clients = accessible_clients(
            role="broker_admin", broker_firm_id=DEMO_BROKER_FIRM_ID,
            user_id="anyone", db=db,
        )
        ids = {c.id for c in clients}
        assert {DEMO_CLIENT_ID, DEMO_CLIENT_2_ID} <= ids
        assert CLIENT_OTHER_ID not in ids


def test_client_role_limited_to_grants() -> None:
    with SessionLocal() as db:
        clients = accessible_clients(
            role="client_hr", broker_firm_id=DEMO_BROKER_FIRM_ID,
            user_id=CLIENT_USER_ID, db=db,
        )
        assert {c.id for c in clients} == {DEMO_CLIENT_ID}
        # Not granted the second client even though it's in the same firm.
        assert assert_client_accessible(
            role="client_hr", broker_firm_id=DEMO_BROKER_FIRM_ID,
            user_id=CLIENT_USER_ID, client_id=DEMO_CLIENT_2_ID, db=db,
        ) is None


def test_system_admin_reaches_all_firms() -> None:
    with SessionLocal() as db:
        clients = accessible_clients(
            role="system_admin", broker_firm_id=None, user_id="sa", db=db,
        )
        ids = {c.id for c in clients}
        assert {DEMO_CLIENT_ID, CLIENT_OTHER_ID} <= ids


def test_resolve_rejects_inaccessible_selection() -> None:
    with SessionLocal() as db:
        assert resolve_active_client_id(
            role="broker_admin", broker_firm_id=DEMO_BROKER_FIRM_ID,
            user_id="x", requested_client_id=CLIENT_OTHER_ID, db=db,
        ) is None
