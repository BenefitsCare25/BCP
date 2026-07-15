"""Bulk plan update — preview (read-only) + apply (writes overrides) (Phase 5)."""
from __future__ import annotations

import os
from pathlib import Path

import pytest

TEST_DB = Path(__file__).parent / "_test_bulk_plan_update.db"
os.environ["INSPRO_DATABASE_URL"] = f"sqlite:///{TEST_DB}"

from datetime import date  # noqa: E402

from fastapi.testclient import TestClient  # noqa: E402

from app.core.auth import DEMO_BROKER_FIRM_ID, CurrentUser, get_current_user  # noqa: E402
from app.db.base import Base  # noqa: E402
from app.db.session import SessionLocal, engine  # noqa: E402
from app.main import app  # noqa: E402
from app.models import (  # noqa: E402
    BulkPlanUpdate,
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
from app.models.policy_year import PolicyYearStatus  # noqa: E402
from app.services.coverage_resolver import load_overrides  # noqa: E402
from scripts.seed_demo import seed  # noqa: E402

CLIENT_ID = "00000000-0000-0000-0000-00000000d000"
PY_ID = "00000000-0000-0000-0000-00000000d001"
PROD_ID = "00000000-0000-0000-0000-00000000d002"
OTHER_PROD_ID = "00000000-0000-0000-0000-00000000d003"
CAT_ID = "00000000-0000-0000-0000-00000000d004"
EMP1 = "00000000-0000-0000-0000-00000000d005"
EMP2 = "00000000-0000-0000-0000-00000000d006"
EMP_NO_PROD = "00000000-0000-0000-0000-00000000d007"
DEP1 = "00000000-0000-0000-0000-00000000d008"


def _user() -> CurrentUser:
    return CurrentUser(
        user_id="00000000-0000-0000-0000-00000000d0ff",
        broker_firm_id=DEMO_BROKER_FIRM_ID, client_id=CLIENT_ID, role="broker_admin",
    )


@pytest.fixture(scope="module", autouse=True)
def _setup_db():
    if TEST_DB.exists():
        TEST_DB.unlink()
    Base.metadata.create_all(bind=engine)
    seed()
    with SessionLocal() as s:
        s.add(Client(id=CLIENT_ID, name="Bulk Co", broker_firm_id=DEMO_BROKER_FIRM_ID))
        s.flush()
        s.add(PolicyYear(
            id=PY_ID, client_id=CLIENT_ID, year=2030,
            start_date=date(2030, 1, 1), end_date=date(2030, 12, 31),
            status=PolicyYearStatus.draft,
        ))
        s.flush()
        s.add(Product(id=PROD_ID, client_id=CLIENT_ID, code="MED",
                      display_name="Medical", insurer="ACME", has_dependants=True))
        s.add(Product(id=OTHER_PROD_ID, client_id=CLIENT_ID, code="DEN",
                      display_name="Dental", insurer="ACME"))
        s.flush()
        for code in ("SILVER", "GOLD"):
            s.add(Plan(id=f"d-plan-{code}", product_id=PROD_ID, policy_year_id=PY_ID,
                       code=code, display_name=code, status="confirmed"))
        s.add(Category(
            id=CAT_ID, policy_year_id=PY_ID, product_id=PROD_ID, priority=1,
            display_name="MED cohort", raw_description="MED cohort",
            plan_assignments={"plan_code": "SILVER"},
            source=SourceKind.system_generated.value,
            status=CategoryStatus.confirmed.value, human_modified=False,
        ))
        # EMP1, EMP2 are in the MED cohort; EMP_NO_PROD matches nothing.
        for eid, staff, matched in (
            (EMP1, "D-1", True), (EMP2, "D-2", True), (EMP_NO_PROD, "D-3", False)
        ):
            s.add(Employee(
                id=eid, client_id=CLIENT_ID, policy_year_id=PY_ID,
                staff_id=staff, employee_name=f"Emp {staff}",
                attribute_values={}, derived_attribute_values={},
                matched_categories=([{"category_id": CAT_ID, "product_code": "MED",
                                      "method": "rule", "confidence": 1.0}] if matched else []),
                source="csv_import", status="active",
            ))
        s.flush()
        s.add(Dependant(id=DEP1, client_id=CLIENT_ID, policy_year_id=PY_ID,
                        employee_id=EMP1, attribute_values={"relationship": "spouse"},
                        link_method="staff_id", status="active"))
        s.commit()
    yield
    engine.dispose()
    if TEST_DB.exists():
        TEST_DB.unlink()


@pytest.fixture(autouse=True)
def _reset():
    yield
    with SessionLocal() as s:
        s.query(EmployeePlanOverride).delete()
        s.query(BulkPlanUpdate).delete()
        s.commit()


@pytest.fixture
def client() -> TestClient:
    app.dependency_overrides[get_current_user] = _user
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.pop(get_current_user, None)


def test_preview_is_read_only(client: TestClient) -> None:
    res = client.post(
        f"/api/v1/policy-years/{PY_ID}/bulk-plan-updates/preview",
        json={"product_code": "MED", "action": "set_plan", "target_plan_code": "GOLD",
              "selector": {"staff_ids": ["D-1", "D-2", "D-3"]}},
    )
    assert res.status_code == 200, res.text
    counts = res.json()["counts"]
    assert counts["would_apply"] == 2 and counts["skipped"] == 1
    with SessionLocal() as s:  # no writes happened
        assert load_overrides(s, PY_ID, [EMP1, EMP2]) == {}


def test_apply_writes_overrides_and_record(client: TestClient) -> None:
    res = client.post(
        f"/api/v1/policy-years/{PY_ID}/bulk-plan-updates/apply",
        json={"product_code": "MED", "action": "set_plan", "target_plan_code": "GOLD",
              "selector": {"employee_ids": [EMP1, EMP2]}},
    )
    assert res.status_code == 200, res.text
    assert res.json()["counts"]["applied"] == 2
    with SessionLocal() as s:
        ovs = load_overrides(s, PY_ID, [EMP1, EMP2])
        assert ovs[(EMP1, PROD_ID)].plan_code == "GOLD"
        assert ovs[(EMP2, PROD_ID)].source == "bulk_update"
        assert s.query(BulkPlanUpdate).count() == 1


def test_apply_decline_with_dependants(client: TestClient) -> None:
    res = client.post(
        f"/api/v1/policy-years/{PY_ID}/bulk-plan-updates/apply",
        json={"product_code": "MED", "action": "decline",
              "selector": {"employee_ids": [EMP1]}},
    )
    assert res.status_code == 200
    with SessionLocal() as s:
        assert load_overrides(s, PY_ID, [EMP1])[(EMP1, PROD_ID)].declined is True


def test_apply_include_all_dependants(client: TestClient) -> None:
    res = client.post(
        f"/api/v1/policy-years/{PY_ID}/bulk-plan-updates/apply",
        json={"product_code": "MED", "action": "set_plan", "target_plan_code": "GOLD",
              "selector": {"employee_ids": [EMP1]},
              "dependant_action": {"mode": "include_all"}},
    )
    assert res.status_code == 200
    with SessionLocal() as s:
        ov = load_overrides(s, PY_ID, [EMP1])[(EMP1, PROD_ID)]
        assert ov.covered_dependant_ids == [DEP1]


def test_apply_snapshots_flex_price_tag(client: TestClient) -> None:
    # With a pricing matrix, a bulk apply snapshots the resolved price tag onto
    # the override (keyed by the member's baseline category + the new plan).
    from app.models import FlexPricing
    from app.services.cohort_tiers import tier_key

    with SessionLocal() as s:
        emp = s.get(Employee, EMP1)
        emp.attribute_values = {"date_of_birth": "1980-03-15"}
        s.add(FlexPricing(
            policy_year_id=PY_ID, client_id=CLIENT_ID,
            pricing={"products": {PROD_ID: {
                "age_bands": [{"label": "all", "min": 0, "max": 200}],
                "price_tags": {tier_key(CAT_ID, "GOLD"): {"all": 1234}},
            }}},
        ))
        s.commit()
    try:
        res = client.post(
            f"/api/v1/policy-years/{PY_ID}/bulk-plan-updates/apply",
            json={"product_code": "MED", "action": "set_plan", "target_plan_code": "GOLD",
                  "selector": {"employee_ids": [EMP1]}},
        )
        assert res.status_code == 200, res.text
        with SessionLocal() as s:
            ov = load_overrides(s, PY_ID, [EMP1])[(EMP1, PROD_ID)]
            assert ov.flex_price_tag == 1234.0
    finally:
        with SessionLocal() as s:
            s.query(FlexPricing).delete()
            s.get(Employee, EMP1).attribute_values = {}
            s.commit()


def test_unknown_staff_id_reported_as_error(client: TestClient) -> None:
    res = client.post(
        f"/api/v1/policy-years/{PY_ID}/bulk-plan-updates/apply",
        json={"product_code": "MED", "action": "set_plan", "target_plan_code": "GOLD",
              "selector": {"staff_ids": ["D-1", "NOPE"]}},
    )
    body = res.json()
    assert body["status"] == "partially_failed"
    assert body["counts"]["error"] == 1 and body["counts"]["applied"] == 1


def test_invalid_target_plan_422(client: TestClient) -> None:
    res = client.post(
        f"/api/v1/policy-years/{PY_ID}/bulk-plan-updates/preview",
        json={"product_code": "MED", "action": "set_plan", "target_plan_code": "PLATINUM",
              "selector": {"employee_ids": [EMP1]}},
    )
    assert res.status_code == 422


def test_unknown_product_404(client: TestClient) -> None:
    res = client.post(
        f"/api/v1/policy-years/{PY_ID}/bulk-plan-updates/preview",
        json={"product_code": "NOSUCH", "action": "decline",
              "selector": {"employee_ids": [EMP1]}},
    )
    assert res.status_code == 404


def test_apply_preserves_elected_dependant_option_levels(client: TestClient) -> None:
    """Elected dependant option LEVELS are tier-independent — a bulk plan change
    must carry them over (dropping them would silently unprice covered
    dependants), while a bulk decline clears them."""
    with SessionLocal() as s:
        s.add(EmployeePlanOverride(
            employee_id=EMP1, policy_year_id=PY_ID, client_id=CLIENT_ID,
            product_id=PROD_ID, product_code="MED", plan_code="SILVER",
            covered_dependant_ids=[DEP1],
            dependant_option_ids={"spouse": "cat-level-40k"},
            source="enrollment",
        ))
        s.commit()
    res = client.post(
        f"/api/v1/policy-years/{PY_ID}/bulk-plan-updates/apply",
        json={"product_code": "MED", "action": "set_plan", "target_plan_code": "GOLD",
              "selector": {"employee_ids": [EMP1]}},
    )
    assert res.status_code == 200, res.text
    with SessionLocal() as s:
        ov = load_overrides(s, PY_ID, [EMP1])[(EMP1, PROD_ID)]
        assert ov.plan_code == "GOLD"
        assert ov.dependant_option_ids == {"spouse": "cat-level-40k"}
        assert ov.covered_dependant_ids == [DEP1]  # untouched
    # A bulk decline removes the coverage AND the elected levels.
    res = client.post(
        f"/api/v1/policy-years/{PY_ID}/bulk-plan-updates/apply",
        json={"product_code": "MED", "action": "decline",
              "selector": {"employee_ids": [EMP1]}},
    )
    assert res.status_code == 200, res.text
    with SessionLocal() as s:
        ov = load_overrides(s, PY_ID, [EMP1])[(EMP1, PROD_ID)]
        assert ov.declined is True and ov.dependant_option_ids is None
