"""Provisioning console: firm/client/user/invitation management + authz.

Mock auth gives a demo broker_admin (firm = DEMO_BROKER_FIRM_ID), so the
broker-admin paths run without overrides. system_admin paths override
get_current_user.
"""
from __future__ import annotations

import os
from pathlib import Path

TEST_DB = Path(__file__).parent / "_test_admin_provisioning.db"
os.environ["INSPRO_DATABASE_URL"] = f"sqlite:///{TEST_DB}"

from datetime import date  # noqa: E402

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.core.auth import DEMO_BROKER_FIRM_ID, CurrentUser, get_current_user  # noqa: E402
from app.db.base import Base  # noqa: E402
from app.db.session import SessionLocal, engine  # noqa: E402
from app.main import app  # noqa: E402
from app.models import BrokerFirm, Client, PolicyYear, User  # noqa: E402
from app.models.policy_year import PolicyYearStatus  # noqa: E402
from scripts.seed_demo import seed  # noqa: E402

FIRM2_ID = "00000000-0000-0000-0000-0000000000a2"
CLIENT_F2_ID = "00000000-0000-0000-0000-0000000000a3"


def _system_admin() -> CurrentUser:
    return CurrentUser(
        user_id="sa-1", broker_firm_id=None, client_id=None, role="system_admin",
    )


@pytest.fixture(scope="module", autouse=True)
def _setup_db():
    if TEST_DB.exists():
        TEST_DB.unlink()
    Base.metadata.create_all(bind=engine)
    seed()
    with SessionLocal() as s:
        s.add(BrokerFirm(id=FIRM2_ID, name="Firm Two"))
        s.flush()
        s.add(Client(id=CLIENT_F2_ID, name="F2 Client", broker_firm_id=FIRM2_ID))
        s.commit()
    yield
    engine.dispose()
    if TEST_DB.exists():
        TEST_DB.unlink()


@pytest.fixture
def broker() -> TestClient:
    # Real mock auth = demo broker_admin.
    return TestClient(app)


@pytest.fixture
def sysadmin() -> TestClient:
    app.dependency_overrides[get_current_user] = _system_admin
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.pop(get_current_user, None)


# ── Firms (system_admin only) ─────────────────────────────────────────────────
def test_broker_cannot_create_firm(broker: TestClient) -> None:
    res = broker.post("/api/v1/admin/broker-firms", json={"name": "Sneaky"})
    assert res.status_code == 403


def test_system_admin_creates_firm(sysadmin: TestClient) -> None:
    res = sysadmin.post("/api/v1/admin/broker-firms", json={"name": "Brand New Firm"})
    assert res.status_code == 201
    assert res.json()["name"] == "Brand New Firm"


# ── Clients ───────────────────────────────────────────────────────────────────
def test_broker_creates_client_in_own_firm(broker: TestClient) -> None:
    res = broker.post("/api/v1/admin/clients", json={"name": "Acme Co"})
    assert res.status_code == 201
    body = res.json()
    assert body["broker_firm_id"] == DEMO_BROKER_FIRM_ID
    # Shows up in the switcher's accessible clients.
    me = broker.get("/api/v1/me").json()
    assert body["id"] in {c["id"] for c in me["accessible_clients"]}


def test_broker_cannot_patch_other_firm_client(broker: TestClient) -> None:
    res = broker.patch(f"/api/v1/admin/clients/{CLIENT_F2_ID}", json={"name": "hijack"})
    assert res.status_code == 404


def test_broker_client_list_scoped_to_firm(broker: TestClient) -> None:
    rows = broker.get("/api/v1/admin/clients").json()
    assert all(c["broker_firm_id"] == DEMO_BROKER_FIRM_ID for c in rows)
    assert CLIENT_F2_ID not in {c["id"] for c in rows}


def test_broker_deletes_empty_client(broker: TestClient) -> None:
    created = broker.post("/api/v1/admin/clients", json={"name": "Disposable Co"}).json()
    res = broker.delete(f"/api/v1/admin/clients/{created['id']}")
    assert res.status_code == 204
    rows = broker.get("/api/v1/admin/clients").json()
    assert created["id"] not in {c["id"] for c in rows}


