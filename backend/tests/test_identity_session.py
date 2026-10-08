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
    DEMO_USER_ID,
)
from app.core.identity import (  # noqa: E402
    accessible_clients,
    assert_client_accessible,
    platform_access_level,
    resolve_active_client_id,
)
from app.db.base import Base  # noqa: E402
from app.db.session import SessionLocal, engine  # noqa: E402
from app.main import app  # noqa: E402
from app.models import BrokerFirm, Client, PolicyYear, User, UserClientAccess  # noqa: E402
from app.models.platform import PlatformAccessGrant  # noqa: E402
from scripts.seed_demo import DEMO_CLIENT_2_ID, seed  # noqa: E402

FIRM2_ID = "00000000-0000-0000-0000-0000000000f2"
CLIENT_OTHER_ID = "00000000-0000-0000-0000-0000000000f3"
CLIENT_USER_ID = "00000000-0000-0000-0000-0000000000e1"
FIRM2_SLUG = "rival-broker"


@pytest.fixture(scope="module", autouse=True)
def _setup_db():
    if TEST_DB.exists():
        TEST_DB.unlink()
    Base.metadata.create_all(bind=engine)
    seed()
    with SessionLocal() as s:
        s.add(BrokerFirm(id=FIRM2_ID, name="Rival Broker Firm", slug=FIRM2_SLUG))
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


def test_system_admin_reaches_the_owner_firm_only_without_a_grant() -> None:
    """Standing access covers the platform owner's firm (the seeded demo firm);
    every other firm needs an access grant."""
    with SessionLocal() as db:
        clients = accessible_clients(
            role="system_admin", broker_firm_id=None, user_id=DEMO_USER_ID, db=db,
        )
        ids = {c.id for c in clients}
        assert {DEMO_CLIENT_ID, DEMO_CLIENT_2_ID} <= ids
        assert CLIENT_OTHER_ID not in ids
        assert assert_client_accessible(
            role="system_admin", broker_firm_id=None, user_id=DEMO_USER_ID,
            client_id=CLIENT_OTHER_ID, db=db,
        ) is None
        assert platform_access_level(db, DEMO_USER_ID, DEMO_BROKER_FIRM_ID) == "standing"
        assert platform_access_level(db, DEMO_USER_ID, FIRM2_ID) is None


def test_resolve_rejects_inaccessible_selection() -> None:
    with SessionLocal() as db:
        assert resolve_active_client_id(
            role="broker_admin", broker_firm_id=DEMO_BROKER_FIRM_ID,
            user_id="x", requested_client_id=CLIENT_OTHER_ID, db=db,
        ) is None


# ── Entra: an unprovisioned account is refused, machine-readably ─────────────
PLATFORM_TID = "2e4f6a8b-1c3d-4e5f-9a7b-8c9d0e1f2a3b"


def _entra_mode(monkeypatch) -> None:
    """Entra mode with the platform directory set (undone with `monkeypatch`;
    `_reset_settings` drops the cached settings afterwards)."""
    from app.core.settings import clear_settings_cache

    monkeypatch.setenv("INSPRO_AUTH_MODE", "entra")
    monkeypatch.setenv("INSPRO_ENTRA_TENANT_ID", PLATFORM_TID)
    monkeypatch.setenv("INSPRO_ENTRA_CLIENT_ID", "8b9c0d1e-2f3a-4b5c-8d6e-7f8a9b0c1d2e")
    clear_settings_cache()


@pytest.fixture(autouse=True)
def _reset_settings():
    from app.core.settings import clear_settings_cache

    yield
    clear_settings_cache()


def _stub_directory(monkeypatch, claims: dict) -> None:
    """A verifier that issues `claims` from whichever directory is asked."""
    from app.core import auth as auth_mod

    monkeypatch.setattr(
        auth_mod, "verify_entra_token",
        lambda _t, _s, **kw: {**claims, "tid": kw["tenant_id"]},
    )


def _entra_403(monkeypatch, claims: dict) -> object:
    """Run `_entra_principal` in entra mode with a stubbed token verifier and
    return the raised HTTPException."""
    from fastapi import HTTPException

    from app.core import auth as auth_mod

    _entra_mode(monkeypatch)
    _stub_directory(monkeypatch, claims)
    with SessionLocal() as db:
        with pytest.raises(HTTPException) as exc:
            auth_mod._entra_principal("Bearer stub-token", db)
    return exc.value


def test_unknown_entra_user_refused_with_no_access_code(monkeypatch) -> None:
    """A tenant user who isn't on the platform's user list gets a coded 403.

    The frontend routes on `detail.code` (identity-level refusal ends the
    session) rather than the message, so the code is part of the contract.
    """
    err = _entra_403(
        monkeypatch, {"oid": "oid-not-provisioned", "email": "stranger@inspro.test"}
    )
    assert err.status_code == 403
    assert err.detail["code"] == "no_access"


