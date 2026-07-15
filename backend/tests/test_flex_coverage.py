"""Flex coverage validation — "is anyone left out?" reconciliation + export.

Builds a policy year with a banded flex scheme and a roster that deliberately hits
every exception bucket:
  * employee assigned + family status resolved  → OK
  * employee assigned but no family status      → no_family_status
  * employee with a family status but off-band  → not_in_any_tier
  * a spouse dependant (classified)             → dependants_ok
  * a "parent" dependant on an active employee  → unclassified_relationship
  * a dependant with no employee link           → orphaned
  * a dependant linked to an unknown employee   → inactive_link

and asserts the reconciliation identity holds, the right people surface, and the
.xlsx export streams back.
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest

TEST_DB = Path(__file__).parent / "_test_flex_coverage.db"
os.environ["INSPRO_DATABASE_URL"] = f"sqlite:///{TEST_DB}"

from datetime import date  # noqa: E402

from fastapi.testclient import TestClient  # noqa: E402

from app.core.auth import (  # noqa: E402
    DEMO_BROKER_FIRM_ID,
    CurrentUser,
    get_current_user,
)
from app.db.base import Base  # noqa: E402
from app.db.session import SessionLocal, engine  # noqa: E402
from app.main import app  # noqa: E402
from app.models import Client, Dependant, Employee, FlexScheme, PolicyYear  # noqa: E402
from app.models.flex_scheme import FlexSchemeStatus  # noqa: E402
from app.models.policy_year import PolicyYearStatus  # noqa: E402
from app.services.flex_membership import (  # noqa: E402
    ResolvedEmployee,
    ResolvedRoster,
    _no_family_status_detail,
    classify_relationship,
    compute_flex_coverage,
    resolve_employee,
)
from scripts.seed_demo import seed  # noqa: E402

# A DEDICATED client + user (the whole suite shares one SQLite engine bound at
# first import, so writing under DEMO would perturb other modules' seeded counts).
CLIENT_CV_ID = "00000000-0000-0000-0000-0000000000ca"
EMP_OK = "00000000-0000-0000-0000-0000000000c1"
EMP_NO_FS = "00000000-0000-0000-0000-0000000000c2"
EMP_OFF_BAND = "00000000-0000-0000-0000-0000000000c3"
EMP_INACTIVE = "00000000-0000-0000-0000-0000000000c4"


def _user_cv() -> CurrentUser:
    return CurrentUser(
        user_id="00000000-0000-0000-0000-0000000000cb",
        broker_firm_id=DEMO_BROKER_FIRM_ID,
        client_id=CLIENT_CV_ID,
        role="broker_admin",
    )

_SCHEME = {
    "meta": {"scheme_name": "Coverage Flexi", "currency": "SGD"},
    "tiers": [
        {
            "name": "JG18+ Singapore",
            "country": "singapore",
            "currency": "SGD",
            "employee_type": {"raw": "Job grade 18 and above", "job_grade_min": 18},
            "limits": [
                {"family_status": "S", "amount": 1000},
                {"family_status": "M", "amount": 2000},
            ],
            "benefit_categories": [{"name": "Dental", "claimable": True}],
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
        # Dedicated client + policy year so nothing here counts against seed's
        # DEMO tenant (the engine/DB is shared suite-wide).
        s.add(Client(id=CLIENT_CV_ID, name="Coverage Co (test)",
                     broker_firm_id=DEMO_BROKER_FIRM_ID))
        s.flush()
        py = PolicyYear(
            client_id=CLIENT_CV_ID, year=2031,
            start_date=date(2031, 1, 1), end_date=date(2031, 12, 31),
            status=PolicyYearStatus.draft,
        )
        s.add(py)
        s.flush()
        py_id = py.id
        s.add(FlexScheme(
            policy_year_id=py_id, status=FlexSchemeStatus.confirmed, scheme=_SCHEME,
        ))
        # Assigned + resolved (spouse dependant → Married).
        s.add(Employee(
            id=EMP_OK, client_id=CLIENT_CV_ID, policy_year_id=py_id,
            staff_id="CV-OK", employee_name="Ada Ok",
            attribute_values={"grade": 18, "nationality": "Singapore"},
            derived_attribute_values={"grade": 18}, source="csv_import", status="active",
        ))
        # Assigned (grade 18) but no dependants and no marital status → no family status.
        s.add(Employee(
            id=EMP_NO_FS, client_id=CLIENT_CV_ID, policy_year_id=py_id,
            staff_id="CV-NOFS", employee_name="Ben Blank",
            attribute_values={"grade": 18, "nationality": "Singapore"},
            derived_attribute_values={"grade": 18}, source="csv_import", status="active",
        ))
        # Off-band (grade 5) but a resolvable marital status → not in any tier.
        s.add(Employee(
            id=EMP_OFF_BAND, client_id=CLIENT_CV_ID, policy_year_id=py_id,
            staff_id="CV-OFF", employee_name="Cara Contractor",
            attribute_values={"grade": 5, "nationality": "Singapore",
                              "marital_status": "single", "designation": "Intern"},
            derived_attribute_values={"grade": 5}, source="csv_import", status="active",
        ))
        # Not on the active roster — a dependant still points here (inactive link).
        s.add(Employee(
            id=EMP_INACTIVE, client_id=CLIENT_CV_ID, policy_year_id=py_id,
            staff_id="CV-GONE", employee_name="Dan Departed",
            attribute_values={"grade": 18, "nationality": "Singapore"},
            derived_attribute_values={"grade": 18}, source="csv_import", status="terminated",
        ))
        s.flush()
        s.add_all([
            # Classified spouse on the OK employee.
            Dependant(client_id=CLIENT_CV_ID, policy_year_id=py_id, employee_id=EMP_OK,
                      link_method="staff_id", status="active",
                      attribute_values={"name": "Spouse Ok", "relationship": "spouse"}),
            # Unclassified relationship on an active employee.
            Dependant(client_id=CLIENT_CV_ID, policy_year_id=py_id, employee_id=EMP_OK,
                      link_method="staff_id", status="active",
                      attribute_values={"name": "Grandpa Ok", "relationship": "parent"}),
            # Orphaned — no employee link.
            Dependant(client_id=CLIENT_CV_ID, policy_year_id=py_id, employee_id=None,
                      link_method="unlinked", status="active",
                      attribute_values={"name": "Lost Child", "relationship": "child"}),
            # Linked to a terminated employee not on the active roster.
            Dependant(client_id=CLIENT_CV_ID, policy_year_id=py_id, employee_id=EMP_INACTIVE,
                      link_method="staff_id", status="active",
                      attribute_values={"name": "Ghost Spouse", "relationship": "spouse"}),
        ])
        s.commit()
        _setup_db.py_id = py_id  # type: ignore[attr-defined]

    yield
    engine.dispose()
    if TEST_DB.exists():
        TEST_DB.unlink()


@pytest.fixture
def client() -> TestClient:
    app.dependency_overrides[get_current_user] = _user_cv
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.pop(get_current_user, None)


def _py_id() -> str:
    return _setup_db.py_id  # type: ignore[attr-defined]


def _bucket(body: dict, key: str) -> dict:
    return next(b for b in body["buckets"] if b["key"] == key)


def test_coverage_reconciliation_and_exceptions(client: TestClient) -> None:
    res = client.get(f"/api/v1/policy-years/{_py_id()}/flex-scheme/coverage")
    assert res.status_code == 200, res.text
    body = res.json()

    # Isolated roster: 3 active employees (the terminated one is excluded).
    assert body["has_tiers"] is True
    assert body["employees_total"] == 3
    assert body["employees_ok"] == 1  # only EMP_OK is resolved AND assigned

    no_fs = _bucket(body, "no_family_status")
    not_in = _bucket(body, "not_in_any_tier")
    assert no_fs["count"] == 1
    assert not_in["count"] == 1
    assert _bucket(body, "multiple_tiers")["count"] == 0
    assert no_fs["rows"][0]["staff_id"] == "CV-NOFS"
    assert not_in["rows"][0]["staff_id"] == "CV-OFF"

    # Dependant reconciliation: 4 active deps → 1 classified spouse + 3 exceptions.
    assert body["dependants_total"] == 4
    assert body["dependants_ok"] == 1
    assert _bucket(body, "unclassified_relationship")["count"] == 1
    assert _bucket(body, "orphaned")["count"] == 1
    assert _bucket(body, "inactive_link")["count"] == 1
    assert _bucket(body, "unclassified_relationship")["rows"][0]["label"] == "Grandpa Ok"
    assert _bucket(body, "orphaned")["rows"][0]["label"] == "Lost Child"


def test_no_family_status_row_names_the_reason(client: TestClient) -> None:
    body = client.get(
        f"/api/v1/policy-years/{_py_id()}/flex-scheme/coverage"
    ).json()
    row = next(r for r in _bucket(body, "no_family_status")["rows"] if r["staff_id"] == "CV-NOFS")
    assert "no dependants" in row["detail"].lower()


def test_coverage_export_streams_xlsx(client: TestClient) -> None:
    res = client.get(f"/api/v1/policy-years/{_py_id()}/flex-scheme/coverage/export")
    assert res.status_code == 200, res.text
    assert (
        res.headers["content-type"]
        == "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )
    assert "attachment" in res.headers["content-disposition"]
    # A real .xlsx is a ZIP container ("PK") and non-trivial in size.
    assert res.content[:2] == b"PK"
    assert len(res.content) > 2000


def test_no_tier_scheme_skips_tier_buckets() -> None:
    """Without tiers, tier-based buckets are omitted but family-status still runs."""
    resolved = [
        ResolvedEmployee(
            employee_id="e1", staff_id="S1", name="No Scheme", designation=None,
            grade=None, nationality=None, marital_raw=None, family_status=None,
            source="none", spouse_count=0, child_count=0, dependant_count=0,
            tier_idx=None, tier_name=None, currency=None, wallet_amount=None,
            overlap_tiers=[],
        )
    ]
    roster = ResolvedRoster(
        resolved=resolved, dependants=[], active_emp_ids={"e1"},
        emp_by_id={}, tiers=[], meta={}, scheme_status=None,
        age_limits={}, ref=None,
    )
    cov = compute_flex_coverage(roster)
    assert cov.has_tiers is False
    keys = {b.key for b in cov.buckets}
    assert "not_in_any_tier" not in keys
    assert "multiple_tiers" not in keys
    assert "no_family_status" in keys
    # The one employee has no family status → not ok.
    assert cov.employees_ok == 0
    assert next(b for b in cov.buckets if b.key == "no_family_status").count == 1


_AGE_LIMITS = {"spouse": {"min": 18, "max": 70}, "child": {"min": 0, "max": 25}}


def test_resolve_employee_excludes_over_age_child_from_family_status() -> None:
    """An over-age child no longer inflates the family band (and thus the wallet):
    with the age window applied, a married employee whose only child is 31 resolves
    to M, not M1C. Without a window they'd still count as M1C."""
    emp = Employee(
        id="e1", staff_id="S1", employee_name="Parent", policy_year_id="py",
        status="active", attribute_values={"marital_status": "Married"},
    )
    child = Dependant(
        policy_year_id="py", status="active", employee_id="e1",
        attribute_values={"relationship": "child", "dob": "1996-01-01", "name": "Kid"},
    )
    ref = date(2027, 1, 1)
    filtered = resolve_employee(emp, [child], {}, [], {}, _AGE_LIMITS, ref)
    assert filtered.child_count == 0
    assert filtered.family_status == "M"
    # No window → the same child counts, giving M1C.
    unfiltered = resolve_employee(emp, [child], {}, [], {})
    assert unfiltered.child_count == 1
    assert unfiltered.family_status == "M1C"


