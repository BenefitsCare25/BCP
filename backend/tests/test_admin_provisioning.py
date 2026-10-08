"""Provisioning console: firm/client/user/invitation management + authz, and
the platform and firm consoles.

Mock auth gives a demo broker_admin (firm = DEMO_BROKER_FIRM_ID, the platform
owner's firm once seeded), so the broker-admin paths run without overrides.
system_admin and firm_admin paths override get_current_user. The platform host
in tests is "testserver".
"""

from __future__ import annotations

import os
import re
from pathlib import Path

TEST_DB = Path(__file__).parent / "_test_admin_provisioning.db"
os.environ["INSPRO_DATABASE_URL"] = f"sqlite:///{TEST_DB}"

from datetime import date  # noqa: E402

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.core.auth import DEMO_BROKER_FIRM_ID, CurrentUser, Role, get_current_user  # noqa: E402
from app.db.base import Base  # noqa: E402
from app.db.session import SessionLocal, engine  # noqa: E402
from app.main import app  # noqa: E402
from app.models import BrokerFirm, Client, Dependant, Employee, PolicyYear, User  # noqa: E402
from app.models.invitation import Invitation  # noqa: E402
from app.models.policy_year import PolicyYearStatus  # noqa: E402
from scripts.seed_demo import seed  # noqa: E402

FIRM2_ID = "00000000-0000-0000-0000-0000000000a2"
FIRM2_SLUG = "firm-two"
CLIENT_F2_ID = "00000000-0000-0000-0000-0000000000a3"
# A real platform admin row: access grants reference their holder.
PLATFORM_ADMIN_ID = "00000000-0000-0000-0000-0000000000a9"


def _system_admin() -> CurrentUser:
    return CurrentUser(
        user_id="sa-1",
        broker_firm_id=None,
        client_id=None,
        role="system_admin",
    )


@pytest.fixture(scope="module", autouse=True)
def _setup_db():
    if TEST_DB.exists():
        TEST_DB.unlink()
    Base.metadata.create_all(bind=engine)
    seed()
    with SessionLocal() as s:
        s.add(BrokerFirm(id=FIRM2_ID, name="Firm Two", slug=FIRM2_SLUG))
        s.flush()
        s.add(Client(id=CLIENT_F2_ID, name="F2 Client", broker_firm_id=FIRM2_ID))
        s.add(User(id=PLATFORM_ADMIN_ID, email="master.admin@inspro.test",
                   broker_firm_id=None, role="system_admin", status="active"))
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


@pytest.mark.parametrize("role", ["broker_admin", "broker_viewer", "client_admin", "client_hr"])
def test_user_administration_requires_a_firm_owner(role: Role) -> None:
    """Reject list access and every management action, including known targets.

    `broker_admin` no longer manages users: only firm owners (`firm_admin`,
    `system_admin`) do.
    """
    app.dependency_overrides[get_current_user] = _system_admin
    client = TestClient(app)
    try:
        created = client.post(
            "/api/v1/admin/invitations",
            json={
                "email": f"restricted-{role}@inspro.test",
                "role": "broker_viewer",
                "broker_firm_id": DEMO_BROKER_FIRM_ID,
            },
        )
        assert created.status_code == 201, created.text
        invite = created.json()
        app.dependency_overrides[get_current_user] = lambda: CurrentUser(
            user_id="restricted-actor",
            broker_firm_id=DEMO_BROKER_FIRM_ID,
            client_id=None,
            role=role,
        )
        requests = [
            ("GET", "/api/v1/admin/users", None),
            ("GET", "/api/v1/admin/invitations", None),
            (
                "POST",
                "/api/v1/admin/invitations",
                {
                    "email": f"blocked-{role}@inspro.test",
                    "role": "broker_admin",
                },
            ),
            ("PATCH", f"/api/v1/admin/users/{invite['user_id']}", {"display_name": "Changed"}),
            ("PATCH", f"/api/v1/admin/users/{invite['user_id']}", {"role": "broker_admin"}),
            ("PATCH", f"/api/v1/admin/users/{invite['user_id']}", {"status": "disabled"}),
            ("PATCH", f"/api/v1/admin/users/{invite['user_id']}", {"broker_mfa_required": True}),
            ("PATCH", f"/api/v1/admin/users/{invite['user_id']}", {
                "external_id": "11111111-1111-4111-8111-111111111111",
            }),
            ("POST", f"/api/v1/admin/invitations/{invite['id']}/revoke", None),
        ]
        for method, path, payload in requests:
            response = client.request(method, path, **({"json": payload} if payload else {}))
            assert response.status_code == 403, (role, method, path, response.text)
        with SessionLocal() as db:
            target = db.get(User, invite["user_id"])
            assert target is not None
            assert (target.display_name, target.role, target.status) == (
                None,
                "broker_viewer",
                "invited",
            )
            assert target.broker_mfa_required is False
            assert (
                db.query(User).filter(User.email == f"blocked-{role}@inspro.test").first() is None
            )
            assert db.get(Invitation, invite["id"]).status == "pending"
    finally:
        app.dependency_overrides.pop(get_current_user, None)


# Destructive POST routes outside the router-level DELETE gate.
_DESTRUCTIVE_POSTS = [
    ("/api/v1/wica/incidents/known-target/documents/known-target/remove", {"revision": 1}),
    ("/api/v1/employees/known-target/coverage/revert", {"target": "default"}),
    ("/api/v1/enrollments/known-target/reset", {}),
    ("/api/v1/claim-doc-types/reset", {"expected_versions": {}}),
    ("/api/v1/bulk-plan-updates/known-target/undo", {}),
]


@pytest.mark.parametrize("role", ["broker_admin", "broker_viewer", "client_admin", "client_hr"])
def test_all_broker_delete_routes_require_a_firm_owner(role: Role) -> None:
    """Cover the registered API surface, including future deletion endpoints."""
    from fastapi.routing import APIRoute

    from app.core.deps import require_write_access

    app.dependency_overrides[get_current_user] = lambda: CurrentUser(
        user_id="delete-actor",
        broker_firm_id=DEMO_BROKER_FIRM_ID,
        client_id=None,
        role=role,
    )
    try:
        client = TestClient(app)
        paths = [
            re.sub(r"\{[^}]+\}", "known-target", route.path)
            for route in app.routes
            if isinstance(route, APIRoute)
            and "DELETE" in route.methods
            and any(dep.call == require_write_access for dep in route.dependant.dependencies)
        ]
        assert len(paths) >= 20  # The full broker surface, not one sample route.
        for path in paths:
            response = client.delete(path)
            assert response.status_code == 403, (role, path, response.text)
        for path, payload in _DESTRUCTIVE_POSTS:
            response = client.post(path, json=payload)
            assert response.status_code == 403, (role, path, response.text)
    finally:
        app.dependency_overrides.pop(get_current_user, None)


def test_broker_authenticator_setting_defaults_off_and_is_audited(sysadmin):
    from app.models import AuditLog

    invited = sysadmin.post("/api/v1/admin/invitations", json={
        "email": "auth-policy-review@inspro.test", "role": "broker_viewer",
        "broker_firm_id": DEMO_BROKER_FIRM_ID,
    })
    assert invited.status_code == 201, invited.text
    uid = invited.json()["user_id"]
    response = sysadmin.get("/api/v1/admin/users", params={"broker_firm_id": DEMO_BROKER_FIRM_ID})
    assert response.status_code == 200, response.text
    listed = response.json()
    assert next(u for u in listed if u["id"] == uid)["broker_mfa_required"] is False
    changed = sysadmin.patch("/api/v1/admin/users/" + uid, json={"broker_mfa_required": True})
    assert changed.status_code == 200, changed.text
    with SessionLocal() as db:
        audit = db.query(AuditLog).filter(AuditLog.entity_id == uid,
                                         AuditLog.action == "update").one()
        assert audit.before["broker_mfa_required"] is False
        assert audit.after["broker_mfa_required"] is True


@pytest.mark.parametrize("role", ["broker_admin", "broker_viewer", "client_admin", "client_hr"])
def test_only_a_firm_owner_can_unlink_a_dependant(role: Role) -> None:
    with SessionLocal() as db:
        policy_year = (
            db.query(PolicyYear)
            .join(Client, Client.id == PolicyYear.client_id)
            .filter(Client.broker_firm_id == DEMO_BROKER_FIRM_ID)
            .first()
        )
        assert policy_year is not None
        employee = Employee(
            client_id=policy_year.client_id,
            policy_year_id=policy_year.id,
            staff_id=f"permission-review-{role}",
            employee_name="Permission review employee",
        )
        db.add(employee)
        db.flush()
        employee_id, client_id = employee.id, employee.client_id
        dependant = Dependant(
            client_id=client_id,
            policy_year_id=employee.policy_year_id,
            employee_id=employee_id,
            link_method="staff_id",
            attribute_values={"dependant_name": "Permission review child"},
        )
        db.add(dependant)
        db.commit()
        dependant_id = dependant.id
    app.dependency_overrides[get_current_user] = lambda: CurrentUser(
        user_id="unlink-actor",
        broker_firm_id=DEMO_BROKER_FIRM_ID,
        client_id=client_id,
        role=role,
    )
    try:
        response = TestClient(app).patch(
            f"/api/v1/dependants/{dependant_id}",
            json={"relink": True, "employee_id": None},
        )
        assert response.status_code == 403, response.text
        with SessionLocal() as db:
            assert db.get(Dependant, dependant_id).employee_id == employee_id
    finally:
        app.dependency_overrides.pop(get_current_user, None)