def test_disabled_entra_user_refused(monkeypatch) -> None:
    with SessionLocal() as db:
        db.add(
            User(
                id="00000000-0000-0000-0000-0000000000d1",
                email="disabled@inspro.test",
                display_name="Disabled User",
                external_id="oid-disabled",
                broker_firm_id=DEMO_BROKER_FIRM_ID,
                role="broker_viewer",
                status="disabled",
            )
        )
        db.commit()
    err = _entra_403(monkeypatch, {"oid": "oid-disabled", "email": "disabled@inspro.test"})
    assert err.status_code == 403
    assert err.detail["code"] == "no_access"


# ── Stale or missing company selection on writes ─────────────────────────────
def test_write_with_inaccessible_company_is_refused(client: TestClient) -> None:
    """Only reads fall back to the default company. A write naming a company
    the caller cannot reach must not land on whichever company the fallback
    would pick."""
    from app.models import PolicyYear

    with SessionLocal() as db:
        before = db.query(PolicyYear).count()
    res = client.post(
        "/api/v1/policy-years",
        headers={"X-Inspro-Client": CLIENT_OTHER_ID},
        json={"start_date": "2035-01-01", "end_date": "2035-12-31"},
    )
    assert res.status_code == 409
    assert res.json()["detail"]["code"] == "client_selection_stale"
    with SessionLocal() as db:
        assert db.query(PolicyYear).count() == before


def _principal(role: str, firm_id: str | None):
    from app.core.auth import Principal

    return Principal(user_id="identity-probe", broker_firm_id=firm_id, role=role)


def test_system_admin_write_without_company_needs_a_choice() -> None:
    """A platform admin reaches several firms, so "the first client" is arbitrary:
    reads may default, writes that act on a company must name one."""
    from fastapi import HTTPException

    from app.core.auth import _build_current_user
    from app.core.deps import require_client_id

    with SessionLocal() as db:
        read = _build_current_user(_principal("system_admin", None), None, db, read_only=True)
        assert read.client_id is not None
        write = _build_current_user(_principal("system_admin", None), None, db, read_only=False)
        assert write.client_id is None
        with pytest.raises(HTTPException) as exc:
            require_client_id(write)
        assert exc.value.status_code == 409
        assert exc.value.detail["code"] == "client_selection_required"
        # Other roles keep their single-company default for writes.
        broker = _build_current_user(
            _principal("broker_admin", DEMO_BROKER_FIRM_ID), None, db, read_only=False
        )
        assert broker.client_id is not None


def test_system_admin_routes_by_the_active_companys_firm(monkeypatch) -> None:
    from app.core import auth as auth_mod
    from app.core.auth import CurrentUser

    routed: list[str | None] = []
    monkeypatch.setattr(auth_mod, "set_search_path", lambda _db, firm: routed.append(firm))
    with SessionLocal() as db:
        for firm_id, client_id in (
            (None, CLIENT_OTHER_ID),
            # A legacy firm on the row never overrides the active company's firm.
            (DEMO_BROKER_FIRM_ID, CLIENT_OTHER_ID),
            (None, None),
        ):
            auth_mod._route_to_firm(db, CurrentUser(
                user_id="sa", broker_firm_id=firm_id, client_id=client_id, role="system_admin",
            ))
    assert routed == [FIRM2_ID, FIRM2_ID, None]


def test_firm_role_with_another_firms_company_is_refused(monkeypatch) -> None:
    from fastapi import HTTPException

    from app.core import auth as auth_mod
    from app.core.auth import CurrentUser

    routed: list[str | None] = []
    monkeypatch.setattr(auth_mod, "set_search_path", lambda _db, firm: routed.append(firm))
    with SessionLocal() as db:
        auth_mod._route_to_firm(db, CurrentUser(
            user_id="b", broker_firm_id=DEMO_BROKER_FIRM_ID, client_id=DEMO_CLIENT_ID,
            role="broker_admin",
        ))
        for role in ("broker_admin", "client_hr"):
            with pytest.raises(HTTPException) as exc:
                auth_mod._route_to_firm(db, CurrentUser(
                    user_id="b", broker_firm_id=DEMO_BROKER_FIRM_ID, client_id=CLIENT_OTHER_ID,
                    role=role,
                ))
            assert exc.value.status_code == 404
    assert routed == [DEMO_BROKER_FIRM_ID]


