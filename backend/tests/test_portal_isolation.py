"""Member-level isolation for the employee portal.

The tenant-isolation suite proves client A's BROKER can't touch client B.
This file proves the finer boundary: a MEMBER is pinned to their own Employee
row — never a co-worker's, never another client's — and the portal/broker auth
surfaces don't accept each other's tokens.
"""
from __future__ import annotations

import os
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pytest

TEST_DB = Path(__file__).parent / "_test_portal_isolation.db"
os.environ["INSPRO_DATABASE_URL"] = f"sqlite:///{TEST_DB}"

import jwt as pyjwt  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.core.auth import DEMO_BROKER_FIRM_ID, DEMO_CLIENT_ID  # noqa: E402
from app.core.portal_auth import issue_member_token  # noqa: E402
from app.db.base import Base  # noqa: E402
from app.db.session import SessionLocal, engine  # noqa: E402
from app.main import app  # noqa: E402
from app.models import (  # noqa: E402
    Client,
    Dependant,
    Employee,
    MemberAccount,
    PolicyYear,
)
from app.models.member_account import MEMBER_STATUS_ACTIVE  # noqa: E402
from app.models.policy_year import PolicyYearStatus  # noqa: E402
from scripts.seed_demo import seed  # noqa: E402

# Client A (demo) fixtures
PY_A = "00000000-0000-0000-0000-00000000pi01"
EMP_ALICE = "00000000-0000-0000-0000-00000000pi02"
EMP_CAROL = "00000000-0000-0000-0000-00000000pi03"
DEP_ALICE = "00000000-0000-0000-0000-00000000pi04"
DEP_CAROL = "00000000-0000-0000-0000-00000000pi05"
ACC_ALICE = "00000000-0000-0000-0000-00000000pi06"
ACC_CAROL = "00000000-0000-0000-0000-00000000pi07"

# Client B fixtures (no active policy year)
CLIENT_B_ID = "00000000-0000-0000-0000-00000000pib0"
ACC_BOB_B = "00000000-0000-0000-0000-00000000pib1"

# Unstamped-member fixtures (staff_id fallback path)
ACC_DAVE = "00000000-0000-0000-0000-00000000pid0"
EMP_DAVE = "00000000-0000-0000-0000-00000000pid1"
# Ambiguous staff_id fixtures
ACC_ERIN = "00000000-0000-0000-0000-00000000pie0"
EMP_ERIN_1 = "00000000-0000-0000-0000-00000000pie1"
EMP_ERIN_2 = "00000000-0000-0000-0000-00000000pie2"


def _employee(eid: str, staff_id: str, name: str, account_id: str | None = None) -> Employee:
    return Employee(
        id=eid,
        client_id=DEMO_CLIENT_ID,
        policy_year_id=PY_A,
        staff_id=staff_id,
        employee_name=name,
        member_account_id=account_id,
        attribute_values={},
        derived_attribute_values={},
        source="csv_import",
        status="active",
    )


def _account(aid: str, client_id: str, email: str, staff_id: str) -> MemberAccount:
    return MemberAccount(
        id=aid,
        client_id=client_id,
        email=email,
        staff_id=staff_id,
        status=MEMBER_STATUS_ACTIVE,
    )