def test_over_age_dependant_surfaces_in_outside_age_bucket() -> None:
    """A classified spouse/child past the age window lands in the outside_age_window
    bucket and is dropped from the eligible (dependants_ok) count."""
    emp = Employee(
        id="e1", staff_id="S1", employee_name="Parent", policy_year_id="py",
        status="active", attribute_values={},
    )
    over_age = Dependant(
        policy_year_id="py", status="active", employee_id="e1",
        attribute_values={"relationship": "child", "dob": "1996-01-01", "name": "Kid"},
    )
    in_window = Dependant(
        policy_year_id="py", status="active", employee_id="e1",
        attribute_values={"relationship": "spouse", "dob": "1985-01-01", "name": "Partner"},
    )
    resolved = [
        ResolvedEmployee(
            employee_id="e1", staff_id="S1", name="Parent", designation=None,
            grade=None, nationality=None, marital_raw=None, family_status="M",
            source="roster", spouse_count=1, child_count=0, dependant_count=2,
            tier_idx=None, tier_name=None, currency=None, wallet_amount=None,
            overlap_tiers=[],
        )
    ]
    roster = ResolvedRoster(
        resolved=resolved, dependants=[over_age, in_window], active_emp_ids={"e1"},
        emp_by_id={"e1": emp}, tiers=[], meta={}, scheme_status=None,
        age_limits=_AGE_LIMITS, ref=date(2027, 1, 1),
    )
    cov = compute_flex_coverage(roster)
    outside = next(b for b in cov.buckets if b.key == "outside_age_window")
    assert outside.count == 1
    assert outside.rows[0].label == "Kid"
    assert cov.dependants_ok == 1  # only the in-window spouse counts
    # Reconciliation identity still holds: every dependant lands in exactly one place.
    exception_total = sum(
        b.count for b in cov.buckets if b.kind == "dependant"
    )
    assert cov.dependants_ok + exception_total == cov.dependants_total