def test_platform_admin_firm_library_write_needs_a_company() -> None:
    """A platform admin reaches a firm's library only through the selected
    company's firm. With none selected the row would land in `public`."""
    from app.core.auth import CurrentUser, get_current_user
    from app.models import Product

    def as_admin(client_id: str | None) -> TestClient:
        app.dependency_overrides[get_current_user] = lambda: CurrentUser(
            user_id="library-admin", broker_firm_id=None, client_id=client_id,
            role="system_admin",
        )
        return TestClient(app)

    body = {"code": "PLATLIB", "display_name": "Platform library product"}
    try:
        refused = as_admin(None).post("/api/v1/schemas/products?scope=firm", json=body)
        assert refused.status_code == 409
        assert refused.json()["detail"]["code"] == "client_selection_required"
        with SessionLocal() as db:
            assert db.query(Product).filter(Product.code == "PLATLIB").count() == 0
        created = as_admin(DEMO_CLIENT_ID).post("/api/v1/schemas/products?scope=firm", json=body)
        assert created.status_code == 201, created.text
        assert created.json()["client_id"] is None
    finally:
        app.dependency_overrides.pop(get_current_user, None)


# ── Master admin access model: standing access + time-limited grants ─────────
@pytest.fixture(autouse=True)
def _no_grants():
    yield
    with SessionLocal() as db:
        db.query(PlatformAccessGrant).delete()
        db.commit()


@pytest.fixture
def platform_admin(monkeypatch) -> TestClient:
    """Mock auth as the platform admin, resolved for real on every request that
    names a company (the seeded demo user's id, so grants can reference it)."""
    monkeypatch.setenv("INSPRO_MOCK_ROLE", "system_admin")
    return TestClient(app)


def _grant(scope: str, *, hours: float = 1, revoked: bool = False) -> None:
    from datetime import UTC, datetime, timedelta

    now = datetime.now(UTC)
    with SessionLocal() as db:
        db.add(PlatformAccessGrant(
            user_id=DEMO_USER_ID, broker_firm_id=FIRM2_ID,
            reason="Support ticket 4411: claims export check", scope=scope,
            expires_at=now + timedelta(hours=hours), revoked_at=now if revoked else None,
        ))
        db.commit()


def _create_year(client: TestClient, company: str):
    return client.post(
        "/api/v1/policy-years",
        headers={"X-Inspro-Client": company},
        json={"start_date": "2036-01-01", "end_date": "2036-12-31"},
    )


@pytest.mark.parametrize(
    ("scope", "hours", "revoked", "expected"),
    [
        ("read", 1, False, "read"),
        ("write", 1, False, "write"),
        ("write", -1, False, None),  # expired
        ("write", 1, True, None),  # revoked
    ],
)
def test_only_a_live_grant_reaches_another_firm(scope, hours, revoked, expected) -> None:
    _grant(scope, hours=hours, revoked=revoked)
    with SessionLocal() as db:
        assert platform_access_level(db, DEMO_USER_ID, FIRM2_ID) == expected
        clients = accessible_clients(
            role="system_admin", broker_firm_id=None, user_id=DEMO_USER_ID, db=db,
        )
        assert (CLIENT_OTHER_ID in {c.id for c in clients}) is (expected is not None)
        # A grant is break-glass: the default company stays in the owner's firm.
        assert clients[0].broker_firm_id == DEMO_BROKER_FIRM_ID


def test_read_grant_is_read_only(platform_admin: TestClient) -> None:
    _grant("read")
    headers = {"X-Inspro-Client": CLIENT_OTHER_ID}
    me = platform_admin.get("/api/v1/me", headers=headers).json()
    assert me["active_client_id"] == CLIENT_OTHER_ID
    assert me["platform_access"] == "read"
    assert me["firm"]["id"] == FIRM2_ID
    # The company picker groups a platform admin's companies by firm.
    assert {"id": CLIENT_OTHER_ID, "name": "Other-firm client", "broker_firm_id": FIRM2_ID,
            "firm_name": "Rival Broker Firm"} in me["accessible_clients"]
    assert platform_admin.get("/api/v1/policy-years", headers=headers).status_code == 200
    with SessionLocal() as db:
        before = db.query(PolicyYear).count()
    refused = _create_year(platform_admin, CLIENT_OTHER_ID)
    assert refused.status_code == 403, refused.text
    assert refused.json()["detail"] == {
        "code": "platform_access_read_only",
        "message": "Your access to this broker is read-only.",
    }
    cleared = platform_admin.delete(
        "/api/v1/employees", headers=headers, params={"policy_year_id": "any"}
    )
    assert cleared.status_code == 403
    assert cleared.json()["detail"]["code"] == "platform_access_read_only"
    with SessionLocal() as db:
        assert db.query(PolicyYear).count() == before


def test_write_grant_allows_changes(platform_admin: TestClient) -> None:
    _grant("read")
    _grant("write")  # the widest live grant wins
    me = platform_admin.get("/api/v1/me", headers={"X-Inspro-Client": CLIENT_OTHER_ID}).json()
    assert me["platform_access"] == "write"
    created = _create_year(platform_admin, CLIENT_OTHER_ID)
    assert created.status_code == 201, created.text
    assert created.json()["client_id"] == CLIENT_OTHER_ID