@pytest.fixture(scope="module", autouse=True)
def _setup_db():
    if TEST_DB.exists():
        TEST_DB.unlink()
    Base.metadata.create_all(bind=engine)
    seed()
    with SessionLocal() as session:
        session.add(
            PolicyYear(
                id=PY_A,
                # Year 2027, NOT 2026 — a second demo 2026 policy year breaks
                # seed()'s .one_or_none() for every later module on the shared DB.
                client_id=DEMO_CLIENT_ID,
                year=2027,
                start_date=date(2027, 3, 1),
                end_date=date(2028, 2, 28),
                status=PolicyYearStatus.active,
            )
        )
        session.add(Client(id=CLIENT_B_ID, name="Client B", broker_firm_id=DEMO_BROKER_FIRM_ID))
        session.flush()

        session.add(_account(ACC_ALICE, DEMO_CLIENT_ID, "alice@a.test", "S-1"))
        session.add(_account(ACC_CAROL, DEMO_CLIENT_ID, "carol@a.test", "S-2"))
        session.add(_account(ACC_BOB_B, CLIENT_B_ID, "bob@b.test", "S-1"))
        session.add(_account(ACC_DAVE, DEMO_CLIENT_ID, "dave@a.test", "S-3"))
        session.add(_account(ACC_ERIN, DEMO_CLIENT_ID, "erin@a.test", "S-4"))
        session.flush()

        session.add(_employee(EMP_ALICE, "S-1", "Alice", ACC_ALICE))
        session.add(_employee(EMP_CAROL, "S-2", "Carol", ACC_CAROL))
        session.add(_employee(EMP_DAVE, "S-3", "Dave"))  # unstamped
        session.add(_employee(EMP_ERIN_1, "S-4", "Erin One"))  # ambiguous pair
        session.add(_employee(EMP_ERIN_2, "S-4", "Erin Two"))
        session.flush()

        dep_rows = ((DEP_ALICE, EMP_ALICE, "Alice Jr"), (DEP_CAROL, EMP_CAROL, "Carol Jr"))
        for dep_id, emp_id, name in dep_rows:
            session.add(
                Dependant(
                    id=dep_id,
                    client_id=DEMO_CLIENT_ID,
                    policy_year_id=PY_A,
                    employee_id=emp_id,
                    attribute_values={"name": name, "relationship": "child"},
                    link_method="staff_id",
                    status="active",
                )
            )
        session.commit()
    yield
    # Shared engine/DB across modules — remove everything this module created.
    with SessionLocal() as session:
        session.query(MemberAccount).filter(
            MemberAccount.client_id.in_([DEMO_CLIENT_ID, CLIENT_B_ID])
        ).delete()
        py = session.get(PolicyYear, PY_A)
        if py is not None:
            session.delete(py)  # cascades employees + dependants
        client_b = session.get(Client, CLIENT_B_ID)
        if client_b is not None:
            session.delete(client_b)
        session.commit()
    engine.dispose()
    if TEST_DB.exists():
        TEST_DB.unlink()


@pytest.fixture
def anon() -> TestClient:
    return TestClient(app)


def _auth(account_id: str, client_id: str = DEMO_CLIENT_ID) -> dict[str, str]:
    token, _ = issue_member_token(account_id, client_id)
    return {"Authorization": f"Bearer {token}"}


# ── A member only ever sees their own row ────────────────────────────────────


def test_statement_is_own_row_only(anon: TestClient):
    res = anon.get("/api/v1/portal/benefit-statement", headers=_auth(ACC_ALICE))
    assert res.status_code == 200
    assert res.json()["employee"]["id"] == EMP_ALICE

    res = anon.get("/api/v1/portal/benefit-statement", headers=_auth(ACC_CAROL))
    assert res.status_code == 200
    assert res.json()["employee"]["id"] == EMP_CAROL


def test_dependants_scoped_to_own_employee(anon: TestClient):
    res = anon.get("/api/v1/portal/dependants", headers=_auth(ACC_ALICE))
    assert res.status_code == 200
    ids = {d["id"] for d in res.json()}
    assert ids == {DEP_ALICE}


def test_member_of_client_without_active_year_404(anon: TestClient):
    headers = _auth(ACC_BOB_B, CLIENT_B_ID)
    res = anon.get("/api/v1/portal/benefit-statement", headers=headers)
    assert res.status_code == 404
    # /me still renders (no coverage context).
    me = anon.get("/api/v1/portal/me", headers=headers)
    assert me.status_code == 200
    assert me.json()["employee"] is None
    assert me.json()["policy_year"] is None


def test_clinics_scoped_to_own_active_year(anon: TestClient):
    """The clinic locator resolves through the member's own employee row —
    no active year means 404, and it never accepts a client/employee id."""
    res = anon.get("/api/v1/portal/clinics", headers=_auth(ACC_BOB_B, CLIENT_B_ID))
    assert res.status_code == 404
    # Members of the active year get an (empty) result, not an error.
    res = anon.get("/api/v1/portal/clinics", headers=_auth(ACC_ALICE))
    assert res.status_code == 200
    assert res.json()["total"] == 0


def test_token_client_mismatch_401(anon: TestClient):
    """A token minted for another client than the account's is refused —
    same staff_id in two clients must never cross."""
    token, _ = issue_member_token(ACC_BOB_B, DEMO_CLIENT_ID)  # wrong client claim
    res = anon.get("/api/v1/portal/me", headers={"Authorization": f"Bearer {token}"})
    assert res.status_code == 401


# ── Staff-id fallback binding ────────────────────────────────────────────────


def test_unstamped_member_binds_by_staff_id_and_is_stamped(anon: TestClient):
    res = anon.get("/api/v1/portal/benefit-statement", headers=_auth(ACC_DAVE))
    assert res.status_code == 200
    assert res.json()["employee"]["id"] == EMP_DAVE
    with SessionLocal() as session:
        assert session.get(Employee, EMP_DAVE).member_account_id == ACC_DAVE


def test_ambiguous_staff_id_409(anon: TestClient):
    res = anon.get("/api/v1/portal/benefit-statement", headers=_auth(ACC_ERIN))
    assert res.status_code == 409