# ── Firms (system_admin only, platform console) ───────────────────────────────
def test_broker_cannot_create_firm(broker: TestClient) -> None:
    res = broker.post("/api/v1/platform/firms", json={"name": "Sneaky"})
    assert res.status_code == 403
    # The admin console no longer creates firms.
    assert broker.post("/api/v1/admin/broker-firms", json={"name": "Sneaky"}).status_code == 405


def test_system_admin_creates_firm(sysadmin: TestClient) -> None:
    res = sysadmin.post("/api/v1/platform/firms", json={"name": "Brand New Firm"})
    assert res.status_code == 201
    assert res.json()["name"] == "Brand New Firm"
    assert res.json()["id"] in {f["id"] for f in sysadmin.get("/api/v1/admin/broker-firms").json()}


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


def test_sysadmin_deletes_empty_client(sysadmin: TestClient) -> None:
    created = sysadmin.post(
        "/api/v1/admin/clients",
        json={"name": "Disposable Co", "broker_firm_id": DEMO_BROKER_FIRM_ID},
    ).json()
    res = sysadmin.delete(f"/api/v1/admin/clients/{created['id']}")
    assert res.status_code == 204
    rows = sysadmin.get(f"/api/v1/admin/clients?broker_firm_id={DEMO_BROKER_FIRM_ID}").json()
    assert created["id"] not in {c["id"] for c in rows}


def test_broker_cannot_delete_other_firm_client(broker: TestClient) -> None:
    res = broker.delete(f"/api/v1/admin/clients/{CLIENT_F2_ID}")
    assert res.status_code == 403


def test_delete_client_blocked_while_it_has_benefit_years(sysadmin: TestClient) -> None:
    created = sysadmin.post(
        "/api/v1/admin/clients",
        json={"name": "Has Years Co", "broker_firm_id": DEMO_BROKER_FIRM_ID},
    ).json()
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
    res = sysadmin.delete(f"/api/v1/admin/clients/{created['id']}")
    assert res.status_code == 409
    rows = sysadmin.get(f"/api/v1/admin/clients?broker_firm_id={DEMO_BROKER_FIRM_ID}").json()
    assert created["id"] in {c["id"] for c in rows}  # still present


def test_audit_rows_name_the_company_of_the_record(broker: TestClient) -> None:
    """An audit row names the company its RECORD belongs to, not whichever one
    the actor had selected (the demo company here): a company's own creation,
    edits and removal file under that company, while firm users and
    invitations belong to no company at all. Firms are platform records, in
    the platform trail."""
    from app.core.auth import DEMO_CLIENT_ID
    from app.models import AuditLog
    from app.models.platform import PlatformAuditLog

    created = broker.post("/api/v1/admin/clients", json={"name": "Stamped Co"})
    assert created.status_code == 201, created.text
    company = created.json()["id"]
    assert broker.patch(
        f"/api/v1/admin/clients/{company}", json={"name": "Stamped Co Renamed"}
    ).status_code == 200
    app.dependency_overrides[get_current_user] = lambda: CurrentUser(
        user_id="sa-stamp", broker_firm_id=None, client_id=DEMO_CLIENT_ID, role="system_admin",
    )
    try:
        admin = TestClient(app)
        firm = admin.post("/api/v1/platform/firms", json={"name": "Stamp Firm"}).json()
        invite = admin.post("/api/v1/admin/invitations", json={
            "email": "stamped@inspro.test", "role": "broker_viewer",
            "broker_firm_id": DEMO_BROKER_FIRM_ID,
        }).json()
        assert admin.patch(
            f"/api/v1/admin/users/{invite['user_id']}", json={"display_name": "Stamped"}
        ).status_code == 200
        assert admin.post(
            f"/api/v1/admin/invitations/{invite['id']}/revoke"
        ).status_code == 200
        assert admin.delete(f"/api/v1/admin/clients/{company}").status_code == 204
    finally:
        app.dependency_overrides.pop(get_current_user, None)
    with SessionLocal() as s:
        rows = s.query(AuditLog).filter(AuditLog.entity_id.in_(
            [company, firm["id"], invite["id"], invite["user_id"]]
        )).all()
        stamps = sorted((r.entity_type, r.action, r.client_id) for r in rows)
        platform_flags = {r.cross_tenant_access for r in rows if r.client_id is None}
        firm_trail = s.query(PlatformAuditLog).filter(
            PlatformAuditLog.entity_id == firm["id"]
        ).one()
    assert (firm_trail.action, firm_trail.actor_user_id, firm_trail.client_id) == (
        "firm.create", "sa-stamp", None,
    )
    assert stamps == sorted([
        ("client", "create", company),
        ("client", "update", company),
        ("client", "delete", company),
        ("invitation", "create", None),
        ("user", "update", None),
        ("invitation", "revoke", None),
    ])
    # A platform row is about no company, so it is never a cross-tenant access.
    assert platform_flags == {False}


def test_new_company_requires_hr_two_factor(sysadmin: TestClient) -> None:
    """New companies start with HR two-factor required; nothing else moves off
    its default and existing companies keep their own policy."""
    from app.models import ClientAuthPolicy

    with SessionLocal() as s:
        existing = s.get(ClientAuthPolicy, CLIENT_F2_ID) or ClientAuthPolicy(
            client_id=CLIENT_F2_ID
        )
        s.add(existing)
        s.commit()
    created = sysadmin.post(
        "/api/v1/admin/clients",
        json={"name": "MFA Default Co", "broker_firm_id": DEMO_BROKER_FIRM_ID},
    )
    assert created.status_code == 201, created.text
    with SessionLocal() as s:
        policy = s.get(ClientAuthPolicy, created.json()["id"])
        assert policy is not None
        assert (policy.mfa_hr_enabled, policy.mfa_hr_required) == (True, True)
        assert (policy.mfa_portal_enabled, policy.mfa_portal_required) == (False, False)
        assert (policy.hr_login_source, policy.portal_login_source) == ("email", "email")
        assert (policy.session_idle_minutes, policy.session_absolute_hours) == (30, 12)
        assert policy.breach_check_enabled is True
        untouched = s.get(ClientAuthPolicy, CLIENT_F2_ID)
        assert (untouched.mfa_hr_enabled, untouched.mfa_hr_required) == (False, False)


def test_surface_switches_end_that_surfaces_sessions(broker: TestClient) -> None:
    """Switching the employee portal or HR portal off ends every live session
    on that surface for that company only, and is audited."""
    from app.core.sessions import issue_session
    from app.models import AuditLog, AuthSession

    company = broker.post("/api/v1/admin/clients", json={"name": "Kill Switch Co"}).json()
    other = broker.post("/api/v1/admin/clients", json={"name": "Bystander Co"}).json()
    assert (company["portal_enabled"], company["hr_enabled"]) == (True, True)

    def session(subject_type: str, client_id: str | None) -> str:
        with SessionLocal() as s:
            sid = issue_session(s, subject_type=subject_type, subject_id=f"{subject_type}-x",
                                client_id=client_id, broker_firm_id=DEMO_BROKER_FIRM_ID,
                                absolute_hours=12).session_id
            s.commit()
        return sid

    member = [session("member", company["id"]), session("member", company["id"])]
    hr = session("user", company["id"])
    bystander = session("member", other["id"])
    broker_sid = session("broker", None)

    def revoked(sid: str) -> bool:
        with SessionLocal() as s:
            return s.get(AuthSession, sid).revoked_at is not None

    off = broker.patch(f"/api/v1/admin/clients/{company['id']}", json={"portal_enabled": False})
    assert off.status_code == 200, off.text
    assert (off.json()["portal_enabled"], off.json()["hr_enabled"]) == (False, True)
    assert all(revoked(sid) for sid in member)
    assert not any(revoked(sid) for sid in (hr, bystander, broker_sid))
    with SessionLocal() as s:
        audit = s.query(AuditLog).filter(
            AuditLog.entity_id == company["id"], AuditLog.action == "revoke_sessions"
        ).one()
        assert audit.after == {"switch": "portal_enabled", "sessions_revoked": 2}
        change = s.query(AuditLog).filter(
            AuditLog.entity_id == company["id"], AuditLog.action == "update"
        ).one()
        assert (change.before["portal_enabled"], change.after["portal_enabled"]) == (True, False)

    hr_off = broker.patch(f"/api/v1/admin/clients/{company['id']}", json={"hr_enabled": False})
    assert hr_off.json()["hr_enabled"] is False
    assert revoked(hr) and not revoked(bystander) and not revoked(broker_sid)
    # A partial patch leaves the other switch alone, and the list carries both.
    listed = next(
        c for c in broker.get("/api/v1/admin/clients").json() if c["id"] == company["id"]
    )
    assert (listed["portal_enabled"], listed["hr_enabled"]) == (False, False)
    on = broker.patch(f"/api/v1/admin/clients/{company['id']}", json={"portal_enabled": True})
    assert (on.json()["portal_enabled"], on.json()["hr_enabled"]) == (True, False)


