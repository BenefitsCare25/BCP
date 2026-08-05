"""Dependant selection — relationship, link state, status, age, and the
sponsoring-employee filter.

The rules under test are the ones whose failure is SILENT: a status default that
hides pending self-adds forever, a name read that reports the PARENT's name, an
employee filter that quietly keeps unlinked rows.
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest

TEST_DB = Path(__file__).parent / "_test_dependant_query.db"
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
    Dependant,
    Employee,
    PolicyYear,
    Product,
)
from app.models.category import CategoryStatus, SourceKind  # noqa: E402
from app.models.policy_year import PolicyYearStatus  # noqa: E402
from scripts.seed_demo import seed  # noqa: E402

CLIENT_ID = "00000000-0000-0000-0000-0000000dq000"
PY_ID = "00000000-0000-0000-0000-0000000dq001"
PROD_ID = "00000000-0000-0000-0000-0000000dq002"
CAT_SALES = "00000000-0000-0000-0000-0000000dq003"
CAT_OPS = "00000000-0000-0000-0000-0000000dq004"
E_SALES = "00000000-0000-0000-0000-0000000dq005"
E_OPS = "00000000-0000-0000-0000-0000000dq006"
# Spouse + child of the Sales employee, a child of the Ops employee,
# an unlinked row, and a pending portal self-add.
D_SPOUSE = "00000000-0000-0000-0000-0000000dq010"
D_CHILD = "00000000-0000-0000-0000-0000000dq011"
D_OPS_KID = "00000000-0000-0000-0000-0000000dq012"
D_UNLINKED = "00000000-0000-0000-0000-0000000dq013"
D_PENDING = "00000000-0000-0000-0000-0000000dq014"
# A row carrying only the parent's name — the DEP_NAME_KEYS trap.
D_NAMELESS = "00000000-0000-0000-0000-0000000dq015"


def _user() -> CurrentUser:
    return CurrentUser(
        user_id="00000000-0000-0000-0000-0000000dq0ff",
        broker_firm_id=DEMO_BROKER_FIRM_ID, client_id=CLIENT_ID, role="broker_admin",
    )


@pytest.fixture(scope="module", autouse=True)
def _setup_db():
    if TEST_DB.exists():
        TEST_DB.unlink()
    Base.metadata.create_all(bind=engine)
    seed()
    with SessionLocal() as s:
        s.add(Client(id=CLIENT_ID, name="Dep Co", broker_firm_id=DEMO_BROKER_FIRM_ID))
        s.flush()
        s.add(PolicyYear(
            id=PY_ID, client_id=CLIENT_ID, year=2030,
            start_date=date(2030, 1, 1), end_date=date(2030, 12, 31),
            status=PolicyYearStatus.draft,
        ))
        s.add(Product(id=PROD_ID, client_id=CLIENT_ID, code="MED",
                      display_name="Medical", insurer="ACME"))
        s.flush()
        for cid, name in ((CAT_SALES, "Sales cohort"), (CAT_OPS, "Ops cohort")):
            s.add(Category(
                id=cid, policy_year_id=PY_ID, product_id=PROD_ID, priority=1,
                display_name=name, raw_description=name,
                plan_assignments={"plan_code": "SILVER"},
                source=SourceKind.system_generated.value,
                status=CategoryStatus.confirmed.value, human_modified=False,
            ))
        for eid, staff, cat, dept in (
            (E_SALES, "D-1", CAT_SALES, "Sales"),
            (E_OPS, "D-2", CAT_OPS, "Ops"),
        ):
            s.add(Employee(
                id=eid, client_id=CLIENT_ID, policy_year_id=PY_ID,
                staff_id=staff, employee_name=f"Parent {staff}",
                attribute_values={"department": dept},
                derived_attribute_values={},
                matched_categories=[{"category_id": cat, "product_code": "MED",
                                     "method": "rule", "confidence": 1.0}],
                source="csv_import", status="active",
            ))
        s.flush()
        rows = (
            (D_SPOUSE, E_SALES, "active", "staff_id", {
                "dependant_name": "Spouse One", "relationship": "Spouse",
                "date_of_birth": "1988-04-01", "employee_staff_id": "D-1",
                "employee_name": "Parent D-1", "dependant_id_no": "S8800001A",
            }),
            (D_CHILD, E_SALES, "active", "staff_id", {
                "dependant_name": "Child One", "relationship": "Son",
                "date_of_birth": "2015-04-01", "employee_staff_id": "D-1",
                "employee_name": "Parent D-1",
            }),
            (D_OPS_KID, E_OPS, "active", "name", {
                "dependant_name": "Child Two", "relationship": "Daughter",
                "date_of_birth": "2010-04-01", "employee_staff_id": "D-2",
                "employee_name": "Parent D-2",
            }),
            (D_UNLINKED, None, "active", "unlinked", {
                "dependant_name": "Orphan Row", "relationship": "Child",
                "date_of_birth": "2012-04-01",
            }),
            (D_PENDING, E_SALES, "pending_approval", "member_portal", {
                "dependant_name": "Pending Kid", "relationship": "child",
                "date_of_birth": "2019-04-01",
            }),
            (D_NAMELESS, E_OPS, "active", "staff_id", {
                # No dependant_name — only the PARENT's name, which is a real
                # roster shape and the reason DEP_NAME_KEYS exists.
                "employee_name": "Parent D-2", "relationship": "Father",
                "date_of_birth": "1955-04-01", "employee_staff_id": "D-2",
            }),
        )
        for did, emp_id, status, method, attrs in rows:
            s.add(Dependant(
                id=did, client_id=CLIENT_ID, policy_year_id=PY_ID,
                employee_id=emp_id, attribute_values=attrs,
                link_method=method, status=status,
                national_id_normalized=attrs.get("dependant_id_no"),
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


def _list(client: TestClient, query: dict | None = None, **kw) -> dict:
    res = client.post(
        f"/api/v1/policy-years/{PY_ID}/dependant-query/list",
        json={"query": query or {}, **kw},
    )
    assert res.status_code == 200, res.text
    return res.json()


def _names(body: dict) -> set[str]:
    return {
        i["attribute_values"].get("dependant_name", "") for i in body["items"]
    }


# ── Status ──────────────────────────────────────────────────────────────────


def test_default_view_is_active_only(client: TestClient) -> None:
    body = _list(client)
    assert body["total"] == 5  # the pending self-add is out
    assert "Pending Kid" not in _names(body)


def test_pending_self_adds_are_reachable(client: TestClient) -> None:
    """They currently exist ONLY in the approvals card — a broker who wants to
    see them beside the rest of the roster has no way to."""
    body = _list(client, {"statuses": ["pending_approval"]})
    assert _names(body) == {"Pending Kid"}


# ── Relationship / role ─────────────────────────────────────────────────────


def test_role_filter_uses_the_shared_classifier(client: TestClient) -> None:
    """Son/Daughter/child all classify as "child"; a parent falls to "other",
    which is exactly where roster data problems collect."""
    assert _names(_list(client, {"roles": ["spouse"]})) == {"Spouse One"}
    assert _names(_list(client, {"roles": ["child"]})) == {
        "Child One", "Child Two", "Orphan Row",
    }
    assert _list(client, {"roles": ["other"]})["total"] == 1  # the Father row


def test_relationship_filter_matches_raw_roster_wording(client: TestClient) -> None:
    assert _names(_list(client, {"relationships": ["Son"]})) == {"Child One"}
    # Case-insensitive, because the roster's wording is whatever was typed.
    assert _names(_list(client, {"relationships": ["daughter"]})) == {"Child Two"}


# ── Link state ──────────────────────────────────────────────────────────────


def test_link_state_and_method(client: TestClient) -> None:
    assert _names(_list(client, {"link_state": "unlinked"})) == {"Orphan Row"}
    assert _list(client, {"link_state": "linked"})["total"] == 4
    # A name-matched link is weaker than a staff-id one, and worth isolating.
    assert _names(_list(client, {"link_methods": ["name"]})) == {"Child Two"}


# ── Age ─────────────────────────────────────────────────────────────────────


def test_age_filter_is_anb_at_year_start(client: TestClient) -> None:
    """Child One is born 2015; ANB at 2030-01-01 is 15. The window must agree
    with the eligibility rules that will actually drop a child."""
    assert _names(_list(client, {"age": {"min": 15, "max": 15}})) == {"Child One"}
    assert _list(client, {"age": {"min": 40}})["total"] == 2  # spouse + father


# ── Sponsoring employee (the nested MemberFilters) ──────────────────────────


def test_employee_filter_scopes_dependants_by_cohort(client: TestClient) -> None:
    """A dependant's category is its employee's category — this is what makes
    "category filter" mean anything on the Dependants tab."""
    body = _list(client, {"employee": {"category_ids": [CAT_SALES]}})
    assert _names(body) == {"Spouse One", "Child One"}


def test_employee_filter_reads_roster_attributes(client: TestClient) -> None:
    body = _list(client, {
        "employee": {"attributes": [{"key": "department", "values": ["Ops"]}]}
    })
    assert body["total"] == 2  # Child Two + the Father row


def test_employee_filter_necessarily_drops_unlinked_rows(client: TestClient) -> None:
    """There is no employee to test them against — so this must not silently
    keep them, and must not silently keep them out when no filter is set."""
    assert "Orphan Row" in _names(_list(client))
    body = _list(client, {"employee": {"attributes": [
        {"key": "department", "values": ["Sales", "Ops"]}
    ]}})
    assert "Orphan Row" not in _names(body)


# ── Search ──────────────────────────────────────────────────────────────────


def test_search_covers_name_nric_and_the_sponsor(client: TestClient) -> None:
    assert _names(_list(client, {"q": "Child One"})) == {"Child One"}
    assert _names(_list(client, {"q": "s88-00001a"})) == {"Spouse One"}
    assert _list(client, {"q": "D-1"})["total"] == 2  # by sponsoring staff id


def test_a_row_without_its_own_name_never_matches_the_parents(
    client: TestClient,
) -> None:
    """``NAME_KEYS`` includes ``employee_name`` and dependant rows carry it, so
    reading a dependant's name through it reports the PARENT's name — and would
    make every nameless row match a search for the parent."""
    body = _list(client, {"q": "Parent D-2"})
    assert body["total"] == 2  # matched via employee_name hint, not as a name
    from app.services.dependant_query import dependant_name

    assert dependant_name({"employee_name": "Parent D-2"}) == ""


# ── Facets ──────────────────────────────────────────────────────────────────


def test_facets_serve_the_vocabulary_with_counts(client: TestClient) -> None:
    res = client.get(f"/api/v1/policy-years/{PY_ID}/dependant-facets")
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["active_total"] == 5 and body["all_statuses_total"] == 6
    assert body["linked"] == 4 and body["unlinked"] == 1
    rel = {r["value"]: r["count"] for r in body["relationships"]}
    assert rel["Spouse"] == 1 and rel["Son"] == 1
    roles = {r["value"]: r["count"] for r in body["roles"]}
    assert roles["child"] == 3 and roles["other"] == 1


def test_status_facet_spans_every_status(client: TestClient) -> None:
    """It is the control that WIDENS the population, so counting it over the
    active-only default would hide the pending self-adds it exists to find."""
    res = client.get(f"/api/v1/policy-years/{PY_ID}/dependant-facets")
    statuses = {s["value"]: s["count"] for s in res.json()["statuses"]}
    assert statuses["active"] == 5 and statuses["pending_approval"] == 1


# ── Paging ──────────────────────────────────────────────────────────────────


def test_paging_groups_a_family_together(client: TestClient) -> None:
    """Sorted by sponsor then dependant name, so a household reads as one."""
    body = _list(client, None, offset=0, limit=3)
    assert body["total"] == 5 and len(body["items"]) == 3
    # D-1's household first, then D-2's (whose nameless row sorts ahead of the
    # named one within the family).
    assert [i["attribute_values"].get("dependant_name") for i in body["items"]] == [
        "Child One", "Spouse One", None,
    ]


def test_unlinked_rows_sort_last_not_first(client: TestClient) -> None:
    """They have no sponsor, so a naive sort puts every exception at the top of
    every page and buries the roster. "Unlinked" is a filter for a reason."""
    body = _list(client)
    assert body["items"][-1]["attribute_values"]["dependant_name"] == "Orphan Row"
