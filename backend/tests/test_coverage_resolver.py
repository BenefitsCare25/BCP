"""Coverage resolver + employee plan-override write path (enrollment Phase 1).

Proves that a sparse EmployeePlanOverride wins over the cohort (category) default
and surfaces through hydrate_plans → the benefit statement, that declining drops
coverage, that dependant elections flow through, and that orphan overrides are
detected.
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest

TEST_DB = Path(__file__).parent / "_test_coverage_resolver.db"
os.environ["INSPRO_DATABASE_URL"] = f"sqlite:///{TEST_DB}"

from fastapi.testclient import TestClient  # noqa: E402

from app.core.auth import (  # noqa: E402
    DEMO_BROKER_FIRM_ID,
    CurrentUser,
    get_current_user,
)
from app.db.base import Base  # noqa: E402
from app.db.session import SessionLocal, engine  # noqa: E402
from app.main import app  # noqa: E402
from app.models import (  # noqa: E402
    Category,
    Client,
    Dependant,
    Employee,
    EmployeePlanOverride,
    Plan,
    PolicyYear,
    Product,
)
from app.models.category import CategoryStatus, SourceKind  # noqa: E402
from app.models.employee_plan_override import OverrideSource  # noqa: E402
from app.models.policy_year import PolicyYearStatus  # noqa: E402
from app.services.coverage_resolver import (  # noqa: E402
    find_orphan_overrides,
    load_overrides,
    resolve_plan,
)
from app.services.plan_hydration import hydrate_plans  # noqa: E402
from scripts.seed_demo import seed  # noqa: E402

# Dedicated client so this module's rows never pollute demo-client-scoped
# assertions in other test modules (the suite shares one physical sqlite DB).
CLIENT_ID = "00000000-0000-0000-0000-00000000e000"
PY_ID = "00000000-0000-0000-0000-00000000e001"
PROD_ID = "00000000-0000-0000-0000-00000000e002"
PROD2_ID = "00000000-0000-0000-0000-00000000e003"
CAT_ID = "00000000-0000-0000-0000-00000000e004"
EMP_ID = "00000000-0000-0000-0000-00000000e005"
DEP_ID = "00000000-0000-0000-0000-00000000e006"
DATE = __import__("datetime").date


def _user() -> CurrentUser:
    return CurrentUser(
        user_id="00000000-0000-0000-0000-00000000e0ff",
        broker_firm_id=DEMO_BROKER_FIRM_ID,
        client_id=CLIENT_ID,
        role="broker_admin",
    )


@pytest.fixture(scope="module", autouse=True)
def _setup_db():
    if TEST_DB.exists():
        TEST_DB.unlink()
    Base.metadata.create_all(bind=engine)
    seed()
    with SessionLocal() as s:
        s.add(Client(id=CLIENT_ID, name="Enrollment Test Co", broker_firm_id=DEMO_BROKER_FIRM_ID))
        s.flush()
        s.add(PolicyYear(
            id=PY_ID, client_id=CLIENT_ID, year=2027,
            start_date=DATE(2027, 1, 1), end_date=DATE(2027, 12, 31),
            status=PolicyYearStatus.draft,
        ))
        s.flush()
        s.add(Product(
            id=PROD_ID, client_id=CLIENT_ID, code="ENRTST",
            display_name="Enrollment Test Medical", insurer="ACME", has_dependants=True,
        ))
        s.add(Product(
            id=PROD2_ID, client_id=CLIENT_ID, code="ENRDEN",
            display_name="Enrollment Test Dental", insurer="ACME",
        ))
        s.flush()
        for code, name in (("SILVER", "Silver"), ("GOLD", "Gold")):
            s.add(Plan(
                id=f"plan-{code}", product_id=PROD_ID, policy_year_id=PY_ID,
                code=code, display_name=name,
                benefit_schedule={"benefit_items": [{"name": f"{name} cover"}]},
                status="confirmed",
            ))
        s.add(Category(
            id=CAT_ID, policy_year_id=PY_ID, product_id=PROD_ID, priority=1,
            display_name="Test cohort", raw_description="Test cohort",
            plan_assignments={"plan_code": "SILVER"},
            source=SourceKind.system_generated.value,
            status=CategoryStatus.confirmed.value, human_modified=False,
        ))
        s.add(Employee(
            id=EMP_ID, client_id=CLIENT_ID, policy_year_id=PY_ID,
            staff_id="ENR-1", employee_name="Enid Enroll",
            attribute_values={}, derived_attribute_values={},
            matched_categories=[{
                "category_id": CAT_ID, "product_code": "ENRTST",
                "method": "rule", "confidence": 1.0,
            }],
            source="csv_import", status="active",
        ))
        s.flush()
        s.add(Dependant(
            id=DEP_ID, client_id=CLIENT_ID, policy_year_id=PY_ID,
            employee_id=EMP_ID, attribute_values={"relationship": "spouse", "name": "Sam"},
            link_method="staff_id", status="active",
        ))
        s.commit()
    yield
    engine.dispose()
    if TEST_DB.exists():
        TEST_DB.unlink()


@pytest.fixture
def client() -> TestClient:
    app.dependency_overrides[get_current_user] = _user
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.pop(get_current_user, None)
        # Clean overrides between tests so each starts from the cohort default.
        with SessionLocal() as s:
            s.query(EmployeePlanOverride).delete()
            s.commit()


# ── resolve_plan (pure) ──────────────────────────────────────────────────────
def test_resolve_plan_no_override_keeps_default() -> None:
    r = resolve_plan(None, "SILVER")
    assert r.plan_code == "SILVER" and not r.overridden and not r.declined


def test_resolve_plan_override_wins() -> None:
    ov = EmployeePlanOverride(
        employee_id=EMP_ID, policy_year_id=PY_ID, client_id=CLIENT_ID,
        product_id=PROD_ID, product_code="ENRTST", plan_code="GOLD",
        source=OverrideSource.enrollment,
    )
    r = resolve_plan(ov, "SILVER")
    assert r.plan_code == "GOLD" and r.overridden and r.override_source == "enrollment"


def test_resolve_plan_declined() -> None:
    ov = EmployeePlanOverride(
        employee_id=EMP_ID, policy_year_id=PY_ID, client_id=CLIENT_ID,
        product_id=PROD_ID, product_code="ENRTST", plan_code=None, declined=True,
    )
    r = resolve_plan(ov, "SILVER")
    assert r.declined and r.plan_code is None and r.overridden


# ── hydrate_plans honours overrides ──────────────────────────────────────────
def _emp(s) -> Employee:
    return s.get(Employee, EMP_ID)


def test_hydrate_default_plan() -> None:
    with SessionLocal() as s:
        plans = hydrate_plans([_emp(s)], s, PY_ID)[EMP_ID]
    assert plans[0].plan_code == "SILVER" and not plans[0].plan_overridden


def test_put_override_changes_resolved_plan(client: TestClient) -> None:
    res = client.put(
        f"/api/v1/employees/{EMP_ID}/plan-overrides/ENRTST",
        json={"plan_code": "GOLD"},
    )
    assert res.status_code == 200, res.text
    with SessionLocal() as s:
        ovs = load_overrides(s, PY_ID, [EMP_ID])
        assert (EMP_ID, PROD_ID) in ovs
        plans = hydrate_plans([_emp(s)], s, PY_ID)[EMP_ID]
    line = next(p for p in plans if p.product_code == "ENRTST")
    assert line.plan_code == "GOLD" and line.plan_overridden
    assert line.override_source == OverrideSource.manual_admin


def test_invalid_plan_rejected(client: TestClient) -> None:
    res = client.put(
        f"/api/v1/employees/{EMP_ID}/plan-overrides/ENRTST",
        json={"plan_code": "PLATINUM"},
    )
    assert res.status_code == 422


def test_plan_rejected_when_product_has_no_plans(client: TestClient) -> None:
    # ENRDEN (PROD2) has no Plan rows — electing any plan must be rejected, not
    # waved through into a bogus override.
    res = client.put(
        f"/api/v1/employees/{EMP_ID}/plan-overrides/ENRDEN",
        json={"plan_code": "ANY"},
    )
    assert res.status_code == 422


def test_decline_drops_coverage_line(client: TestClient) -> None:
    res = client.put(
        f"/api/v1/employees/{EMP_ID}/plan-overrides/ENRTST",
        json={"declined": True},
    )
    assert res.status_code == 200, res.text
    stmt = client.get(f"/api/v1/employees/{EMP_ID}/benefit-statement").json()
    assert all(c["product_code"] != "ENRTST" for c in stmt["coverage"])


def test_dependant_election_flows_to_statement(client: TestClient) -> None:
    res = client.put(
        f"/api/v1/employees/{EMP_ID}/plan-overrides/ENRTST",
        json={"plan_code": "GOLD", "covered_dependant_ids": [DEP_ID]},
    )
    assert res.status_code == 200, res.text
    stmt = client.get(f"/api/v1/employees/{EMP_ID}/benefit-statement").json()
    line = next(c for c in stmt["coverage"] if c["product_code"] == "ENRTST")
    assert line["covers_dependants"] is True
    assert [d["id"] for d in line["covered_dependants"]] == [DEP_ID]


def test_invalid_dependant_rejected(client: TestClient) -> None:
    res = client.put(
        f"/api/v1/employees/{EMP_ID}/plan-overrides/ENRTST",
        json={"plan_code": "GOLD", "covered_dependant_ids": ["not-a-real-dep"]},
    )
    assert res.status_code == 422


def test_delete_reverts_to_default(client: TestClient) -> None:
    client.put(
        f"/api/v1/employees/{EMP_ID}/plan-overrides/ENRTST",
        json={"plan_code": "GOLD"},
    )
    res = client.delete(f"/api/v1/employees/{EMP_ID}/plan-overrides/ENRTST")
    assert res.status_code == 204
    with SessionLocal() as s:
        plans = hydrate_plans([_emp(s)], s, PY_ID)[EMP_ID]
    assert plans[0].plan_code == "SILVER" and not plans[0].plan_overridden


def test_orphan_override_detected(client: TestClient) -> None:
    # Override for ENRDEN — a product the employee's category does NOT cover.
    with SessionLocal() as s:
        s.add(EmployeePlanOverride(
            employee_id=EMP_ID, policy_year_id=PY_ID, client_id=CLIENT_ID,
            product_id=PROD2_ID, product_code="ENRDEN", plan_code="X",
            source=OverrideSource.bulk_update,
        ))
        s.commit()
        orphans = find_orphan_overrides(s, PY_ID, [_emp(s)])
    assert [o.product_code for o in orphans] == ["ENRDEN"]


def test_orphans_endpoint(client: TestClient) -> None:
    with SessionLocal() as s:
        s.add(EmployeePlanOverride(
            employee_id=EMP_ID, policy_year_id=PY_ID, client_id=CLIENT_ID,
            product_id=PROD2_ID, product_code="ENRDEN", plan_code="X",
            source=OverrideSource.bulk_update,
        ))
        s.commit()
    res = client.get(f"/api/v1/policy-years/{PY_ID}/plan-overrides/orphans")
    assert res.status_code == 200, res.text
    assert [o["product_code"] for o in res.json()] == ["ENRDEN"]


def test_snapshot_includes_overrides(client: TestClient) -> None:
    from app.services.snapshot import build_snapshot
    client.put(
        f"/api/v1/employees/{EMP_ID}/plan-overrides/ENRTST", json={"plan_code": "GOLD"}
    )
    with SessionLocal() as s:
        snap = build_snapshot(s, PY_ID)
    assert snap["counts"]["plan_overrides"] == 1
    assert snap["plan_overrides"][0]["plan_code"] == "GOLD"
    assert "enrollment_windows" in snap and "leave_elections" in snap
