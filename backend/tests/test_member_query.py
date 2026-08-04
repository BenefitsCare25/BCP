"""Roster selection as a rule — facets, filters, counts, pasted lists.

The rules under test are the ones whose failure is SILENT: a filter that quietly
includes leavers, an exclusion applied before the union instead of after, a facet
counting cohort defaults instead of effective plans.
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest

TEST_DB = Path(__file__).parent / "_test_member_query.db"
os.environ["INSPRO_DATABASE_URL"] = f"sqlite:///{TEST_DB}"

from datetime import date  # noqa: E402

from fastapi.testclient import TestClient  # noqa: E402

from app.core.auth import DEMO_BROKER_FIRM_ID, CurrentUser, get_current_user  # noqa: E402
from app.db.base import Base  # noqa: E402
from app.db.session import SessionLocal, engine  # noqa: E402
from app.main import app  # noqa: E402
from app.models import (  # noqa: E402
    Category,
    Client,
    Employee,
    EmployeeAttributeSchema,
    EmployeePlanOverride,
    Plan,
    PolicyYear,
    Product,
)
from app.models.category import CategoryStatus, SourceKind  # noqa: E402
from app.models.policy_year import PolicyYearStatus  # noqa: E402
from scripts.seed_demo import seed  # noqa: E402

CLIENT_ID = "00000000-0000-0000-0000-0000000mq000"
PY_ID = "00000000-0000-0000-0000-0000000mq001"
PROD_ID = "00000000-0000-0000-0000-0000000mq002"
CAT_SALES = "00000000-0000-0000-0000-0000000mq003"
CAT_OPS = "00000000-0000-0000-0000-0000000mq004"
# Sales: SALES-1 (default GOLD via override), SALES-2. Ops: OPS-1. Leaver: GONE-1.
E_SALES1 = "00000000-0000-0000-0000-0000000mq005"
E_SALES2 = "00000000-0000-0000-0000-0000000mq006"
E_OPS1 = "00000000-0000-0000-0000-0000000mq007"
E_GONE = "00000000-0000-0000-0000-0000000mq008"


def _user() -> CurrentUser:
    return CurrentUser(
        user_id="00000000-0000-0000-0000-0000000mq0ff",
        broker_firm_id=DEMO_BROKER_FIRM_ID, client_id=CLIENT_ID, role="broker_admin",
    )


@pytest.fixture(scope="module", autouse=True)
def _setup_db():
    if TEST_DB.exists():
        TEST_DB.unlink()
    Base.metadata.create_all(bind=engine)
    seed()
    with SessionLocal() as s:
        s.add(Client(id=CLIENT_ID, name="Query Co", broker_firm_id=DEMO_BROKER_FIRM_ID))
        s.flush()
        s.add(PolicyYear(
            id=PY_ID, client_id=CLIENT_ID, year=2030,
            start_date=date(2030, 1, 1), end_date=date(2030, 12, 31),
            status=PolicyYearStatus.draft,
        ))
        s.add(Product(id=PROD_ID, client_id=CLIENT_ID, code="MED",
                      display_name="Medical", insurer="ACME"))
        s.flush()
        for code in ("SILVER", "GOLD"):
            s.add(Plan(id=f"mq-plan-{code}", product_id=PROD_ID, policy_year_id=PY_ID,
                       code=code, display_name=code, status="confirmed"))
        for cid, name in ((CAT_SALES, "Sales cohort"), (CAT_OPS, "Ops cohort")):
            s.add(Category(
                id=cid, policy_year_id=PY_ID, product_id=PROD_ID, priority=1,
                display_name=name, raw_description=name,
                plan_assignments={"plan_code": "SILVER"},
                source=SourceKind.system_generated.value,
                status=CategoryStatus.confirmed.value, human_modified=False,
            ))
        s.add(EmployeeAttributeSchema(
            client_id=CLIENT_ID, attribute_id="department",
            display_name="Department", data_type="string",
        ))
        rows = (
            (E_SALES1, "Q-1", CAT_SALES, "Sales", "1985-06-01", "active"),
            (E_SALES2, "Q-2", CAT_SALES, "Sales", "1995-06-01", "active"),
            (E_OPS1, "Q-3", CAT_OPS, "Ops", "1975-06-01", "active"),
            (E_GONE, "Q-4", CAT_SALES, "Sales", "1990-06-01", "terminated"),
        )
        for eid, staff, cat, dept, dob, status in rows:
            s.add(Employee(
                id=eid, client_id=CLIENT_ID, policy_year_id=PY_ID,
                staff_id=staff, employee_name=f"Emp {staff}",
                attribute_values={"department": dept, "date_of_birth": dob,
                                  "id_no": f"S{staff.replace('-', '')}0000A"},
                derived_attribute_values={},
                matched_categories=[{"category_id": cat, "product_code": "MED",
                                     "method": "rule", "confidence": 1.0}],
                source="csv_import", status=status,
            ))
        s.flush()
        # SALES-1 sits on GOLD by override; everyone else on the cohort default.
        s.add(EmployeePlanOverride(
            employee_id=E_SALES1, policy_year_id=PY_ID, client_id=CLIENT_ID,
            product_id=PROD_ID, product_code="MED", plan_code="GOLD",
            source="manual",
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


def _count(client: TestClient, query: dict, product: str | None = "MED") -> dict:
    res = client.post(
        f"/api/v1/policy-years/{PY_ID}/member-query/count",
        json={"query": query, "product_code": product},
    )
    assert res.status_code == 200, res.text
    return res.json()


# ── Filters ─────────────────────────────────────────────────────────────────


def test_attribute_filter_selects_a_department(client: TestClient) -> None:
    body = _count(client, {"attributes": [{"key": "department", "values": ["Sales"]}]})
    assert body["total"] == 2  # the leaver is not counted


def test_leavers_are_excluded_until_asked_for(client: TestClient) -> None:
    """A bulk tool that silently includes terminated members reinstates cover
    for people who have left."""
    f = {"attributes": [{"key": "department", "values": ["Sales"]}]}
    assert _count(client, f)["total"] == 2
    assert _count(client, {**f, "include_terminated": True})["total"] == 3


def test_not_in_keeps_members_with_no_value(client: TestClient) -> None:
    body = _count(client, {"attributes": [
        {"key": "department", "values": ["Sales"], "op": "not_in"}
    ]})
    assert body["total"] == 1  # Ops only


def test_current_plan_filter_is_effective_not_default(client: TestClient) -> None:
    """SALES-1's override puts them on GOLD while their cohort default is
    SILVER — the filter must follow the resolver, not the category."""
    assert _count(client, {"current_plan_codes": ["GOLD"]})["total"] == 1
    assert _count(client, {"current_plan_codes": ["SILVER"]})["total"] == 2


def test_coverage_state_finds_deviations(client: TestClient) -> None:
    """Every option the Coverage dropdown offers has to select something —
    a filter that silently matches the same set as "Everyone" is a dead control."""
    assert _count(client, {"coverage_state": "overridden"})["total"] == 1
    assert _count(client, {"coverage_state": "default"})["total"] == 2
    assert _count(client, {"coverage_state": "declined"})["total"] == 0

    with SessionLocal() as s:
        s.add(EmployeePlanOverride(
            employee_id=E_OPS1, policy_year_id=PY_ID, client_id=CLIENT_ID,
            product_id=PROD_ID, product_code="MED", declined=True, source="manual",
        ))
        s.commit()
    try:
        assert _count(client, {"coverage_state": "declined"})["total"] == 1
        # A declined member is overridden, and is NOT on their cohort default.
        assert _count(client, {"coverage_state": "overridden"})["total"] == 2
        assert _count(client, {"coverage_state": "default"})["total"] == 1
    finally:
        with SessionLocal() as s:
            s.query(EmployeePlanOverride).filter_by(employee_id=E_OPS1).delete()
            s.commit()


def test_age_filter_uses_anb_at_year_start(client: TestClient) -> None:
    # Born 1975 → ANB 56 at 2030-01-01; 1985 → 46; 1995 → 36.
    assert _count(client, {"age": {"min": 50}})["total"] == 1
    assert _count(client, {"age": {"max": 40}})["total"] == 1


def test_filters_are_anded(client: TestClient) -> None:
    body = _count(client, {
        "attributes": [{"key": "department", "values": ["Sales"]}],
        "current_plan_codes": ["GOLD"],
    })
    assert body["total"] == 1


# ── Union / subtraction order ───────────────────────────────────────────────


def test_explicit_ids_add_to_the_filter_result(client: TestClient) -> None:
    body = _count(client, {
        "attributes": [{"key": "department", "values": ["Ops"]}],
        "employee_ids": [E_SALES1],
    })
    assert body["total"] == 2


def test_exclusions_apply_last(client: TestClient) -> None:
    """Unticking a row must survive the union — an exclusion applied before the
    explicit ids would silently put the member back."""
    body = _count(client, {
        "attributes": [{"key": "department", "values": ["Sales"]}],
        "employee_ids": [E_OPS1],
        "exclude_employee_ids": [E_SALES1, E_OPS1],
    })
    assert body["total"] == 1


def test_unresolved_ids_are_reported_not_silently_dropped(client: TestClient) -> None:
    body = _count(client, {"staff_ids": ["Q-1", "NOPE"]})
    assert body["total"] == 1
    assert [u["value"] for u in body["unresolved"]] == ["NOPE"]


def test_a_leaver_named_explicitly_says_why_it_was_dropped(client: TestClient) -> None:
    """"Not found" and "excluded as a leaver" need different answers — only the
    second is a one-checkbox fix."""
    body = _count(client, {"employee_ids": [E_GONE]})
    assert body["total"] == 0
    assert "leaver" in body["unresolved"][0]["reason"].lower()


def test_empty_query_is_rejected(client: TestClient) -> None:
    res = client.post(
        f"/api/v1/policy-years/{PY_ID}/member-query/count",
        json={"query": {"include_terminated": True}},
    )
    assert res.status_code == 422


# ── Facets ──────────────────────────────────────────────────────────────────


def test_facets_carry_labels_and_headcounts(client: TestClient) -> None:
    res = client.get(f"/api/v1/policy-years/{PY_ID}/member-facets")
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["employees_total"] == 3 and body["terminated_total"] == 1
    dept = next(a for a in body["attributes"] if a["key"] == "department")
    assert dept["label"] == "Department"
    assert {v["value"]: v["count"] for v in dept["values"]} == {"Sales": 2, "Ops": 1}


def test_facets_omit_identifiers(client: TestClient) -> None:
    """NRIC and date of birth are one value per person — a picker offering them
    is noise, and one of them is PII."""
    res = client.get(f"/api/v1/policy-years/{PY_ID}/member-facets")
    keys = {a["key"] for a in res.json()["attributes"]}
    assert "id_no" not in keys and "date_of_birth" not in keys


def test_plan_facet_counts_effective_coverage(client: TestClient) -> None:
    res = client.get(f"/api/v1/policy-years/{PY_ID}/member-facets")
    product = next(p for p in res.json()["products"] if p["code"] == "MED")
    assert {p["code"]: p["count"] for p in product["plans"]} == {"SILVER": 2, "GOLD": 1}
    assert product["covered"] == 3


def test_category_facets_include_empty_cohorts(client: TestClient) -> None:
    """A cohort matching nobody is a matching gap the broker needs to see."""
    res = client.get(f"/api/v1/policy-years/{PY_ID}/member-facets")
    counts = {c["id"]: c["count"] for c in res.json()["categories"]}
    assert counts[CAT_SALES] == 2 and counts[CAT_OPS] == 1


# ── Pasted lists ────────────────────────────────────────────────────────────


def test_pasted_list_resolves_staff_ids_and_nric(client: TestClient) -> None:
    res = client.post(
        f"/api/v1/policy-years/{PY_ID}/member-query/resolve",
        # A column copied out of Excel, a comma list, and an NRIC in one paste.
        json={"text": "Q-1\nQ-2, SQ30000A\nNOBODY"},
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert {m["staff_id"] for m in body["matched"]} == {"Q-1", "Q-2", "Q-3"}
    assert body["unmatched"] == ["NOBODY"]


def test_pasted_duplicates_are_counted(client: TestClient) -> None:
    res = client.post(
        f"/api/v1/policy-years/{PY_ID}/member-query/resolve",
        json={"text": "Q-1 Q-1 Q-2"},
    )
    body = res.json()
    assert len(body["matched"]) == 2 and body["duplicates"] == 1