@pytest.mark.parametrize("role", ["broker_viewer", "client_admin", "client_hr"])
def test_surface_switches_need_company_edit_rights(role: Role) -> None:
    app.dependency_overrides[get_current_user] = lambda: CurrentUser(
        user_id="switch-actor", broker_firm_id=DEMO_BROKER_FIRM_ID, client_id=None, role=role,
    )
    try:
        with SessionLocal() as s:
            target = s.query(Client).filter(Client.broker_firm_id == DEMO_BROKER_FIRM_ID).first()
            target_id = target.id
        res = TestClient(app).patch(
            f"/api/v1/admin/clients/{target_id}", json={"hr_enabled": False}
        )
        assert res.status_code == 403, res.text
        with SessionLocal() as s:
            assert s.get(Client, target_id).hr_enabled is True
    finally:
        app.dependency_overrides.pop(get_current_user, None)


def test_broker_cannot_switch_off_another_firms_company(broker: TestClient) -> None:
    res = broker.patch(f"/api/v1/admin/clients/{CLIENT_F2_ID}", json={"portal_enabled": False})
    assert res.status_code == 404
    with SessionLocal() as s:
        assert s.get(Client, CLIENT_F2_ID).portal_enabled is True


# ── Invitations / users ───────────────────────────────────────────────────────
def test_invite_provisions_user_and_invitation(sysadmin: TestClient) -> None:
    res = sysadmin.post(
        "/api/v1/admin/invitations",
        json={
            "broker_firm_id": DEMO_BROKER_FIRM_ID,
            "email": "New.Hire@Inspro.test",
            "role": "broker_viewer",
        },
    )
    assert res.status_code == 201
    body = res.json()
    assert body["email"] == "new.hire@inspro.test"  # normalized
    users = sysadmin.get(f"/api/v1/admin/users?broker_firm_id={DEMO_BROKER_FIRM_ID}").json()
    invited = next(u for u in users if u["email"] == "new.hire@inspro.test")
    assert invited["status"] == "invited"
    assert invited["role"] == "broker_viewer"
    pending = sysadmin.get(f"/api/v1/admin/invitations?broker_firm_id={DEMO_BROKER_FIRM_ID}").json()
    assert any(i["email"] == "new.hire@inspro.test" for i in pending)


def test_invite_client_role_grants_client_access(sysadmin: TestClient) -> None:
    # Use a client in the demo firm.
    clients = sysadmin.get(f"/api/v1/admin/clients?broker_firm_id={DEMO_BROKER_FIRM_ID}").json()
    target_client = clients[0]["id"]
    res = sysadmin.post(
        "/api/v1/admin/invitations",
        json={
            "broker_firm_id": DEMO_BROKER_FIRM_ID,
            "email": "hr2@inspro.test",
            "role": "client_hr",
            "client_ids": [target_client],
        },
    )
    assert res.status_code == 201
    users = sysadmin.get(f"/api/v1/admin/users?broker_firm_id={DEMO_BROKER_FIRM_ID}").json()
    u = next(u for u in users if u["email"] == "hr2@inspro.test")
    assert u["client_ids"] == [target_client]


def test_invite_can_carry_a_name_and_it_survives_to_the_user_row(
    sysadmin: TestClient,
) -> None:
    """Without this the users list shows the email as the name AND as the
    subtitle — the same string twice — until someone edits it by hand."""
    res = sysadmin.post(
        "/api/v1/admin/invitations",
        json={
            "broker_firm_id": DEMO_BROKER_FIRM_ID,
            "email": "named@inspro.test",
            "role": "broker_viewer",
            "display_name": "  Chee Leong Ong  ",
        },
    )
    assert res.status_code == 201, res.text
    users = sysadmin.get(f"/api/v1/admin/users?broker_firm_id={DEMO_BROKER_FIRM_ID}").json()
    u = next(u for u in users if u["email"] == "named@inspro.test")
    assert u["display_name"] == "Chee Leong Ong"  # trimmed


def test_a_users_name_can_be_set_and_cleared_after_the_fact(
    sysadmin: TestClient,
) -> None:
    """Most rows predate the name field, so editing is the path that matters.
    An emptied box CLEARS the name; omitting the key leaves it alone."""
    sysadmin.post(
        "/api/v1/admin/invitations",
        json={
            "broker_firm_id": DEMO_BROKER_FIRM_ID,
            "email": "rename@inspro.test",
            "role": "broker_viewer",
        },
    )
    users = sysadmin.get(f"/api/v1/admin/users?broker_firm_id={DEMO_BROKER_FIRM_ID}").json()
    uid = next(u["id"] for u in users if u["email"] == "rename@inspro.test")

    named = sysadmin.patch(f"/api/v1/admin/users/{uid}", json={"display_name": "Fiona Lee"})
    assert named.status_code == 200, named.text
    assert named.json()["display_name"] == "Fiona Lee"

    # A patch that doesn't mention the name must not wipe it.
    kept = sysadmin.patch(f"/api/v1/admin/users/{uid}", json={"role": "broker_admin"})
    assert kept.json()["display_name"] == "Fiona Lee"

    cleared = sysadmin.patch(f"/api/v1/admin/users/{uid}", json={"display_name": ""})
    assert cleared.json()["display_name"] is None


def test_invite_duplicate_email_conflicts(sysadmin: TestClient) -> None:
    sysadmin.post(
        "/api/v1/admin/invitations",
        json={
            "broker_firm_id": DEMO_BROKER_FIRM_ID,
            "email": "dup@inspro.test",
            "role": "broker_viewer",
        },
    )
    res = sysadmin.post(
        "/api/v1/admin/invitations",
        json={
            "broker_firm_id": DEMO_BROKER_FIRM_ID,
            "email": "dup@inspro.test",
            "role": "broker_viewer",
        },
    )
    assert res.status_code == 409


def test_broker_cannot_grant_system_admin(broker: TestClient) -> None:
    res = broker.post(
        "/api/v1/admin/invitations", json={"email": "evil@inspro.test", "role": "system_admin"}
    )
    assert res.status_code == 403


def test_invite_to_other_firm_client_rejected(sysadmin: TestClient) -> None:
    res = sysadmin.post(
        "/api/v1/admin/invitations",
        json={
            "broker_firm_id": DEMO_BROKER_FIRM_ID,
            "email": "x@inspro.test",
            "role": "client_hr",
            "client_ids": [CLIENT_F2_ID],
        },
    )
    assert res.status_code == 404


def test_revoke_invitation_disables_invited_user(sysadmin: TestClient) -> None:
    inv = sysadmin.post(
        "/api/v1/admin/invitations",
        json={
            "broker_firm_id": DEMO_BROKER_FIRM_ID,
            "email": "torevoke@inspro.test",
            "role": "broker_viewer",
        },
    ).json()
    res = sysadmin.post(f"/api/v1/admin/invitations/{inv['id']}/revoke")
    assert res.status_code == 200
    with SessionLocal() as s:
        u = s.query(User).filter(User.email == "torevoke@inspro.test").one()
        assert u.status == "disabled"


def test_patch_user_role_and_status(sysadmin: TestClient) -> None:
    inv = sysadmin.post(
        "/api/v1/admin/invitations",
        json={
            "broker_firm_id": DEMO_BROKER_FIRM_ID,
            "email": "patchme@inspro.test",
            "role": "broker_viewer",
        },
    ).json()
    res = sysadmin.patch(
        f"/api/v1/admin/users/{inv['user_id']}",
        json={"role": "broker_admin", "display_name": "Patched"},
    )
    assert res.status_code == 200
    assert res.json()["role"] == "broker_admin"
    assert res.json()["display_name"] == "Patched"


def test_broker_admin_cannot_patch_users(broker: TestClient) -> None:
    # A user that belongs to firm 2 (inserted directly to avoid installing a
    # global system_admin override that would also affect the broker client).
    with SessionLocal() as s:
        f2_user = User(
            email="f2user@inspro.test",
            display_name=None,
            broker_firm_id=FIRM2_ID,
            role="broker_viewer",
            status="active",
        )
        s.add(f2_user)
        s.commit()
        f2_user_id = f2_user.id
    res = broker.patch(f"/api/v1/admin/users/{f2_user_id}", json={"status": "disabled"})
    assert res.status_code == 403


