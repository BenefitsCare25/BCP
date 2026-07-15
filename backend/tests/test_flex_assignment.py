"""Flex wallet assignment + its surfacing on the benefit statement.

Builds a confirmed Flex scheme (one Singapore JG18+ tier with family-status
limits, cost-share and claimable categories) plus an eligible employee with a
spouse + child, and asserts:
  * POST .../flex-scheme/assign persists the wallet onto the employee row
  * the resolved wallet matches the family-status limit (M1C → 3,000)
  * the benefit statement exposes a `flex` block with the wallet + categories
  * an inactive / ineligible employee carries no wallet
  * assign refuses to run on a draft (unconfirmed) scheme
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest

TEST_DB = Path(__file__).parent / "_test_flex_assignment.db"
os.environ["INSPRO_DATABASE_URL"] = f"sqlite:///{TEST_DB}"

from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import func, select  # noqa: E402
from sqlalchemy.orm.attributes import flag_modified  # noqa: E402

from app.core.auth import DEMO_CLIENT_ID  # noqa: E402
from app.db.base import Base  # noqa: E402
from app.db.session import SessionLocal, engine  # noqa: E402
from app.main import app  # noqa: E402
from app.models import Dependant, Employee, FlexScheme, PolicyYear  # noqa: E402
from app.models.flex_scheme import FlexSchemeStatus  # noqa: E402
from app.services.benefit_statement import build_benefit_statement  # noqa: E402
from scripts.seed_demo import seed  # noqa: E402

EMP_ELIGIBLE = "00000000-0000-0000-0000-0000000000a1"
EMP_INELIGIBLE = "00000000-0000-0000-0000-0000000000a2"

_SCHEME = {
    "meta": {"scheme_name": "Flexi Benefits", "currency": "SGD"},
    "tiers": [
        {
            "name": "JG18+ Singapore",
            "country": "singapore",
            "currency": "SGD",
            "employee_type": {"raw": "Job grade 18 and above", "job_grade_min": 18},
            "limits": [
                {"family_status": "S", "amount": 1000},
                {"family_status": "M", "amount": 2000},
                {"family_status": "M1C", "amount": 3000},
            ],
            "cost_sharing": {"employer_pct": 80, "employee_pct": 20},
            "benefit_categories": [
                {"name": "Dental", "claimable": True, "sub_limit": 500, "note": "Panel only"},
                {"name": "Optical", "claimable": True},
            ],
        }
    ],
}


@pytest.fixture(scope="module", autouse=True)
def _setup_db():
    if TEST_DB.exists():
        TEST_DB.unlink()
    Base.metadata.create_all(bind=engine)
    seed()

    with SessionLocal() as s:
        py_id = (
            s.query(PolicyYear.id).filter(PolicyYear.client_id == DEMO_CLIENT_ID).first()[0]
        )

        s.add(FlexScheme(
            policy_year_id=py_id, status=FlexSchemeStatus.confirmed, scheme=_SCHEME,
        ))
        s.add(Employee(
            id=EMP_ELIGIBLE, client_id=DEMO_CLIENT_ID, policy_year_id=py_id,
            staff_id="FX-001", employee_name="Flex Eligible",
            attribute_values={"grade": 18, "nationality": "Singapore"},
            derived_attribute_values={"grade": 18},
            source="csv_import", status="active",
        ))
        s.add(Employee(
            id=EMP_INELIGIBLE, client_id=DEMO_CLIENT_ID, policy_year_id=py_id,
            staff_id="FX-002", employee_name="Flex Junior",
            attribute_values={"grade": 5, "nationality": "Singapore"},
            derived_attribute_values={"grade": 5},
            source="csv_import", status="active",
        ))
        s.flush()
        s.add_all([
            Dependant(client_id=DEMO_CLIENT_ID, policy_year_id=py_id,
                      employee_id=EMP_ELIGIBLE, link_method="staff_id", status="active",
                      attribute_values={"name": "Spouse X", "relationship": "spouse"}),
            Dependant(client_id=DEMO_CLIENT_ID, policy_year_id=py_id,
                      employee_id=EMP_ELIGIBLE, link_method="staff_id", status="active",
                      attribute_values={"name": "Kid X", "relationship": "child"}),
        ])
        s.commit()
        _setup_db.py_id = py_id  # type: ignore[attr-defined]

    yield
    engine.dispose()
    if TEST_DB.exists():
        TEST_DB.unlink()


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


def _py_id() -> str:
    return _setup_db.py_id  # type: ignore[attr-defined]


def test_assign_persists_wallet_onto_employee(client: TestClient) -> None:
    res = client.post(f"/api/v1/policy-years/{_py_id()}/flex-scheme/assign")
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["employees_total"] >= 2
    assert body["employees_assigned"] >= 1
    assert body["by_tier"].get("JG18+ Singapore", 0) >= 1

    with SessionLocal() as s:
        emp = s.get(Employee, EMP_ELIGIBLE)
        assert emp.flex_tier_name == "JG18+ Singapore"
        assert emp.flex_family_status == "M1C"  # spouse + 1 child
        assert emp.flex_wallet_amount == 3000.0
        assert emp.flex_currency == "SGD"
        assert emp.flex_source == "dependants"
        assert emp.flex_assigned_at is not None


def test_ineligible_employee_has_no_wallet(client: TestClient) -> None:
    client.post(f"/api/v1/policy-years/{_py_id()}/flex-scheme/assign")
    with SessionLocal() as s:
        emp = s.get(Employee, EMP_INELIGIBLE)
        # Grade 5 falls outside the JG18+ band → no tier, no wallet.
        assert emp.flex_tier_name is None
        assert emp.flex_wallet_amount is None


def test_benefit_statement_exposes_flex(client: TestClient) -> None:
    client.post(f"/api/v1/policy-years/{_py_id()}/flex-scheme/assign")
    with SessionLocal() as s:
        emp = s.get(Employee, EMP_ELIGIBLE)
        st = build_benefit_statement(s, emp)
    assert st.flex is not None
    assert st.flex.tier_name == "JG18+ Singapore"
    assert st.flex.wallet_amount == 3000.0
    assert st.flex.currency == "SGD"
    assert st.flex.scheme_name == "Flexi Benefits"
    assert st.flex.employer_pct == 80
    names = {c.name for c in st.flex.benefit_categories}
    assert names == {"Dental", "Optical"}


def test_benefit_statement_endpoint_includes_flex(client: TestClient) -> None:
    client.post(f"/api/v1/policy-years/{_py_id()}/flex-scheme/assign")
    res = client.get(f"/api/v1/employees/{EMP_ELIGIBLE}/benefit-statement")
    assert res.status_code == 200, res.text
    flex = res.json()["flex"]
    assert flex is not None
    assert flex["wallet_amount"] == 3000.0
    assert flex["family_status"] == "M1C"


def test_statement_flags_stale_after_tier_rename(client: TestClient) -> None:
    """Renaming a tier after assignment flags the wallet stale and drops the
    (now-unresolvable) claimable categories, while the persisted wallet stays."""
    client.post(f"/api/v1/policy-years/{_py_id()}/flex-scheme/assign")
    with SessionLocal() as s:
        row = s.execute(
            select(FlexScheme).where(FlexScheme.policy_year_id == _py_id())
        ).scalar_one()
        original = row.scheme
        edited = {**original, "tiers": [{**original["tiers"][0], "name": "Renamed Tier"}]}
        row.scheme = edited
        flag_modified(row, "scheme")
        s.commit()
    try:
        with SessionLocal() as s:
            emp = s.get(Employee, EMP_ELIGIBLE)
            st = build_benefit_statement(s, emp)
        assert st.flex is not None
        assert st.flex.assignment_stale is True
        assert st.flex.benefit_categories == []  # old tier name no longer resolves
        assert st.flex.wallet_amount == 3000.0   # persisted wallet still shown
    finally:
        with SessionLocal() as s:
            row = s.execute(
                select(FlexScheme).where(FlexScheme.policy_year_id == _py_id())
            ).scalar_one()
            row.scheme = original
            flag_modified(row, "scheme")
            s.commit()


def test_confirm_is_idempotent_when_already_confirmed(client: TestClient) -> None:
    """A duplicate confirm on an already-confirmed scheme is a no-op (no extra
    assign run / audit churn)."""
    before = _assign_audit_count()
    res = client.post(f"/api/v1/policy-years/{_py_id()}/flex-scheme/confirm")
    assert res.status_code == 200, res.text
    assert res.json()["status"] == "confirmed"
    assert _assign_audit_count() == before  # no new flex_scheme.assign rows


def _assign_audit_count() -> int:
    from app.models import AuditLog
    with SessionLocal() as s:
        return s.scalar(
            select(func.count(AuditLog.id)).where(
                AuditLog.action == "flex_scheme.assign",
                AuditLog.entity_id == _py_id(),
            )
        ) or 0


def test_assign_requires_confirmed_scheme(client: TestClient) -> None:
    """Reopening the scheme to draft blocks assignment until re-confirmed."""
    with SessionLocal() as s:
        scheme = s.execute(
            FlexScheme.__table__.select().where(
                FlexScheme.policy_year_id == _py_id()
            )
        ).first()
        row = s.get(FlexScheme, scheme.id)
        row.status = FlexSchemeStatus.draft
        s.commit()
    try:
        res = client.post(f"/api/v1/policy-years/{_py_id()}/flex-scheme/assign")
        assert res.status_code == 422
    finally:
        with SessionLocal() as s:
            row = s.get(FlexScheme, scheme.id)
            row.status = FlexSchemeStatus.confirmed
            s.commit()


def test_roster_vocab_lists_actual_grade_values(client: TestClient) -> None:
    """The vocab endpoint surfaces the raw grade values on the roster (the two
    seeded employees carry grades 18 and 5), for the tier match-set pickers."""
    res = client.get(f"/api/v1/policy-years/{_py_id()}/flex-scheme/roster-vocab")
    assert res.status_code == 200, res.text
    body = res.json()
    grade_values = {g["value"] for g in body["grades"]}
    assert {"18", "5"} <= grade_values
    assert body["employees_total"] >= 2


def test_confirm_warns_on_unmatched_then_acknowledges(client: TestClient) -> None:
    """The coverage guard 409s when an active employee (grade 5) matches no tier,
    then confirms once acknowledged."""
    py = _py_id()
    with SessionLocal() as s:
        row = s.execute(
            select(FlexScheme).where(FlexScheme.policy_year_id == py)
        ).scalar_one()
        row.status = FlexSchemeStatus.draft
        s.commit()

    res = client.post(f"/api/v1/policy-years/{py}/flex-scheme/confirm", json={})
    assert res.status_code == 409, res.text
    detail = res.json()["detail"]
    assert detail["code"] == "unmatched_employees"
    assert detail["ineligible_count"] >= 1

    res = client.post(
        f"/api/v1/policy-years/{py}/flex-scheme/confirm", json={"acknowledge": True}
    )
    assert res.status_code == 200, res.text
    assert res.json()["status"] == "confirmed"