def test_broker_cannot_delete_other_firm_client(broker: TestClient) -> None:
    res = broker.delete(f"/api/v1/admin/clients/{CLIENT_F2_ID}")
    assert res.status_code == 404


def test_delete_client_blocked_while_it_has_benefit_years(broker: TestClient) -> None:
    created = broker.post("/api/v1/admin/clients", json={"name": "Has Years Co"}).json()
    with SessionLocal() as s:
        s.add(
            PolicyYear(
                client_id=created["id"],
                year=2027,
                start_date=date(2027, 1, 1),
                end_date=date(2027, 12, 31),
                status=PolicyYearStatus.active,
            )
        )
        s.commit()
    res = broker.delete(f"/api/v1/admin/clients/{created['id']}")
    assert res.status_code == 409
    rows = broker.get("/api/v1/admin/clients").json()
    assert created["id"] in {c["id"] for c in rows}  # still present


# ── Invitations / users ───────────────────────────────────────────────────────
def test_invite_provisions_user_and_invitation(broker: TestClient) -> None:
    res = broker.post(
        "/api/v1/admin/invitations",
        json={"email": "New.Hire@Inspro.test", "role": "broker_viewer"},
    )
    assert res.status_code == 201
    body = res.json()
    assert body["email"] == "new.hire@inspro.test"  # normalized
    assert body["token"]
    users = broker.get("/api/v1/admin/users").json()
    invited = next(u for u in users if u["email"] == "new.hire@inspro.test")
    assert invited["status"] == "invited"
    assert invited["role"] == "broker_viewer"
    pending = broker.get("/api/v1/admin/invitations").json()
    assert any(i["email"] == "new.hire@inspro.test" for i in pending)


def test_invite_client_role_grants_client_access(broker: TestClient) -> None:
    # Use a client in the demo firm.
    clients = broker.get("/api/v1/admin/clients").json()
    target_client = clients[0]["id"]
    res = broker.post(
        "/api/v1/admin/invitations",
        json={"email": "hr2@inspro.test", "role": "client_hr",
              "client_ids": [target_client]},
    )
    assert res.status_code == 201
    users = broker.get("/api/v1/admin/users").json()
    u = next(u for u in users if u["email"] == "hr2@inspro.test")
    assert u["client_ids"] == [target_client]


def test_invite_can_carry_a_name_and_it_survives_to_the_user_row(
    broker: TestClient,
) -> None:
    """Without this the users list shows the email as the name AND as the
    subtitle — the same string twice — until someone edits it by hand."""
    res = broker.post(
        "/api/v1/admin/invitations",
        json={"email": "named@inspro.test", "role": "broker_viewer",
              "display_name": "  Chee Leong Ong  "},
    )
    assert res.status_code == 201, res.text
    users = broker.get("/api/v1/admin/users").json()
    u = next(u for u in users if u["email"] == "named@inspro.test")
    assert u["display_name"] == "Chee Leong Ong"  # trimmed


def test_a_users_name_can_be_set_and_cleared_after_the_fact(
    broker: TestClient,
) -> None:
    """Most rows predate the name field, so editing is the path that matters.
    An emptied box CLEARS the name; omitting the key leaves it alone."""
    broker.post("/api/v1/admin/invitations",
                json={"email": "rename@inspro.test", "role": "broker_viewer"})
    users = broker.get("/api/v1/admin/users").json()
    uid = next(u["id"] for u in users if u["email"] == "rename@inspro.test")

    named = broker.patch(f"/api/v1/admin/users/{uid}",
                         json={"display_name": "Fiona Lee"})
    assert named.status_code == 200, named.text
    assert named.json()["display_name"] == "Fiona Lee"

    # A patch that doesn't mention the name must not wipe it.
    kept = broker.patch(f"/api/v1/admin/users/{uid}", json={"role": "broker_admin"})
    assert kept.json()["display_name"] == "Fiona Lee"

    cleared = broker.patch(f"/api/v1/admin/users/{uid}", json={"display_name": ""})
    assert cleared.json()["display_name"] is None