# ── Firm resolution for system_admin ─────────────────────────────────────────
# The admin UI sends no broker_firm_id, so a system_admin used to get
# 400 "must specify broker_firm_id" on every Create company / Invite — the
# console was unusable for the very role that bootstraps the platform.
def test_sysadmin_invite_ambiguous_when_several_firms(sysadmin: TestClient) -> None:
    """With more than one firm there is no unambiguous target, so still refuse."""
    res = sysadmin.post(
        "/api/v1/admin/invitations",
        json={"email": "ambiguous@inspro.test", "role": "broker_viewer"},
    )
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
        s.add(
            User(
                email="platform-owner@inspro.test",
                display_name=None,
                broker_firm_id=None,
                role="system_admin",
                status="active",
            )
        )
        s.commit()
    emails = {
        u["email"] for u in sysadmin.get(f"/api/v1/admin/users?broker_firm_id={FIRM2_ID}").json()
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
            s.add(
                User(
                    email=email,
                    display_name=None,
                    broker_firm_id=None,
                    role="system_admin",
                    status="active",
                )
            )
            s.commit()
    response = broker.get("/api/v1/admin/users")
    assert response.status_code == 403
    assert email not in response.text


# ── Platform-admin guard rails ────────────────────────────────────────────────
# These rows became editable when the user list started showing them, and both
# edits below are one-way doors with no UI path back.
def test_cannot_disable_last_system_admin(sysadmin: TestClient) -> None:
    with SessionLocal() as s:
        # Park the other admins as disabled rather than DELETE-ing them: the
        # guard counts ACTIVE admins, and this module shares one DB across tests,
        # so deleting rows earlier tests created makes the suite order-dependent.
        s.query(User).filter(User.role == "system_admin", User.status == "active").update(
            {"status": "disabled"}
        )
        only = User(
            email="only-admin@inspro.test",
            display_name=None,
            broker_firm_id=None,
            role="system_admin",
            status="active",
        )
        s.add(only)
        s.commit()
        only_id = only.id
    res = sysadmin.patch(f"/api/v1/admin/users/{only_id}", json={"status": "disabled"})
    assert res.status_code == 409
    assert "last active system_admin" in res.json()["detail"]


def test_demoting_a_platform_admin_requires_the_firm_it_joins(sysadmin: TestClient) -> None:
    """A firm-less row with a firm role would match no list query, so demotion
    must name an existing firm — which then becomes the account's firm."""
    with SessionLocal() as s:
        u = User(
            email="strandable@inspro.test",
            display_name=None,
            broker_firm_id=None,
            role="system_admin",
            status="active",
        )
        # Another active admin, so the last-admin guard is not what answers.
        s.add_all([u, User(email="demote-keeper@inspro.test", broker_firm_id=None,
                           role="system_admin", status="active")])
        s.commit()
        uid = u.id
    path = f"/api/v1/admin/users/{uid}"
    missing = sysadmin.patch(path, json={"role": "broker_viewer"})
    assert missing.status_code == 422
    assert "broker firm" in missing.json()["detail"]
    unknown = sysadmin.patch(
        path, json={"role": "broker_viewer", "broker_firm_id": "no-such-firm"}
    )
    assert unknown.status_code == 404
    with SessionLocal() as s:
        assert (s.get(User, uid).role, s.get(User, uid).broker_firm_id) == ("system_admin", None)

    demoted = sysadmin.patch(path, json={"role": "broker_viewer", "broker_firm_id": FIRM2_ID})
    assert demoted.status_code == 200, demoted.text
    assert (demoted.json()["role"], demoted.json()["broker_firm_id"]) == ("broker_viewer", FIRM2_ID)
    listed = sysadmin.get(f"/api/v1/admin/users?broker_firm_id={FIRM2_ID}").json()
    assert uid in {row["id"] for row in listed}
    # A firm is only chosen when leaving the platform-admin role.
    moved = sysadmin.patch(path, json={"broker_firm_id": DEMO_BROKER_FIRM_ID})
    assert moved.status_code == 422


def test_last_platform_admin_cannot_be_demoted(sysadmin: TestClient) -> None:
    with SessionLocal() as s:
        s.query(User).filter(User.role == "system_admin", User.status == "active").update(
            {"status": "disabled"}
        )
        last = User(email="last-demote@inspro.test", broker_firm_id=None,
                    role="system_admin", status="active")
        s.add(last)
        s.commit()
        last_id = last.id
    res = sysadmin.patch(
        f"/api/v1/admin/users/{last_id}",
        json={"role": "broker_admin", "broker_firm_id": DEMO_BROKER_FIRM_ID},
    )
    assert res.status_code == 409
    assert "last active system_admin" in res.json()["detail"]


def test_promoting_to_system_admin_detaches_firm_and_company_grants(
    sysadmin: TestClient,
) -> None:
    from app.core.sessions import issue_session
    from app.models import AuthSession, UserClientAccess

    target_client = sysadmin.get(
        f"/api/v1/admin/clients?broker_firm_id={DEMO_BROKER_FIRM_ID}"
    ).json()[0]["id"]
    invited = sysadmin.post("/api/v1/admin/invitations", json={
        "broker_firm_id": DEMO_BROKER_FIRM_ID, "email": "promote-me@inspro.test",
        "role": "client_hr", "client_ids": [target_client],
    })
    assert invited.status_code == 201, invited.text
    uid = invited.json()["user_id"]
    with SessionLocal() as s:
        hr_session = issue_session(s, subject_type="user", subject_id=uid,
                                   client_id=target_client, broker_firm_id=DEMO_BROKER_FIRM_ID,
                                   absolute_hours=12).session_id
        s.commit()

    res = sysadmin.patch(f"/api/v1/admin/users/{uid}", json={"role": "system_admin"})
    assert res.status_code == 200, res.text
    assert res.json()["broker_firm_id"] is None
    assert res.json()["client_ids"] == []
    with SessionLocal() as s:
        assert s.get(User, uid).broker_firm_id is None
        assert s.query(UserClientAccess).filter(UserClientAccess.user_id == uid).count() == 0
        assert s.get(AuthSession, hr_session).revoked_at is not None


def test_invited_system_admin_belongs_to_no_firm(sysadmin: TestClient) -> None:
    """The invitation row keeps the issuing firm (its column is required); the
    account itself is firm-less like every platform admin."""
    res = sysadmin.post("/api/v1/admin/invitations", json={
        "broker_firm_id": DEMO_BROKER_FIRM_ID, "email": "new-platform-admin@inspro.test",
        "role": "system_admin",
    })
    assert res.status_code == 201, res.text
    with SessionLocal() as s:
        created = s.get(User, res.json()["user_id"])
        assert (created.role, created.status, created.broker_firm_id) == (
            "system_admin", "invited", None,
        )
        assert s.get(Invitation, res.json()["id"]).broker_firm_id == DEMO_BROKER_FIRM_ID
    listed = sysadmin.get(f"/api/v1/admin/users?broker_firm_id={FIRM2_ID}").json()
    assert "new-platform-admin@inspro.test" in {row["email"] for row in listed}


def test_admin_change_allowed_when_another_admin_remains(sysadmin: TestClient) -> None:
    """The guard must not block ordinary administration."""
    with SessionLocal() as s:
        keeper = User(
            email="keeper@inspro.test",
            display_name=None,
            broker_firm_id=None,
            role="system_admin",
            status="active",
        )
        spare = User(
            email="spare@inspro.test",
            display_name=None,
            broker_firm_id=FIRM2_ID,
            role="system_admin",
            status="active",
        )
        s.add_all([keeper, spare])
        s.commit()
        spare_id = spare.id
    res = sysadmin.patch(f"/api/v1/admin/users/{spare_id}", json={"status": "disabled"})
    assert res.status_code == 200, res.text
    assert res.json()["status"] == "disabled"


# ── Firm admins: their own firm's users and destructive actions ──────────────
def _as(role: str, *, firm: str | None = DEMO_BROKER_FIRM_ID, user_id: str = "actor",
        client_id: str | None = None) -> TestClient:
    app.dependency_overrides[get_current_user] = lambda: CurrentUser(
        user_id=user_id, broker_firm_id=firm, client_id=client_id, role=role,  # type: ignore[arg-type]
    )
    return TestClient(app)


@pytest.fixture
def firm_admin() -> TestClient:
    try:
        yield _as("firm_admin", user_id="firm-admin-actor")
    finally:
        app.dependency_overrides.pop(get_current_user, None)


@pytest.fixture
def master() -> TestClient:
    """The platform admin, as a real user row (grants reference it)."""
    try:
        yield _as("system_admin", firm=None, user_id=PLATFORM_ADMIN_ID)
    finally:
        app.dependency_overrides.pop(get_current_user, None)


def _grant(scope: str, firm_id: str = FIRM2_ID) -> str:
    from datetime import UTC, datetime, timedelta

    from app.models.platform import PlatformAccessGrant

    with SessionLocal() as s:
        grant = PlatformAccessGrant(
            user_id=PLATFORM_ADMIN_ID, broker_firm_id=firm_id, scope=scope,
            reason="Renewal migration support for the broker",
            expires_at=datetime.now(UTC) + timedelta(hours=1),
        )
        s.add(grant)
        s.commit()
        return grant.id


def _end_grants() -> None:
    from app.models.platform import PlatformAccessGrant

    with SessionLocal() as s:
        s.query(PlatformAccessGrant).delete()
        s.commit()


def test_firm_admin_manages_its_own_firms_users(firm_admin: TestClient) -> None:
    invited = firm_admin.post("/api/v1/admin/invitations", json={
        "email": "fa-invitee@inspro.test", "role": "broker_viewer",
    })
    assert invited.status_code == 201, invited.text
    invite = invited.json()
    assert invite["broker_firm_id"] == DEMO_BROKER_FIRM_ID
    peer = firm_admin.post("/api/v1/admin/invitations", json={
        "email": "fa-peer@inspro.test", "role": "firm_admin",
    })
    assert peer.status_code == 201, peer.text
    assert firm_admin.post("/api/v1/admin/invitations", json={
        "email": "fa-platform@inspro.test", "role": "system_admin",
    }).status_code == 403

    listed = firm_admin.get("/api/v1/admin/users")
    assert listed.status_code == 200, listed.text
    assert {u["broker_firm_id"] for u in listed.json()} == {DEMO_BROKER_FIRM_ID}
    assert invite["user_id"] in {u["id"] for u in listed.json()}
    patched = firm_admin.patch(f"/api/v1/admin/users/{invite['user_id']}", json={
        "role": "broker_admin", "display_name": "Promoted", "broker_mfa_required": True,
    })
    assert patched.status_code == 200, patched.text
    assert (patched.json()["role"], patched.json()["display_name"]) == ("broker_admin", "Promoted")
    assert firm_admin.patch(
        f"/api/v1/admin/users/{peer.json()['user_id']}", json={"role": "system_admin"}
    ).status_code == 403
    assert invite["id"] in {i["id"] for i in firm_admin.get("/api/v1/admin/invitations").json()}
    revoked = firm_admin.post(f"/api/v1/admin/invitations/{invite['id']}/revoke")
    assert revoked.status_code == 200, revoked.text
    with SessionLocal() as s:
        assert s.get(User, invite["user_id"]).status == "disabled"
        assert s.get(User, peer.json()["user_id"]).role == "firm_admin"


def test_firm_admin_is_refused_on_another_firm(firm_admin: TestClient) -> None:
    """Another firm's accounts and invitations are a 404, like a missing one —
    so are platform admins and platform-admin invitations issued from it."""
    with SessionLocal() as s:
        other = User(email="f2.staff@inspro.test", broker_firm_id=FIRM2_ID,
                     role="broker_viewer", status="active")
        f2_invite = Invitation(email="f2.invitee@inspro.test", broker_firm_id=FIRM2_ID,
                               role="broker_viewer", token="f2-invite-token", status="pending")
        platform_invite = Invitation(
            email="next.master@inspro.test", broker_firm_id=DEMO_BROKER_FIRM_ID,
            role="system_admin", token="platform-invite-token", status="pending",
        )
        s.add_all([other, f2_invite, platform_invite])
        s.commit()
        other_id, f2_invite_id, platform_invite_id = other.id, f2_invite.id, platform_invite.id
    for target in (other_id, PLATFORM_ADMIN_ID):
        res = firm_admin.patch(f"/api/v1/admin/users/{target}", json={"status": "disabled"})
        assert res.status_code == 404, res.text
    assert firm_admin.get(f"/api/v1/admin/users?broker_firm_id={FIRM2_ID}").status_code == 404
    assert firm_admin.get(f"/api/v1/admin/invitations?broker_firm_id={FIRM2_ID}").status_code == 404
    assert firm_admin.post("/api/v1/admin/invitations", json={
        "email": "cross@inspro.test", "role": "broker_viewer", "broker_firm_id": FIRM2_ID,
    }).status_code == 404
    for invite_id in (f2_invite_id, platform_invite_id):
        res = firm_admin.post(f"/api/v1/admin/invitations/{invite_id}/revoke")
        assert res.status_code == 404, res.text
    listed = {i["id"] for i in firm_admin.get("/api/v1/admin/invitations").json()}
    assert platform_invite_id not in listed
    with SessionLocal() as s:
        assert s.get(User, other_id).status == "active"
        assert s.get(Invitation, f2_invite_id).status == "pending"
        assert s.get(Invitation, platform_invite_id).status == "pending"


def test_firm_admin_cannot_disable_itself() -> None:
    with SessionLocal() as s:
        me = User(email="self.disable@inspro.test", broker_firm_id=DEMO_BROKER_FIRM_ID,
                  role="firm_admin", status="active")
        s.add(me)
        s.commit()
        me_id = me.id
    try:
        client = _as("firm_admin", user_id=me_id)
        res = client.patch(f"/api/v1/admin/users/{me_id}", json={"status": "disabled"})
        assert res.status_code == 409, res.text
    finally:
        app.dependency_overrides.pop(get_current_user, None)


def test_email_is_unique_per_firm(sysadmin: TestClient) -> None:
    """The same address may hold an account in two firms; a second account in
    one firm is refused without saying anything about other firms."""
    def invite(firm_id: str):
        return sysadmin.post("/api/v1/admin/invitations", json={
            "email": "Shared.Person@inspro.test", "role": "broker_viewer",
            "broker_firm_id": firm_id,
        })

    assert invite(DEMO_BROKER_FIRM_ID).status_code == 201
    assert invite(FIRM2_ID).status_code == 201
    again = invite(DEMO_BROKER_FIRM_ID)
    assert again.status_code == 409
    assert again.json()["detail"] == "This email already has an account in this firm."

    with SessionLocal() as s:
        s.add(User(email="hr.shared@inspro.test", broker_firm_id=FIRM2_ID,
                   role="client_hr", status="active"))
        s.commit()
        company = s.query(Client).filter(Client.broker_firm_id == DEMO_BROKER_FIRM_ID).first().id
    body = {"client_id": company, "email": "hr.shared@inspro.test"}
    assert sysadmin.post("/api/v1/hr-admin/accounts", json=body).status_code == 201
    duplicate = sysadmin.post("/api/v1/hr-admin/accounts", json=body)
    assert duplicate.status_code == 409
    assert duplicate.json()["detail"] == "This email already has an account in this firm."


def _depends_on(dependant, call) -> bool:
    return any(dep.call is call or _depends_on(dep, call) for dep in dependant.dependencies)


def test_firm_admin_passes_every_destructive_gate(firm_admin: TestClient) -> None:
    """The sweep above refuses every other firm role; a firm_admin gets past the
    gate on every broker DELETE and destructive POST (to a 404/409/4xx about
    the target), except the platform-only routes reserved for system_admin."""
    from fastapi.routing import APIRoute

    from app.core.deps import require_system_admin, require_write_access

    routes = [
        route for route in app.routes
        if isinstance(route, APIRoute) and "DELETE" in route.methods
        and _depends_on(route.dependant, require_write_access)
    ]
    platform_only = [r for r in routes if _depends_on(r.dependant, require_system_admin)]
    assert platform_only  # platform AI credentials, platform domains
    for route in routes:
        path = re.sub(r"\{[^}]+\}", "known-target", route.path)
        response = firm_admin.delete(path)
        expected_refusal = route in platform_only
        assert (response.status_code == 403) is expected_refusal, (path, response.text)
    for path, payload in _DESTRUCTIVE_POSTS:
        response = firm_admin.post(path, json=payload)
        assert response.status_code != 403, (path, response.text)


def test_firm_admin_deletes_its_own_firms_companies_only(firm_admin: TestClient) -> None:
    created = firm_admin.post("/api/v1/admin/clients", json={"name": "Firm Admin Disposable"})
    assert created.status_code == 201, created.text
    assert firm_admin.delete(f"/api/v1/admin/clients/{created.json()['id']}").status_code == 204
    assert firm_admin.delete(f"/api/v1/admin/clients/{CLIENT_F2_ID}").status_code == 404


def test_firm_admin_can_unlink_a_dependant() -> None:
    with SessionLocal() as db:
        policy_year = (
            db.query(PolicyYear)
            .join(Client, Client.id == PolicyYear.client_id)
            .filter(Client.broker_firm_id == DEMO_BROKER_FIRM_ID)
            .first()
        )
        employee = Employee(
            client_id=policy_year.client_id, policy_year_id=policy_year.id,
            staff_id="permission-review-firm-admin", employee_name="Firm admin review",
        )
        db.add(employee)
        db.flush()
        dependant = Dependant(
            client_id=employee.client_id, policy_year_id=employee.policy_year_id,
            employee_id=employee.id, link_method="staff_id",
            attribute_values={"dependant_name": "Firm admin review child"},
        )
        db.add(dependant)
        db.commit()
        dependant_id, client_id = dependant.id, employee.client_id
    try:
        res = _as("firm_admin", client_id=client_id).patch(
            f"/api/v1/dependants/{dependant_id}", json={"relink": True, "employee_id": None},
        )
        assert res.status_code == 200, res.text
        with SessionLocal() as db:
            assert db.get(Dependant, dependant_id).employee_id is None
    finally:
        app.dependency_overrides.pop(get_current_user, None)


# ── Master admin reach into firms: standing access vs grants ────────────────
def test_company_administration_needs_access_to_the_firm(master: TestClient) -> None:
    """Users and invitations reach any firm (how a firm's first admin is
    invited); its companies need standing access or a grant."""
    assert master.get(f"/api/v1/admin/clients?broker_firm_id={FIRM2_ID}").status_code == 404
    rename = master.patch(f"/api/v1/admin/clients/{CLIENT_F2_ID}", json={"name": "x"})
    assert rename.status_code == 404
    assert master.post("/api/v1/admin/clients", json={
        "name": "No Grant Co", "broker_firm_id": FIRM2_ID,
    }).status_code == 404
    assert master.get(f"/api/v1/admin/users?broker_firm_id={FIRM2_ID}").status_code == 200
    try:
        _grant("read")
        assert master.get(f"/api/v1/admin/clients?broker_firm_id={FIRM2_ID}").status_code == 200
        refused = master.patch(f"/api/v1/admin/clients/{CLIENT_F2_ID}", json={"name": "x"})
        assert refused.status_code == 403
        assert refused.json()["detail"]["code"] == "platform_access_read_only"
        _grant("write")
        renamed = master.patch(f"/api/v1/admin/clients/{CLIENT_F2_ID}", json={"name": "F2 Client"})
        assert renamed.status_code == 200, renamed.text
    finally:
        _end_grants()


def test_hr_accounts_need_access_to_the_firm(master: TestClient) -> None:
    body = {"client_id": CLIENT_F2_ID, "email": "f2.hr.admin@inspro.test"}
    assert master.post("/api/v1/hr-admin/accounts", json=body).status_code == 404
    assert master.get(f"/api/v1/hr-admin/accounts?client_id={CLIENT_F2_ID}").status_code == 404
    try:
        _grant("read")
        assert master.get(f"/api/v1/hr-admin/accounts?client_id={CLIENT_F2_ID}").status_code == 200
        assert master.post("/api/v1/hr-admin/accounts", json=body).status_code == 403
        _grant("write")
        created = master.post("/api/v1/hr-admin/accounts", json=body)
        assert created.status_code == 201, created.text
    finally:
        _end_grants()


# ── Platform console ──────────────────────────────────────────────────────────
def test_platform_console_is_master_admin_only_on_platform_hosts(master: TestClient) -> None:
    probes = [
        ("GET", "/api/v1/platform/firms", None),
        ("POST", "/api/v1/platform/firms", {"name": "Probe Firm"}),
        ("GET", "/api/v1/platform/access-grants", None),
        ("POST", "/api/v1/platform/access-grants", {
            "broker_firm_id": FIRM2_ID, "reason": "Probe of the console gate", "scope": "read",
            "hours": 1,
        }),
        ("GET", "/api/v1/platform/audit", None),
        ("DELETE", "/api/v1/platform/domains/known-target", None),
    ]
    # A broker's own host: the console does not exist there, even for the master admin.
    off_platform = TestClient(app, base_url=f"http://{FIRM2_SLUG}.localhost")
    for method, path, payload in probes:
        res = off_platform.request(method, path, **({"json": payload} if payload else {}))
        assert res.status_code == 404, (method, path, res.text)
    for role in ("firm_admin", "broker_admin", "broker_viewer"):
        client = _as(role)
        for method, path, payload in probes:
            res = client.request(method, path, **({"json": payload} if payload else {}))
            assert res.status_code == 403, (role, method, path, res.text)
    with SessionLocal() as s:
        assert s.query(BrokerFirm).filter(BrokerFirm.name == "Probe Firm").count() == 0


def test_platform_creates_lists_and_updates_firms(master: TestClient) -> None:
    from app.models.platform import PlatformAuditLog

    created = master.post("/api/v1/platform/firms", json={"name": "  Harbour Brokers  "})
    assert created.status_code == 201, created.text
    firm = created.json()
    assert firm | {"id": None, "created_at": None} == {
        "id": None, "name": "Harbour Brokers", "slug": "harbour-brokers", "status": "active",
        "is_platform_owner": False, "allow_hide_attribution": False, "client_count": 0,
        "domain_count": 0, "created_at": None,
    }
    twin = master.post("/api/v1/platform/firms", json={"name": "Harbour Brokers"})
    assert twin.json()["slug"] == "harbour-brokers-2"
    for slug, code in (("Bad Slug", 422), ("platform", 422), ("demo", 422),
                       ("harbour-brokers", 409)):
        res = master.post("/api/v1/platform/firms", json={"name": "Slug Probe", "slug": slug})
        assert res.status_code == code, (slug, res.text)

    listed = master.get("/api/v1/platform/firms").json()
    assert listed[0]["is_platform_owner"] is True
    assert listed[0]["client_count"] >= 2
    assert firm["id"] in {f["id"] for f in listed}

    patched = master.patch(f"/api/v1/platform/firms/{firm['id']}", json={
        "name": "Harbour Brokers Pte", "slug": "harbour", "allow_hide_attribution": True,
    })
    assert patched.status_code == 200, patched.text
    assert (patched.json()["name"], patched.json()["slug"]) == ("Harbour Brokers Pte", "harbour")
    assert patched.json()["allow_hide_attribution"] is True
    assert master.patch(
        f"/api/v1/platform/firms/{twin.json()['id']}", json={"slug": "harbour"}
    ).status_code == 409
    missing = master.patch("/api/v1/platform/firms/no-such-firm", json={"name": "x"})
    assert missing.status_code == 404
    with SessionLocal() as s:
        actions = [
            (row.action, row.actor_user_id)
            for row in s.query(PlatformAuditLog).filter(PlatformAuditLog.entity_id == firm["id"])
        ]
    assert sorted(actions) == [
        ("firm.create", PLATFORM_ADMIN_ID), ("firm.update", PLATFORM_ADMIN_ID),
    ]


def test_suspending_a_firm_ends_its_sessions(master: TestClient) -> None:
    from app.core.sessions import issue_session
    from app.models import AuthSession

    owner = master.patch(
        f"/api/v1/platform/firms/{DEMO_BROKER_FIRM_ID}", json={"status": "suspended"}
    )
    assert owner.status_code == 409
    firm = master.post("/api/v1/platform/firms", json={"name": "Suspended Brokers"}).json()
    with SessionLocal() as s:
        company = Client(name="Suspended Co", broker_firm_id=firm["id"])
        staff = User(email="staff@suspended.test", broker_firm_id=firm["id"],
                     role="broker_admin", status="active")
        s.add_all([company, staff])
        s.commit()

        def live(subject_type: str, subject_id: str, client_id: str | None,
                 firm_id: str | None) -> str:
            sid = issue_session(s, subject_type=subject_type, subject_id=subject_id,
                                client_id=client_id, broker_firm_id=firm_id,
                                absolute_hours=12).session_id
            s.commit()
            return sid

        ended = [
            live("broker", staff.id, None, firm["id"]),
            live("user", "hr-user-x", company.id, firm["id"]),
            live("member", "member-x", company.id, None),
        ]
        bystander = live("broker", "demo-staff", None, DEMO_BROKER_FIRM_ID)

    res = master.patch(f"/api/v1/platform/firms/{firm['id']}", json={"status": "suspended"})
    assert res.status_code == 200, res.text
    assert res.json()["status"] == "suspended"
    with SessionLocal() as s:
        assert all(s.get(AuthSession, sid).revoked_at is not None for sid in ended)
        assert s.get(AuthSession, bystander).revoked_at is None
    resumed = master.patch(f"/api/v1/platform/firms/{firm['id']}", json={"status": "active"})
    assert resumed.json()["status"] == "active"


def test_domains_are_validated_and_one_is_primary_per_surface(
    master: TestClient, monkeypatch
) -> None:
    from dataclasses import replace

    from app.api.v1 import platform as platform_api
    from app.core.settings import get_settings
    from app.core.tenant_resolution import resolve_firm

    firm = master.post("/api/v1/platform/firms", json={"name": "Domain Brokers"}).json()
    base = f"/api/v1/platform/firms/{firm['id']}/domains"
    settings = get_settings()
    monkeypatch.setattr(platform_api, "get_settings", lambda: replace(
        settings, platform_hosts=(*settings.platform_hosts, "console.inspro-platform.test"),
        firm_fallback_domain="brokers-fallback.test",
    ))
    for hostname in ("localhost", "single", "10.0.0.1", "a..b.example", "-bad.example.com",
                     "bad_label.example.com", "console.inspro-platform.test",
                     f"acme.{settings.base_domain}", settings.base_domain,
                     "harbour.brokers-fallback.test",
                     ("a" * 60 + ".") * 5 + "example"):
        res = master.post(base, json={"hostname": hostname, "surface": "all"})
        assert res.status_code == 422, (hostname, res.text)
    assert master.post(base, json={"hostname": "x.example", "surface": "web"}).status_code == 422

    first = master.post(base, json={
        "hostname": "Benefits.Domain-Brokers.Example.", "surface": "all", "is_primary": True,
    })
    assert first.status_code == 201, first.text
    first_id = first.json()["id"]
    assert first.json() | {"id": None, "created_at": None} == {
        "id": None, "hostname": "benefits.domain-brokers.example", "surface": "all",
        "is_primary": True, "status": "pending", "verified_at": None, "created_at": None,
    }
    assert master.post(base, json={
        "hostname": "benefits.domain-brokers.example", "surface": "staff",
    }).status_code == 409
    staff = master.post(base, json={
        "hostname": "staff.domain-brokers.example", "surface": "staff", "is_primary": True,
    }).json()
    www = master.post(base, json={
        "hostname": "www.domain-brokers.example", "surface": "all", "is_primary": True,
    }).json()

    def primaries() -> set[str]:
        return {d["id"] for d in master.get(base).json() if d["is_primary"]}

    assert primaries() == {staff["id"], www["id"]}
    assert resolve_firm("benefits.domain-brokers.example").firm_id != firm["id"]  # pending
    activated = master.patch(f"/api/v1/platform/domains/{first_id}", json={
        "status": "active", "is_primary": True,
    })
    assert activated.status_code == 200, activated.text
    assert activated.json()["verified_at"] is not None
    assert primaries() == {staff["id"], first_id}
    assert resolve_firm("benefits.domain-brokers.example").firm_id == firm["id"]
    assert master.delete(f"/api/v1/platform/domains/{www['id']}").status_code == 204
    assert master.delete(f"/api/v1/platform/domains/{www['id']}").status_code == 404
    listed = master.get(base).json()
    assert {d["hostname"] for d in listed} == {
        "benefits.domain-brokers.example", "staff.domain-brokers.example",
    }
    assert next(
        f for f in master.get("/api/v1/platform/firms").json() if f["id"] == firm["id"]
    )["domain_count"] == 2


def test_access_grants_are_audited_for_the_platform_and_the_firm(master: TestClient) -> None:
    from app.models import AuditLog
    from app.models.platform import PlatformAuditLog

    url = "/api/v1/platform/access-grants"
    body = {"broker_firm_id": FIRM2_ID, "reason": "Investigate failed renewal import",
            "scope": "read", "hours": 2}
    for change, code in (({"reason": "too short"}, 422), ({"hours": 9}, 422), ({"hours": 0}, 422),
                         ({"scope": "admin"}, 422),
                         ({"broker_firm_id": DEMO_BROKER_FIRM_ID}, 409),
                         ({"broker_firm_id": "no-such-firm"}, 404)):
        res = master.post(url, json=body | change)
        assert res.status_code == code, (change, res.text)
    try:
        created = master.post(url, json=body)
        assert created.status_code == 201, created.text
        grant = created.json()
        assert (grant["user_id"], grant["user_email"], grant["firm_name"], grant["scope"]) == (
            PLATFORM_ADMIN_ID, "master.admin@inspro.test", "Firm Two", "read",
        )
        assert grant["revoked_at"] is None
        def listed(active: bool) -> set[str]:
            return {g["id"] for g in master.get(url, params={"active": active}).json()}

        assert grant["id"] in listed(True)
        assert grant["id"] not in listed(False)

        revoked = master.post(f"{url}/{grant['id']}/revoke")
        assert revoked.status_code == 200, revoked.text
        assert revoked.json()["revoked_at"] is not None
        again = master.post(f"{url}/{grant['id']}/revoke")
        assert again.json()["revoked_at"] == revoked.json()["revoked_at"]
        assert grant["id"] in listed(False)
        assert master.post(f"{url}/no-such-grant/revoke").status_code == 404

        with SessionLocal() as s:
            platform_rows = sorted(
                (r.action, r.entity_type, r.broker_firm_id, r.actor_user_id)
                for r in s.query(PlatformAuditLog).filter(PlatformAuditLog.entity_id == grant["id"])
            )
            firm_rows = sorted(
                (r.action, r.entity_type, r.client_id, r.user_id)
                for r in s.query(AuditLog).filter(AuditLog.entity_id == grant["id"])
            )
        assert platform_rows == [
            ("access_grant.create", "platform_access", FIRM2_ID, PLATFORM_ADMIN_ID),
            ("access_grant.revoke", "platform_access", FIRM2_ID, PLATFORM_ADMIN_ID),
        ]
        assert firm_rows == [
            ("platform_access.create", "platform_access", None, PLATFORM_ADMIN_ID),
            ("platform_access.revoke", "platform_access", None, PLATFORM_ADMIN_ID),
        ]

        trail = master.get("/api/v1/platform/audit", params={"limit": 2}).json()
        assert [row["action"] for row in trail] == ["access_grant.revoke", "access_grant.create"]
        assert trail[0]["actor_email"] == "master.admin@inspro.test"
        older = master.get("/api/v1/platform/audit", params={
            "limit": 50, "before": trail[-1]["occurred_at"],
        }).json()
        assert grant["id"] not in {row["entity_id"] for row in older}
    finally:
        _end_grants()


def test_master_admin_invites_a_new_firms_first_admin(master: TestClient) -> None:
    """The onboarding path: no access grant is needed to invite into a firm,
    and the invitation is on the platform trail."""
    from app.models.platform import PlatformAuditLog

    firm = master.post("/api/v1/platform/firms", json={"name": "Onboarding Brokers"}).json()
    invited = master.post("/api/v1/admin/invitations", json={
        "email": "first.admin@onboarding.test", "role": "firm_admin",
        "broker_firm_id": firm["id"],
    })
    assert invited.status_code == 201, invited.text
    revoked = master.post(f"/api/v1/admin/invitations/{invited.json()['id']}/revoke")
    assert revoked.status_code == 200, revoked.text
    with SessionLocal() as s:
        trail = sorted(
            (row.action, row.broker_firm_id, row.actor_user_id)
            for row in s.query(PlatformAuditLog).filter(
                PlatformAuditLog.entity_id == invited.json()["id"]
            )
        )
    assert trail == [
        ("invitation.create", firm["id"], PLATFORM_ADMIN_ID),
        ("invitation.revoke", firm["id"], PLATFORM_ADMIN_ID),
    ]


# ── Firm console ──────────────────────────────────────────────────────────────
def test_firm_console_serves_the_callers_own_firm(firm_admin: TestClient) -> None:
    profile = firm_admin.get("/api/v1/firm/profile")
    assert profile.status_code == 200, profile.text
    assert profile.json() == {
        "id": DEMO_BROKER_FIRM_ID, "name": "Demo Broker Firm", "slug": "demo-broker-firm",
        "status": "active", "is_platform_owner": True, "allow_hide_attribution": False,
    }
    requested = firm_admin.post("/api/v1/firm/domains", json={
        "hostname": "benefits.demo-broker.example", "surface": "client",
    })
    assert requested.status_code == 201, requested.text
    assert (requested.json()["status"], requested.json()["is_primary"]) == ("pending", False)
    assert firm_admin.post("/api/v1/firm/domains", json={
        "hostname": "testserver.example", "surface": "everything",
    }).status_code == 422
    assert {d["hostname"] for d in firm_admin.get("/api/v1/firm/domains").json()} == {
        "benefits.demo-broker.example",
    }
    assert _as("broker_admin").get("/api/v1/firm/profile").status_code == 403


def test_firm_admins_see_grants_on_their_firm_only() -> None:
    try:
        grant_id = _grant("write")
        own = _as("firm_admin", firm=FIRM2_ID).get("/api/v1/firm/platform-access")
        assert own.status_code == 200, own.text
        assert [g["id"] for g in own.json()] == [grant_id]
        assert own.json()[0]["reason"] == "Renewal migration support for the broker"
        assert _as("firm_admin").get("/api/v1/firm/platform-access").json() == []
        # The master admin sees the selected company's firm; with none chosen a
        # change has no firm to act on.
        assert _as(
            "system_admin", firm=None, user_id=PLATFORM_ADMIN_ID, client_id=CLIENT_F2_ID,
        ).get("/api/v1/firm/platform-access").json()[0]["id"] == grant_id
        unchosen = _as("system_admin", firm=None, user_id=PLATFORM_ADMIN_ID).post(
            "/api/v1/firm/domains", json={"hostname": "x.f2.example", "surface": "all"},
        )
        assert unchosen.status_code == 409
        assert unchosen.json()["detail"]["code"] == "client_selection_required"
    finally:
        app.dependency_overrides.pop(get_current_user, None)
        _end_grants()


# ── Staff invitation links ────────────────────────────────────────────────────
class _CapturedMail:
    def __init__(self, fail: bool = False) -> None:
        self.sent: list[tuple[str, str, str]] = []
        self.fail = fail

    def send_staff_invite(self, email: str, firm_name: str, invite_url: str) -> None:
        if self.fail:
            raise RuntimeError("mail relay down")
        self.sent.append((email, firm_name, invite_url))


def _capture_mail(monkeypatch, *, fail: bool = False) -> _CapturedMail:
    from app.api.v1 import admin as admin_api

    mail = _CapturedMail(fail=fail)
    monkeypatch.setattr(admin_api, "mail_deliverable", lambda: True)
    monkeypatch.setattr(admin_api, "get_mailer", lambda *_: mail)
    return mail


def test_invitation_link_token_is_shown_once_and_stored_hashed(
    sysadmin: TestClient, monkeypatch
) -> None:
    import hashlib

    from app.core.tenant_resolution import public_origin

    mail = _capture_mail(monkeypatch)
    res = sysadmin.post("/api/v1/admin/invitations", json={
        "broker_firm_id": DEMO_BROKER_FIRM_ID, "email": "link.invitee@inspro.test",
        "role": "broker_admin",
    })
    assert res.status_code == 201, res.text
    body = res.json()
    raw = body["invite_token"]
    with SessionLocal() as s:
        origin = public_origin(s, DEMO_BROKER_FIRM_ID, "staff")
        stored = s.get(Invitation, body["id"]).token
    assert "token" not in body
    assert body["invite_url"] == f"{origin}/sign-in#invite={raw}"
    assert stored == hashlib.sha256(raw.encode()).hexdigest() != raw
    assert mail.sent == [("link.invitee@inspro.test", "Demo Broker Firm", body["invite_url"])]
    listed = sysadmin.get(f"/api/v1/admin/invitations?broker_firm_id={DEMO_BROKER_FIRM_ID}")
    assert listed.status_code == 200
    assert raw not in listed.text and stored not in listed.text
    assert all(not any("token" in key for key in item) for item in listed.json())


def test_invitation_links_need_an_address_and_a_staff_role(
    sysadmin: TestClient, monkeypatch
) -> None:
    """No web address yet, or a company (HR) role that never signs in to the
    staff app: no link and no email, but the invitation stands. A mail fault
    never fails the invitation."""
    from app.api.v1 import admin as admin_api
    from app.core.tenant_resolution import FirmOriginUnavailable

    mail = _capture_mail(monkeypatch)

    def unavailable(_db, firm_id, _surface):
        raise FirmOriginUnavailable(firm_id)

    with monkeypatch.context() as patch:
        patch.setattr(admin_api, "public_origin", unavailable)
        no_address = sysadmin.post("/api/v1/admin/invitations", json={
            "broker_firm_id": DEMO_BROKER_FIRM_ID, "email": "no.address@inspro.test",
            "role": "broker_viewer",
        })
    company_role = sysadmin.post("/api/v1/admin/invitations", json={
        "broker_firm_id": DEMO_BROKER_FIRM_ID, "email": "company.role@inspro.test",
        "role": "client_hr",
    })
    for res in (no_address, company_role):
        assert res.status_code == 201, res.text
        assert res.json()["invite_url"] is None and res.json()["invite_token"]
    assert mail.sent == []
    _capture_mail(monkeypatch, fail=True)
    relay_down = sysadmin.post("/api/v1/admin/invitations", json={
        "broker_firm_id": DEMO_BROKER_FIRM_ID, "email": "relay.down@inspro.test",
        "role": "broker_viewer",
    })
    assert relay_down.status_code == 201, relay_down.text
    assert relay_down.json()["invite_url"]


# ── Staff sign-in methods ─────────────────────────────────────────────────────
DIRECTORY = "4b5c6d7e-8f90-4a1b-9c2d-3e4f5a6b7c8d"
OTHER_DIRECTORY = "c0ffee00-1234-4abc-8def-0123456789ab"
PLATFORM_DIRECTORY = "e1d2c3b4-a596-4877-8695-a4b3c2d1e0f9"
APP_CLIENT_ID = "0a1b2c3d-4e5f-4061-8728-394a5b6c7d8e"


def _forget_methods(firm_id: str) -> None:
    from app.models import IdentityProvider

    with SessionLocal() as s:
        s.query(IdentityProvider).filter(IdentityProvider.broker_firm_id == firm_id).delete()
        s.commit()


@pytest.fixture
def entra_app():
    """The platform app registration's settings (any auth mode)."""
    from app.core.settings import clear_settings_cache

    with pytest.MonkeyPatch.context() as patch:
        patch.setenv("INSPRO_ENTRA_CLIENT_ID", APP_CLIENT_ID)
        patch.setenv("INSPRO_ENTRA_TENANT_ID", PLATFORM_DIRECTORY)
        clear_settings_cache()
        yield patch
    clear_settings_cache()


def test_firm_sign_in_methods_are_validated_and_audited(
    firm_admin: TestClient, entra_app
) -> None:
    from app.models import AuditLog

    try:
        current = firm_admin.get("/api/v1/firm/sign-in-methods")
        assert current.status_code == 200, current.text
        # Mock mode: no rows means password sign-in only, even for the owner.
        assert current.json() == {
            "entra": {"enabled": False, "tenant_id": None, "require_platform_mfa": False},
            "local": {"enabled": True}, "admin_consent_url": None,
        }
        entra = {"enabled": True, "tenant_id": DIRECTORY.upper(), "require_platform_mfa": True}
        for body in (
            {"entra": {**entra, "enabled": False}, "local": {"enabled": False}},
            {"entra": {**entra, "tenant_id": None}, "local": {"enabled": True}},
            {"entra": {**entra, "tenant_id": "contoso.onmicrosoft.com"},
             "local": {"enabled": True}},
        ):
            res = firm_admin.put("/api/v1/firm/sign-in-methods", json=body)
            assert res.status_code == 422, (body, res.text)
        saved = firm_admin.put("/api/v1/firm/sign-in-methods",
                               json={"entra": entra, "local": {"enabled": False}})
        assert saved.status_code == 200, saved.text
        assert saved.json() == {
            "entra": {"enabled": True, "tenant_id": DIRECTORY, "require_platform_mfa": True},
            "local": {"enabled": False},
            "admin_consent_url": (
                f"https://login.microsoftonline.com/{DIRECTORY}/adminconsent"
                f"?client_id={APP_CLIENT_ID}"
            ),
        }
        assert firm_admin.get("/api/v1/firm/sign-in-methods").json() == saved.json()
        with SessionLocal() as s:
            audit = s.query(AuditLog).filter(
                AuditLog.entity_type == "sign_in_methods",
                AuditLog.entity_id == DEMO_BROKER_FIRM_ID,
            ).one()
            assert audit.before["local_enabled"] is True
            assert audit.after["tenant_id"] == DIRECTORY
        assert _as("broker_admin").get("/api/v1/firm/sign-in-methods").status_code == 403
    finally:
        _forget_methods(DEMO_BROKER_FIRM_ID)


def test_platform_sets_a_firms_sign_in_methods_and_ends_replaced_sessions(
    master: TestClient, entra_app
) -> None:
    from app.core.sessions import issue_session
    from app.models import AuditLog, AuthSession
    from app.models.platform import PlatformAuditLog

    firm = master.post("/api/v1/platform/firms", json={"name": "Methods Brokers"}).json()
    path = f"/api/v1/platform/firms/{firm['id']}/sign-in-methods"
    # A new firm starts with password sign-in, so its first admin needs no directory.
    assert master.get(path).json() == {
        "entra": {"enabled": False, "tenant_id": None, "require_platform_mfa": False},
        "local": {"enabled": True}, "admin_consent_url": None,
    }
    with SessionLocal() as s:
        password_staff = User(email="pw@methods.test", broker_firm_id=firm["id"],
                              role="broker_admin", status="active")
        microsoft_staff = User(email="ms@methods.test", broker_firm_id=firm["id"],
                               role="broker_admin", status="active",
                               external_id="5a5a5a5a-0000-4000-8000-000000000001",
                               external_tid=DIRECTORY)
        s.add_all([password_staff, microsoft_staff])
        s.flush()

        def live(user: User) -> str:
            return issue_session(s, subject_type="broker", subject_id=user.id, client_id=None,
                                 broker_firm_id=firm["id"], absolute_hours=12).session_id

        password_session, microsoft_session = live(password_staff), live(microsoft_staff)
        s.commit()

    entra = {"enabled": True, "tenant_id": DIRECTORY, "require_platform_mfa": False}
    res = master.put(path, json={"entra": entra, "local": {"enabled": False}})
    assert res.status_code == 200, res.text
    with SessionLocal() as s:
        assert s.get(AuthSession, password_session).revoked_at is not None
        assert s.get(AuthSession, microsoft_session).revoked_at is None
    moved = master.put(path, json={"entra": {**entra, "tenant_id": OTHER_DIRECTORY},
                                   "local": {"enabled": True}})
    assert moved.status_code == 200, moved.text
    with SessionLocal() as s:
        assert s.get(AuthSession, microsoft_session).revoked_at is not None
        trail = s.query(PlatformAuditLog).filter(
            PlatformAuditLog.action == "firm.sign_in_methods",
            PlatformAuditLog.entity_id == firm["id"],
        ).all()
        assert [row.detail["sessions_revoked"] for row in trail] == [1, 1]
        assert s.query(AuditLog).filter(
            AuditLog.entity_type == "sign_in_methods", AuditLog.entity_id == firm["id"],
        ).count() == 2
    off_platform = TestClient(app, base_url=f"http://{FIRM2_SLUG}.localhost")
    assert off_platform.get(path).status_code == 404
    assert master.get("/api/v1/platform/firms/no-such-firm/sign-in-methods").status_code == 404


def test_platform_owner_keeps_microsoft_sign_in_through_the_platform_directory(
    master: TestClient, entra_app
) -> None:
    """The master admin signs in through the owner's Microsoft method, so in
    Entra mode it cannot be switched off or moved to another directory."""
    from app.core.settings import clear_settings_cache

    entra_app.setenv("INSPRO_AUTH_MODE", "entra")
    clear_settings_cache()
    path = f"/api/v1/platform/firms/{DEMO_BROKER_FIRM_ID}/sign-in-methods"
    try:
        implicit = master.get(path).json()
        assert implicit["entra"] == {
            "enabled": True, "tenant_id": PLATFORM_DIRECTORY, "require_platform_mfa": False,
        }
        assert implicit["admin_consent_url"] is None  # nothing saved yet
        entra = {"enabled": True, "tenant_id": PLATFORM_DIRECTORY, "require_platform_mfa": False}
        off = master.put(path, json={"entra": {**entra, "enabled": False},
                                     "local": {"enabled": True}})
        assert off.status_code == 409, off.text
        moved = master.put(path, json={"entra": {**entra, "tenant_id": DIRECTORY},
                                       "local": {"enabled": True}})
        assert moved.status_code == 422, moved.text
        both = master.put(path, json={"entra": entra, "local": {"enabled": True}})
        assert both.status_code == 200, both.text
        assert both.json()["local"] == {"enabled": True}
    finally:
        _forget_methods(DEMO_BROKER_FIRM_ID)