@pytest.mark.parametrize(("hours", "revoked"), [(-1, False), (1, True)])
def test_lapsed_grant_is_an_inaccessible_company(
    platform_admin: TestClient, hours: float, revoked: bool
) -> None:
    """An expired or revoked grant behaves like any company the caller cannot
    reach: writes are refused as stale, reads fall back to the default."""
    _grant("write", hours=hours, revoked=revoked)
    stale = _create_year(platform_admin, CLIENT_OTHER_ID)
    assert stale.status_code == 409
    assert stale.json()["detail"]["code"] == "client_selection_stale"
    me = platform_admin.get("/api/v1/me", headers={"X-Inspro-Client": CLIENT_OTHER_ID}).json()
    assert me["active_client_id"] != CLIENT_OTHER_ID
    assert CLIENT_OTHER_ID not in {c["id"] for c in me["accessible_clients"]}
    assert me["platform_access"] == "standing"


# ── /me: firm and platform console ───────────────────────────────────────────
def test_me_names_the_callers_firm(client: TestClient) -> None:
    body = client.get("/api/v1/me").json()
    assert body["firm"] == {
        "id": DEMO_BROKER_FIRM_ID, "name": "Demo Broker Firm",
        "slug": "demo-broker-firm", "is_platform_owner": True,
    }
    assert body["platform_console"] is False
    assert body["platform_access"] is None


def test_me_offers_the_console_to_the_platform_admin_on_a_platform_host(
    platform_admin: TestClient,
) -> None:
    headers = {"X-Inspro-Client": DEMO_CLIENT_ID}
    body = platform_admin.get("/api/v1/me", headers=headers).json()
    assert body["role"] == "system_admin"
    assert body["platform_console"] is True
    assert body["platform_access"] == "standing"
    assert body["firm"]["id"] == DEMO_BROKER_FIRM_ID
    # A broker's own host never offers the console.
    elsewhere = TestClient(app, base_url=f"http://{FIRM2_SLUG}.localhost")
    assert elsewhere.get("/api/v1/me", headers=headers).json()["platform_console"] is False


def test_invitation_is_accepted_only_on_a_host_the_account_may_use(monkeypatch) -> None:
    """Signing in on another firm's host neither signs in nor accepts the
    invitation; on the account's own firm's host it does both. The other firm
    signs in through the same directory, so only the firm check stands in the
    way there."""
    from datetime import UTC, datetime, timedelta

    from fastapi import HTTPException

    from app.core import auth as auth_mod
    from app.core.tenant_resolution import FirmContext
    from app.models import IdentityProvider
    from app.models.invitation import Invitation

    oid, email = "oid-invited-elsewhere", "invited.elsewhere@inspro.test"
    with SessionLocal() as db:
        account = User(email=email, external_id=oid, external_tid=PLATFORM_TID,
                       broker_firm_id=DEMO_BROKER_FIRM_ID, role="broker_viewer",
                       status="invited")
        invite = Invitation(email=email, broker_firm_id=DEMO_BROKER_FIRM_ID, role="broker_viewer",
                            token="invited-elsewhere-token", status="pending",
                            expires_at=datetime.now(UTC) + timedelta(days=1))
        db.add_all([account, invite, IdentityProvider(
            broker_firm_id=FIRM2_ID, kind="entra", enabled=True, entra_tenant_id=PLATFORM_TID,
        )])
        db.commit()
        account_id, invite_id = account.id, invite.id
    _entra_mode(monkeypatch)
    _stub_directory(monkeypatch, {"oid": oid})

    def host(firm_id: str) -> FirmContext:
        return FirmContext(firm_id=firm_id, firm_slug=None, surface="all",
                           is_platform_host=False, database_key="default", status="active",
                           host="staff.example.test")

    with SessionLocal() as db:
        with pytest.raises(HTTPException) as exc:
            auth_mod._entra_principal("Bearer stub", db, firm=host(FIRM2_ID))
        assert exc.value.detail["code"] == "no_access"
    with SessionLocal() as db:
        assert db.get(User, account_id).status == "invited"
        assert db.get(Invitation, invite_id).status == "pending"
        principal = auth_mod._entra_principal("Bearer stub", db, firm=host(DEMO_BROKER_FIRM_ID))
        assert principal.user_id == account_id
    with SessionLocal() as db:
        assert db.get(User, account_id).status == "active"
        assert db.get(Invitation, invite_id).status == "accepted"
    with SessionLocal() as db:
        db.query(IdentityProvider).filter(IdentityProvider.broker_firm_id == FIRM2_ID).delete()
        db.commit()