def test_invite_duplicate_email_conflicts(broker: TestClient) -> None:
    broker.post("/api/v1/admin/invitations",
                json={"email": "dup@inspro.test", "role": "broker_viewer"})
    res = broker.post("/api/v1/admin/invitations",
                      json={"email": "dup@inspro.test", "role": "broker_viewer"})
    assert res.status_code == 409


def test_broker_cannot_grant_system_admin(broker: TestClient) -> None:
    res = broker.post("/api/v1/admin/invitations",
                      json={"email": "evil@inspro.test", "role": "system_admin"})
    assert res.status_code == 403


def test_invite_to_other_firm_client_rejected(broker: TestClient) -> None:
    res = broker.post(
        "/api/v1/admin/invitations",
        json={"email": "x@inspro.test", "role": "client_hr", "client_ids": [CLIENT_F2_ID]},
    )
    assert res.status_code == 404


def test_revoke_invitation_disables_invited_user(broker: TestClient) -> None:
    inv = broker.post("/api/v1/admin/invitations",
                      json={"email": "torevoke@inspro.test", "role": "broker_viewer"}).json()
    res = broker.post(f"/api/v1/admin/invitations/{inv['id']}/revoke")
    assert res.status_code == 200
    with SessionLocal() as s:
        u = s.query(User).filter(User.email == "torevoke@inspro.test").one()
        assert u.status == "disabled"


def test_patch_user_role_and_status(broker: TestClient) -> None:
    inv = broker.post("/api/v1/admin/invitations",
                      json={"email": "patchme@inspro.test", "role": "broker_viewer"}).json()
    res = broker.patch(f"/api/v1/admin/users/{inv['user_id']}",
                       json={"role": "broker_admin", "display_name": "Patched"})
    assert res.status_code == 200
    assert res.json()["role"] == "broker_admin"
    assert res.json()["display_name"] == "Patched"


def test_patch_other_firm_user_404(broker: TestClient) -> None:
    # A user that belongs to firm 2 (inserted directly to avoid installing a
    # global system_admin override that would also affect the broker client).
    with SessionLocal() as s:
        f2_user = User(
            email="f2user@inspro.test", display_name=None,
            broker_firm_id=FIRM2_ID, role="broker_viewer", status="active",
        )
        s.add(f2_user)
        s.commit()
        f2_user_id = f2_user.id
    res = broker.patch(f"/api/v1/admin/users/{f2_user_id}", json={"status": "disabled"})
    assert res.status_code == 404


# ── Firm resolution for system_admin ─────────────────────────────────────────
# The admin UI sends no broker_firm_id, so a system_admin used to get
# 400 "must specify broker_firm_id" on every Create company / Invite — the
# console was unusable for the very role that bootstraps the platform.
def test_sysadmin_invite_ambiguous_when_several_firms(sysadmin: TestClient) -> None:
    """With more than one firm there is no unambiguous target, so still refuse."""
    res = sysadmin.post("/api/v1/admin/invitations",
                        json={"email": "ambiguous@inspro.test", "role": "broker_viewer"})
    assert res.status_code == 400
    assert "broker_firm_id" in res.json()["detail"]


def test_resolve_target_firm_sole_firm_fallback() -> None:
    """Zero / one / many firms, against a DB of exactly known contents.

    Uses its own in-memory engine: the module fixture seeds several firms, which
    is precisely the case that must NOT resolve.
    """
    from fastapi import HTTPException
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    from app.api.v1.admin import _resolve_target_firm

    eng = create_engine("sqlite://")
    Base.metadata.create_all(bind=eng)
    with sessionmaker(bind=eng)() as s:
        # No firm at all: "specify a firm" is unactionable advice here.
        with pytest.raises(HTTPException) as exc:
            _resolve_target_firm(_system_admin(), None, s)
        assert "create one first" in str(exc.value.detail)

        s.add(BrokerFirm(id="firm-solo", name="Only Firm"))
        s.commit()
        assert _resolve_target_firm(_system_admin(), None, s) == "firm-solo"
        # An explicit id always wins over the fallback.
        assert _resolve_target_firm(_system_admin(), "firm-xyz", s) == "firm-xyz"

        s.add(BrokerFirm(id="firm-second", name="Second Firm"))
        s.commit()
        with pytest.raises(HTTPException) as exc:
            _resolve_target_firm(_system_admin(), None, s)
        assert "must specify" in str(exc.value.detail)