def test_fallback_never_steals_a_stamped_row(anon: TestClient):
    """An account whose staff_id collides with an already-stamped employee
    must not bind to it (Bob-B's staff 'S-1' equals Alice's, but Alice's row
    is stamped to Alice's account and belongs to client A anyway)."""
    with SessionLocal() as session:
        assert session.get(Employee, EMP_ALICE).member_account_id == ACC_ALICE


# ── Cross-surface token rejection ────────────────────────────────────────────


def test_wrong_secret_token_401(anon: TestClient):
    claims = {
        "sub": ACC_ALICE,
        "client_id": DEMO_CLIENT_ID,
        "typ": "member",
        "exp": int((datetime.now(UTC) + timedelta(hours=1)).timestamp()),
    }
    token = pyjwt.encode(claims, "some-other-secret-entirely-0123456789ab", algorithm="HS256")
    res = anon.get("/api/v1/portal/me", headers={"Authorization": f"Bearer {token}"})
    assert res.status_code == 401


def test_portal_token_rejected_on_broker_surface_in_entra_mode(anon: TestClient, monkeypatch):
    """In entra mode a member token must never authenticate a broker endpoint.
    (In mock mode broker auth ignores tokens entirely, so this is the mode
    where the property matters.)"""
    from app.core import auth as auth_module
    from app.core.entra import EntraAuthError
    from app.core.settings import clear_settings_cache

    monkeypatch.setenv("INSPRO_AUTH_MODE", "entra")
    monkeypatch.setenv("INSPRO_ENTRA_TENANT_ID", "t")
    monkeypatch.setenv("INSPRO_ENTRA_CLIENT_ID", "c")

    def _reject(token: str, settings) -> dict:
        raise EntraAuthError("not an Entra token")

    monkeypatch.setattr(auth_module, "verify_entra_token", _reject)
    clear_settings_cache()
    try:
        token, _ = issue_member_token(ACC_ALICE, DEMO_CLIENT_ID)
        res = anon.get(
            "/api/v1/employees/coverage-summary",
            params={"policy_year_id": PY_A},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert res.status_code == 401
        # And the member token still works on its own surface.
        me = anon.get("/api/v1/portal/me", headers={"Authorization": f"Bearer {token}"})
        assert me.status_code == 200
    finally:
        clear_settings_cache()


def test_broker_style_hs256_user_token_rejected_on_portal(anon: TestClient):
    """Even a token signed with the right secret but without typ=member fails."""
    from app.core.settings import get_settings

    claims = {
        "sub": ACC_ALICE,
        "client_id": DEMO_CLIENT_ID,
        "exp": int((datetime.now(UTC) + timedelta(hours=1)).timestamp()),
    }
    token = pyjwt.encode(claims, get_settings().portal_jwt_secret, algorithm="HS256")
    res = anon.get("/api/v1/portal/me", headers={"Authorization": f"Bearer {token}"})
    assert res.status_code == 401


def test_expired_member_token_401(anon: TestClient):
    from app.core.settings import get_settings

    claims = {
        "sub": ACC_ALICE,
        "client_id": DEMO_CLIENT_ID,
        "typ": "member",
        "exp": int((datetime.now(UTC) - timedelta(minutes=1)).timestamp()),
    }
    token = pyjwt.encode(claims, get_settings().portal_jwt_secret, algorithm="HS256")
    res = anon.get("/api/v1/portal/me", headers={"Authorization": f"Bearer {token}"})
    assert res.status_code == 401


# ── Financials gating (member statement never carries broker figures) ────────


def test_member_statement_strips_financials_and_match_internals():
    from app.schemas.api import (
        BenefitStatementOut,
        CoverageLine,
        PlanFinancials,
        StatementEmployee,
    )
    from app.services import member_statement as ms

    broker_stmt = BenefitStatementOut(
        employee=StatementEmployee(id="e1", staff_id="S-1", employee_name="A"),
        policy_year_id="py1",
        is_matched=True,
        coverage=[
            CoverageLine(
                product_code="GHS",
                plan_code="P1",
                match_method="rule",
                match_confidence=0.97,
                rule_human_readable="grade >= 10",
                financials=PlanFinancials(),
            )
        ],
    )
    original = ms.build_benefit_statement
    ms.build_benefit_statement = lambda db, employee: broker_stmt
    try:
        out = ms.build_member_statement(None, None)
    finally:
        ms.build_benefit_statement = original
    line = out.coverage[0]
    assert line.financials is None
    assert line.match_method is None
    assert line.match_confidence is None
    assert line.rule_human_readable is None
    assert line.product_code == "GHS"  # everything else intact