def test_stepparent_not_classified_as_child() -> None:
    """A 'stepmother'/'stepfather' must not be miscounted as a child dependant,
    while genuine stepchildren still classify via their base word."""
    assert classify_relationship("stepmother") is None
    assert classify_relationship("stepfather") is None
    assert classify_relationship("step-parent") is None
    assert classify_relationship("stepson") == "child"
    assert classify_relationship("stepdaughter") == "child"
    assert classify_relationship("stepchild") == "child"


def test_no_family_status_detail_distinguishes_dependants() -> None:
    def _emp(marital, deps):
        return ResolvedEmployee(
            employee_id="e", staff_id="s", name=None, designation=None,
            grade=None, nationality=None, marital_raw=marital, family_status=None,
            source="none", spouse_count=0, child_count=0, dependant_count=deps,
            tier_idx=None, tier_name=None, currency=None, wallet_amount=None,
            overlap_tiers=[],
        )

    # No dependants, no marital status.
    assert "no dependants listed" in _no_family_status_detail(_emp(None, 0))
    # Has a dependant, just not a spouse/child — must NOT claim "no dependants".
    d = _no_family_status_detail(_emp(None, 1))
    assert "has dependants" in d
    assert "no dependants listed" not in d
    # An unrecognized marital value is named.
    assert "separated?" in _no_family_status_detail(_emp("separated?", 0))