def test_sysadmin_list_includes_firmless_system_admins(sysadmin: TestClient) -> None:
    """A platform system_admin has no broker firm, so a purely firm-scoped list
    hid the most privileged accounts on the platform behind "No users yet"."""
    with SessionLocal() as s:
        s.add(User(
            email="platform-owner@inspro.test", display_name=None,
            broker_firm_id=None, role="system_admin", status="active",
        ))
        s.commit()
    emails = {
        u["email"]
        for u in sysadmin.get(f"/api/v1/admin/users?broker_firm_id={FIRM2_ID}").json()
    }
    assert "platform-owner@inspro.test" in emails


def test_broker_admin_does_not_see_firmless_system_admins(broker: TestClient) -> None:
    """Only a system_admin may see accounts outside their own firm.

    Creates its own subject rather than leaning on the preceding test: asserting
    an email is ABSENT passes vacuously if nothing ever inserted it, so run in
    isolation this would have proved nothing.
    """
    email = "hidden-platform-owner@inspro.test"
    with SessionLocal() as s:
        if s.query(User).filter(User.email == email).one_or_none() is None:
            s.add(User(
                email=email, display_name=None, broker_firm_id=None,
                role="system_admin", status="active",
            ))
            s.commit()
    emails = {u["email"] for u in broker.get("/api/v1/admin/users").json()}
    assert email not in emails


# ── Platform-admin guard rails ────────────────────────────────────────────────
# These rows became editable when the user list started showing them, and both
# edits below are one-way doors with no UI path back.
def test_cannot_disable_last_system_admin(sysadmin: TestClient) -> None:
    with SessionLocal() as s:
        # Park the other admins as disabled rather than DELETE-ing them: the
        # guard counts ACTIVE admins, and this module shares one DB across tests,
        # so deleting rows earlier tests created makes the suite order-dependent.
        s.query(User).filter(
            User.role == "system_admin", User.status == "active"
        ).update({"status": "disabled"})
        only = User(email="only-admin@inspro.test", display_name=None,
                    broker_firm_id=None, role="system_admin", status="active")
        s.add(only)
        s.commit()
        only_id = only.id
    res = sysadmin.patch(f"/api/v1/admin/users/{only_id}", json={"status": "disabled"})
    assert res.status_code == 409
    assert "last active system_admin" in res.json()["detail"]


def test_cannot_strand_firmless_admin_by_demoting(sysadmin: TestClient) -> None:
    """Demoting a firm-less admin leaves a row that matches no list query."""
    with SessionLocal() as s:
        u = User(email="strandable@inspro.test", display_name=None,
                 broker_firm_id=None, role="system_admin", status="active")
        s.add(u)
        s.commit()
        uid = u.id
    res = sysadmin.patch(f"/api/v1/admin/users/{uid}", json={"role": "broker_viewer"})
    assert res.status_code == 409
    assert "no broker firm" in res.json()["detail"]


def test_admin_change_allowed_when_another_admin_remains(sysadmin: TestClient) -> None:
    """The guard must not block ordinary administration."""
    with SessionLocal() as s:
        keeper = User(email="keeper@inspro.test", display_name=None,
                      broker_firm_id=None, role="system_admin", status="active")
        spare = User(email="spare@inspro.test", display_name=None,
                     broker_firm_id=FIRM2_ID, role="system_admin", status="active")
        s.add_all([keeper, spare])
        s.commit()
        spare_id = spare.id
    res = sysadmin.patch(f"/api/v1/admin/users/{spare_id}", json={"status": "disabled"})
    assert res.status_code == 200, res.text
    assert res.json()["status"] == "disabled"
