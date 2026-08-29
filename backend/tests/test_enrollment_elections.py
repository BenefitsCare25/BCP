"""Enrollment elections, leave, confirm + reverse finalization (Phase 3 & 4)."""
from __future__ import annotations

import os
from pathlib import Path

import pytest

TEST_DB = Path(__file__).parent / "_test_enrollment_elections.db"
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
    EmployeePlanOverride,
    Enrollment,
    EnrollmentElection,
    EnrollmentWindow,
    FlexPricing,
    LeaveElection,
    LeavePolicy,
    Plan,
    PolicyYear,
    Product,
)
from app.models.category import CategoryStatus, SourceKind  # noqa: E402
from app.models.policy_year import PolicyYearStatus  # noqa: E402
from app.services.coverage_resolver import load_overrides  # noqa: E402
from scripts.seed_demo import seed  # noqa: E402

CLIENT_ID = "00000000-0000-0000-0000-00000000a000"
PY_ID = "00000000-0000-0000-0000-00000000a001"
PROD_ID = "00000000-0000-0000-0000-00000000a002"
CAT_ID = "00000000-0000-0000-0000-00000000a003"
EMP1 = "00000000-0000-0000-0000-00000000a004"
EMP2 = "00000000-0000-0000-0000-00000000a005"


def _user() -> CurrentUser:
    return CurrentUser(
        user_id="00000000-0000-0000-0000-00000000a0ff",
        broker_firm_id=DEMO_BROKER_FIRM_ID, client_id=CLIENT_ID, role="broker_admin",
    )


@pytest.fixture(scope="module", autouse=True)
def _setup_db():
    if TEST_DB.exists():
        TEST_DB.unlink()
    Base.metadata.create_all(bind=engine)
    seed()
    with SessionLocal() as s:
        s.add(Client(id=CLIENT_ID, name="Elect Co", broker_firm_id=DEMO_BROKER_FIRM_ID))
        s.flush()
        s.add(PolicyYear(
            id=PY_ID, client_id=CLIENT_ID, year=2029,
            start_date=date(2029, 1, 1), end_date=date(2029, 12, 31),
            status=PolicyYearStatus.draft,
        ))
        s.flush()
        s.add(Product(
            id=PROD_ID, client_id=CLIENT_ID, code="MED",
            display_name="Medical", insurer="ACME", has_dependants=True,
        ))
        s.flush()
        for code, name in (("SILVER", "Silver"), ("GOLD", "Gold")):
            s.add(Plan(
                id=f"a-plan-{code}", product_id=PROD_ID, policy_year_id=PY_ID,
                code=code, display_name=name, status="confirmed",
            ))
        s.add(Category(
            id=CAT_ID, policy_year_id=PY_ID, product_id=PROD_ID, priority=1,
            display_name="All staff", raw_description="All staff",
            plan_assignments={"plan_code": "SILVER"},
            source=SourceKind.system_generated.value,
            status=CategoryStatus.confirmed.value, human_modified=False,
        ))
        for eid, staff in ((EMP1, "E-1"), (EMP2, "E-2")):
            s.add(Employee(
                id=eid, client_id=CLIENT_ID, policy_year_id=PY_ID,
                staff_id=staff, employee_name=f"Emp {staff}",
                attribute_values={}, derived_attribute_values={},
                matched_categories=[{"category_id": CAT_ID, "product_code": "MED",
                                     "method": "rule", "confidence": 1.0}],
                source="csv_import", status="active",
            ))
        s.add(LeavePolicy(
            id="a-leave", policy_year_id=PY_ID, client_id=CLIENT_ID,
            allow_buy=True, allow_sell=True, min_buy_days=0, max_buy_days=5,
            min_sell_days=0, max_sell_days=5, increment_days=1.0,
        ))
        s.commit()
    yield
    engine.dispose()
    if TEST_DB.exists():
        TEST_DB.unlink()


@pytest.fixture(autouse=True)
def _reset_enrollment_state():
    """Each test starts with no windows/enrollments/overrides."""
    yield
    with SessionLocal() as s:
        for model in (LeaveElection, EnrollmentElection, Enrollment,
                      EmployeePlanOverride, EnrollmentWindow):
            s.query(model).delete()
        s.commit()


@pytest.fixture
def client() -> TestClient:
    app.dependency_overrides[get_current_user] = _user
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.pop(get_current_user, None)


def _make_window(
    client: TestClient, *, runtime_flex: bool = False, **over
) -> str:
    body = {
        "name": "OE", "window_type": "open",
        "opens_at": "2020-01-01T00:00:00Z", "closes_at": "2035-01-01T00:00:00Z",
        "allow_leave": True,
    }
    body.update(over)
    wid = client.post(
        f"/api/v1/policy-years/{PY_ID}/enrollment-windows", json=body
    ).json()["id"]
    opened = client.post(f"/api/v1/enrollment-windows/{wid}/open")
    assert opened.status_code == 200, opened.text
    if runtime_flex:
        # Isolate option/snapshot pricing from the draft-opening readiness gate:
        # these fixtures predate scheme provisioning and exercise the already-
        # open election path directly.
        with SessionLocal() as s:
            s.get(EnrollmentWindow, wid).uses_flex = True
            employee = s.get(Employee, EMP1)
            employee.flex_wallet_amount = 10_000.0
            employee.flex_currency = "SGD"
            s.commit()
    return wid


def _enrollment_id(client: TestClient, wid: str, staff: str) -> str:
    roster = client.get(f"/api/v1/enrollment-windows/{wid}/enrollments").json()
    return next(i["id"] for i in roster["items"] if i["staff_id"] == staff)


def test_open_creates_enrollments_with_baseline(client: TestClient) -> None:
    wid = _make_window(client)
    roster = client.get(f"/api/v1/enrollment-windows/{wid}/enrollments").json()
    assert roster["total"] == 2
    eid = _enrollment_id(client, wid, "E-1")
    detail = client.get(f"/api/v1/enrollments/{eid}").json()
    assert detail["baseline_snapshot"]["products"]["MED"]["plan_code"] == "SILVER"


def test_upgrade_leave_submit_confirm(client: TestClient) -> None:
    wid = _make_window(client)
    eid = _enrollment_id(client, wid, "E-1")
    res = client.put(
        f"/api/v1/enrollments/{eid}/elections",
        json={"elections": [{"product_code": "MED", "plan_code": "GOLD"}]},
    )
    assert res.status_code == 200, res.text
    # Direction is a heuristic (no stored tier order); it must register as a change.
    assert res.json()["elections"][0]["action"] in ("upgrade", "downgrade")

    lv = client.put(f"/api/v1/enrollments/{eid}/leave", json={"action": "buy", "days": 2})
    assert lv.status_code == 200 and lv.json()["leave"]["days"] == 2

    assert client.post(f"/api/v1/enrollments/{eid}/submit").json()["status"] == "submitted"
    conf = client.post(f"/api/v1/enrollments/{eid}/confirm")
    assert conf.status_code == 200 and conf.json()["status"] == "confirmed"

    with SessionLocal() as s:
        ovs = load_overrides(s, PY_ID, [EMP1])
        assert ovs[(EMP1, PROD_ID)].plan_code == "GOLD"


def test_compulsory_dependants_are_auto_priced_when_payload_omits_ids(
    client: TestClient,
) -> None:
    """Compulsory controls selection, not funding. Even when the window does
    not allow dependant changes and the client sends no IDs, every active
    eligible dependant is persisted and charged to the employee wallet."""
    with SessionLocal() as s:
        category = s.get(Category, CAT_ID)
        assert category is not None
        previous_model = category.participation_model
        previous_detail = category.participation_detail
        category.participation_model = "compulsory"
        category.participation_detail = {
            "employee": "compulsory",
            "dependant": "voluntary",
            "direction": None,
        }
        s.add(
            Dependant(
                id=DEP1,
                client_id=CLIENT_ID,
                policy_year_id=PY_ID,
                employee_id=EMP1,
                attribute_values={"relationship": "Spouse"},
                status="active",
            )
        )
        s.add(
            FlexPricing(
                id="compulsory-dependant-pricing",
                policy_year_id=PY_ID,
                client_id=CLIENT_ID,
                pricing={
                    "products": {
                        PROD_ID: {
                            "dependant": {
                                "participation": {
                                    f"{CAT_ID}::SILVER": "compulsory"
                                },
                                "modes": {f"{CAT_ID}::SILVER": "per_pax"},
                                "per_pax": {
                                    f"{CAT_ID}::SILVER": {"flat": 25}
                                },
                            }
                        }
                    }
                },
            )
        )
        s.commit()

    try:
        wid = _make_window(
            client, runtime_flex=True, allow_dependant_changes=False
        )
        eid = _enrollment_id(client, wid, "E-1")
        response = client.put(
            f"/api/v1/enrollments/{eid}/elections",
            json={"elections": [{"product_code": "MED", "plan_code": "SILVER"}]},
        )
        assert response.status_code == 200, response.text
        election = response.json()["elections"][0]
        assert election["covered_dependant_ids"] == [DEP1]
        assert election["flex_price_tag"] == 25.0
    finally:
        with SessionLocal() as s:
            s.query(FlexPricing).filter(
                FlexPricing.id == "compulsory-dependant-pricing"
            ).delete()
            s.query(Dependant).filter(Dependant.id == DEP1).delete()
            category = s.get(Category, CAT_ID)
            assert category is not None
            category.participation_model = previous_model
            category.participation_detail = previous_detail
            s.commit()


def test_removed_plan_dependant_cover_ignores_submitted_dependants(
    client: TestClient,
) -> None:
    """A plan-level `none` override removes cover even when the source category
    was compulsory and a stale client still submits dependant IDs."""
    with SessionLocal() as s:
        category = s.get(Category, CAT_ID)
        assert category is not None
        previous_detail = category.participation_detail
        category.participation_detail = {
            "employee": "compulsory",
            "dependant": "compulsory",
        }
        s.add(
            Dependant(
                id=DEP1,
                client_id=CLIENT_ID,
                policy_year_id=PY_ID,
                employee_id=EMP1,
                attribute_values={"relationship": "Spouse"},
                status="active",
            )
        )
        s.add(
            FlexPricing(
                id="removed-dependant-pricing",
                policy_year_id=PY_ID,
                client_id=CLIENT_ID,
                pricing={
                    "products": {
                        PROD_ID: {
                            "dependant": {
                                "participation": {
                                    f"{CAT_ID}::SILVER": "none"
                                },
                                "modes": {f"{CAT_ID}::SILVER": "per_pax"},
                                "per_pax": {
                                    f"{CAT_ID}::SILVER": {"flat": 25}
                                },
                            }
                        }
                    }
                },
            )
        )
        s.commit()

    try:
        wid = _make_window(client, allow_dependant_changes=True)
        eid = _enrollment_id(client, wid, "E-1")
        response = client.put(
            f"/api/v1/enrollments/{eid}/elections",
            json={
                "elections": [
                    {
                        "product_code": "MED",
                        "plan_code": "SILVER",
                        "covered_dependant_ids": [DEP1],
                    }
                ]
            },
        )
        assert response.status_code == 200, response.text
        election = response.json()["elections"][0]
        assert election["covered_dependant_ids"] is None
    finally:
        with SessionLocal() as s:
            s.query(FlexPricing).filter(
                FlexPricing.id == "removed-dependant-pricing"
            ).delete()
            s.query(Dependant).filter(Dependant.id == DEP1).delete()
            category = s.get(Category, CAT_ID)
            assert category is not None
            category.participation_detail = previous_detail
            s.commit()


def test_reopen_confirmed_enrollment_allows_replan(client: TestClient) -> None:
    wid = _make_window(client)
    eid = _enrollment_id(client, wid, "E-1")
    client.put(
        f"/api/v1/enrollments/{eid}/elections",
        json={"elections": [{"product_code": "MED", "plan_code": "GOLD"}]},
    )
    client.post(f"/api/v1/enrollments/{eid}/submit")
    assert client.post(f"/api/v1/enrollments/{eid}/confirm").json()["status"] == "confirmed"

    # Reopen flips the confirmed enrollment back to editable; the confirmed
    # override stays until re-confirm.
    re = client.post(f"/api/v1/enrollments/{eid}/reopen")
    assert re.status_code == 200 and re.json()["status"] == "in_progress"
    with SessionLocal() as s:
        assert load_overrides(s, PY_ID, [EMP1])[(EMP1, PROD_ID)].plan_code == "GOLD"

    # Change the plan and re-confirm → the override re-projects. SILVER is the
    # cohort default, so the sparse model drops the override entirely.
    client.put(
        f"/api/v1/enrollments/{eid}/elections",
        json={"elections": [{"product_code": "MED", "plan_code": "SILVER"}]},
    )
    client.post(f"/api/v1/enrollments/{eid}/submit")
    assert client.post(f"/api/v1/enrollments/{eid}/confirm").json()["status"] == "confirmed"
    with SessionLocal() as s:
        assert (EMP1, PROD_ID) not in load_overrides(s, PY_ID, [EMP1])

    # Reopen applies only to a confirmed enrollment; a second reopen on the now
    # in_progress enrollment is rejected.
    assert client.post(f"/api/v1/enrollments/{eid}/reopen").status_code == 200
    assert client.post(f"/api/v1/enrollments/{eid}/reopen").status_code == 409


def test_decline_projects_declined_override(client: TestClient) -> None:
    wid = _make_window(client)
    eid = _enrollment_id(client, wid, "E-1")
    put = client.put(
        f"/api/v1/enrollments/{eid}/elections",
        json={"elections": [{"product_code": "MED", "declined": True}]},
    )
    assert put.status_code == 200, put.text
    assert put.json()["elections"][0]["action"] == "decline"
    # Confirm requires a submitted enrollment (see confirm_enrollment's gate).
    assert client.post(f"/api/v1/enrollments/{eid}/submit").status_code == 200
    conf = client.post(f"/api/v1/enrollments/{eid}/confirm")
    assert conf.status_code == 200, conf.text
    with SessionLocal() as s:
        assert load_overrides(s, PY_ID, [EMP1])[(EMP1, PROD_ID)].declined is True


def test_invalid_plan_422(client: TestClient) -> None:
    wid = _make_window(client)
    eid = _enrollment_id(client, wid, "E-1")
    res = client.put(
        f"/api/v1/enrollments/{eid}/elections",
        json={"elections": [{"product_code": "MED", "plan_code": "PLATINUM"}]},
    )
    assert res.status_code == 422


def test_leave_out_of_bounds_422(client: TestClient) -> None:
    wid = _make_window(client)
    eid = _enrollment_id(client, wid, "E-1")
    res = client.put(f"/api/v1/enrollments/{eid}/leave", json={"action": "buy", "days": 99})
    assert res.status_code == 422


def test_closed_window_rejects_edits(client: TestClient) -> None:
    wid = _make_window(client)
    eid = _enrollment_id(client, wid, "E-1")
    client.post(f"/api/v1/enrollment-windows/{wid}/close")
    res = client.put(
        f"/api/v1/enrollments/{eid}/elections",
        json={"elections": [{"product_code": "MED", "plan_code": "GOLD"}]},
    )
    assert res.status_code == 409


def test_reverse_keep_current_leaves_default(client: TestClient) -> None:
    wid = _make_window(client, default_behavior="deemed_keep_current")
    summary = client.post(f"/api/v1/enrollment-windows/{wid}/close").json()
    assert summary["deemed_kept"] == 2
    with SessionLocal() as s:
        assert load_overrides(s, PY_ID, [EMP1, EMP2]) == {}


def test_reverse_decline_writes_declined_overrides(client: TestClient) -> None:
    wid = _make_window(client, default_behavior="deemed_decline")
    summary = client.post(f"/api/v1/enrollment-windows/{wid}/close").json()
    assert summary["deemed_declined"] == 2
    with SessionLocal() as s:
        ovs = load_overrides(s, PY_ID, [EMP1, EMP2])
        assert ovs[(EMP1, PROD_ID)].declined and ovs[(EMP2, PROD_ID)].declined


def test_submitted_enrollment_confirmed_on_close(client: TestClient) -> None:
    wid = _make_window(client, default_behavior="deemed_keep_current")
    eid = _enrollment_id(client, wid, "E-1")
    client.put(
        f"/api/v1/enrollments/{eid}/elections",
        json={"elections": [{"product_code": "MED", "plan_code": "GOLD"}]},
    )
    client.post(f"/api/v1/enrollments/{eid}/submit")
    summary = client.post(f"/api/v1/enrollment-windows/{wid}/close").json()
    assert summary["confirmed"] == 1 and summary["deemed_kept"] == 1
    with SessionLocal() as s:
        assert load_overrides(s, PY_ID, [EMP1])[(EMP1, PROD_ID)].plan_code == "GOLD"


# ── Freestanding dependant option levels (rule-4 choices) ────────────────────

DEP1 = "00000000-0000-0000-0000-00000000a006"
OPT_S20 = "a-cat-dep-s20"
OPT_S40 = "a-cat-dep-s40"


@pytest.fixture
def _dependant_levels():
    """A spouse dependant for EMP1 + two freestanding Spouse option levels on MED
    (flat per-1000 pricing so amounts are deterministic without a DOB)."""
    from app.models import Dependant

    with SessionLocal() as s:
        s.add(Dependant(
            id=DEP1, client_id=CLIENT_ID, policy_year_id=PY_ID, employee_id=EMP1,
            attribute_values={"relationship": "Spouse"}, status="active",
        ))
        for cid, plan, si in ((OPT_S20, "D1", 20000.0), (OPT_S40, "D2", 40000.0)):
            s.add(Category(
                id=cid, policy_year_id=PY_ID, product_id=PROD_ID, priority=9,
                display_name="Spouse", raw_description="Spouse",
                participation_model="voluntary",
                participation_detail={"employee": None, "dependant": "voluntary",
                                      "direction": None},
                plan_assignments={"plan_code": plan, "sum_insured": si,
                                  "premium_rate": 1.0, "rate_basis": "per_1000_si",
                                  "member_scope": "dependant"},
                source=SourceKind.system_generated.value,
                status=CategoryStatus.confirmed.value, human_modified=False,
            ))
        s.commit()
    yield
    with SessionLocal() as s:
        from app.models import Dependant

        s.query(Dependant).filter(Dependant.id == DEP1).delete()
        s.query(Category).filter(Category.id.in_([OPT_S20, OPT_S40])).delete()
        s.commit()


def test_options_expose_choices_and_election_stores_priced_level(
    client: TestClient, _dependant_levels
) -> None:
    wid = _make_window(client, runtime_flex=True, allow_dependant_changes=True)
    eid = _enrollment_id(client, wid, "E-1")
    # Options surface the electable levels with per-dependant amounts.
    opts = client.get(f"/api/v1/enrollments/{eid}/options").json()
    med = next(p for p in opts["products"] if p["product_code"] == "MED")
    assert med["dependant"]["mode"] == "slip_options"
    roles = {r["role"]: r["choices"] for r in med["dependant"]["option_choices"]}
    assert [c["sum_insured"] for c in roles["spouse"]] == [20000.0, 40000.0]
    lvl40 = next(c for c in roles["spouse"] if c["sum_insured"] == 40000.0)
    assert lvl40["amount"] == 40.0  # 40k/1000 x 1.0, flat — no age needed
    assert lvl40["amounts_by_dependant"] == {DEP1: 40.0}
    # Electing the S$40k level covers the spouse at that level's price.
    put = client.put(
        f"/api/v1/enrollments/{eid}/elections",
        json={"elections": [{
            "product_code": "MED", "plan_code": "SILVER",
            "covered_dependant_ids": [DEP1],
            "dependant_option_ids": {"spouse": lvl40["category_id"]},
        }]},
    )
    assert put.status_code == 200, put.text
    el = put.json()["elections"][0]
    assert el["dependant_option_ids"] == {"spouse": lvl40["category_id"]}
    assert el["flex_price_tag"] == 40.0  # no employee slip premium; spouse level
    # Confirm projects the elected level onto the override.
    client.post(f"/api/v1/enrollments/{eid}/submit")
    assert client.post(f"/api/v1/enrollments/{eid}/confirm").status_code == 200
    with SessionLocal() as s:
        ov = load_overrides(s, PY_ID, [EMP1])[(EMP1, PROD_ID)]
        assert ov.dependant_option_ids == {"spouse": lvl40["category_id"]}
        assert ov.flex_price_tag == 40.0


def test_covered_dependants_without_elected_level_are_unpriced(
    client: TestClient, _dependant_levels
) -> None:
    wid = _make_window(client, runtime_flex=True, allow_dependant_changes=True)
    eid = _enrollment_id(client, wid, "E-1")
    put = client.put(
        f"/api/v1/enrollments/{eid}/elections",
        json={"elections": [{
            "product_code": "MED", "plan_code": "SILVER",
            "covered_dependant_ids": [DEP1],
        }]},
    )
    assert put.status_code == 200, put.text
    # No elected level -> the whole tag is unpriced (guard surfaces it).
    assert put.json()["elections"][0]["flex_price_tag"] is None


def test_no_cover_election_clears_submitted_dependants_and_option_levels(
    client: TestClient, _dependant_levels
) -> None:
    from app.services.cohort_tiers import tier_key

    with SessionLocal() as s:
        s.add(FlexPricing(
            id="no-cover-election-pricing",
            policy_year_id=PY_ID,
            client_id=CLIENT_ID,
            pricing={"products": {PROD_ID: {"dependant": {
                "participation": {tier_key(CAT_ID, "SILVER"): "none"},
            }}}},
        ))
        s.commit()
    try:
        wid = _make_window(
            client, runtime_flex=True, allow_dependant_changes=True
        )
        eid = _enrollment_id(client, wid, "E-1")
        put = client.put(
            f"/api/v1/enrollments/{eid}/elections",
            json={"elections": [{
                "product_code": "MED", "plan_code": "SILVER",
                "covered_dependant_ids": [DEP1],
                "dependant_option_ids": {"spouse": OPT_S40},
            }]},
        )
        assert put.status_code == 200, put.text
        election = put.json()["elections"][0]
        assert election["covered_dependant_ids"] is None
        assert election["dependant_option_ids"] is None
    finally:
        with SessionLocal() as s:
            s.query(FlexPricing).filter(
                FlexPricing.id == "no-cover-election-pricing"
            ).delete()
            s.commit()


def test_invalid_dependant_option_level_422(
    client: TestClient, _dependant_levels
) -> None:
    wid = _make_window(client, runtime_flex=True, allow_dependant_changes=True)
    eid = _enrollment_id(client, wid, "E-1")
    # An employee category id is not a dependant option level.
    res = client.put(
        f"/api/v1/enrollments/{eid}/elections",
        json={"elections": [{
            "product_code": "MED", "plan_code": "SILVER",
            "covered_dependant_ids": [DEP1],
            "dependant_option_ids": {"spouse": CAT_ID},
        }]},
    )
    assert res.status_code == 422
    # A spouse level stored under 'child' is rejected too.
    res = client.put(
        f"/api/v1/enrollments/{eid}/elections",
        json={"elections": [{
            "product_code": "MED", "plan_code": "SILVER",
            "dependant_option_ids": {"child": OPT_S40},
        }]},
    )
    assert res.status_code == 422


def test_age_ineligible_dependant_excluded_from_election_pricing(
    client: TestClient, _dependant_levels
) -> None:
    """The election snapshot applies the product's dependant eligibility windows
    exactly like every recompute surface: an over-age dependant is EXCLUDED
    (not priced, and not unpriced-blocking) — here a 30-year-old 'child' is
    outside the default 0-25 window, so covering them costs nothing and the
    tag doesn't demand an elected level for them."""
    from app.models import Dependant

    OVERAGE = "00000000-0000-0000-0000-00000000a007"
    with SessionLocal() as s:
        s.add(Dependant(
            id=OVERAGE, client_id=CLIENT_ID, policy_year_id=PY_ID, employee_id=EMP1,
            attribute_values={"relationship": "Child", "dob": "1996-01-01"},
            status="active",
        ))
        s.commit()
    try:
        wid = _make_window(
            client, runtime_flex=True, allow_dependant_changes=True
        )
        eid = _enrollment_id(client, wid, "E-1")
        put = client.put(
            f"/api/v1/enrollments/{eid}/elections",
            json={"elections": [{
                "product_code": "MED", "plan_code": "SILVER",
                "covered_dependant_ids": [OVERAGE],
            }]},
        )
        assert put.status_code == 200, put.text
        # Excluded by the age window -> $0 draw, NOT None (no level demanded).
        assert put.json()["elections"][0]["flex_price_tag"] == 0.0
    finally:
        with SessionLocal() as s:
            s.query(Dependant).filter(Dependant.id == OVERAGE).delete()
            s.commit()


def test_manual_override_sets_validates_and_prices_dependant_levels(
    client: TestClient, _dependant_levels
) -> None:
    """The manual-admin override path handles elected dependant levels like the
    other writers: a bad id 422s, a valid one persists WITH a repriced flex
    tag, an omitted field leaves the stored value untouched, and declining
    clears it."""
    url = f"/api/v1/employees/{EMP1}/plan-overrides/MED"
    # An employee category id is not an electable level -> 422.
    res = client.put(url, json={
        "plan_code": "SILVER", "covered_dependant_ids": [DEP1],
        "dependant_option_ids": {"spouse": CAT_ID},
    })
    assert res.status_code == 422, res.text
    # A real level persists and the tag is repriced (spouse 40k x 1.0/1000).
    res = client.put(url, json={
        "plan_code": "SILVER", "covered_dependant_ids": [DEP1],
        "dependant_option_ids": {"spouse": OPT_S40},
    })
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["dependant_option_ids"] == {"spouse": OPT_S40}
    with SessionLocal() as s:
        ov = load_overrides(s, PY_ID, [EMP1])[(EMP1, PROD_ID)]
        assert ov.dependant_option_ids == {"spouse": OPT_S40}
        assert ov.flex_price_tag == 40.0
    # Omitting the field on a later edit keeps the stored level (and reprices).
    res = client.put(url, json={"plan_code": "GOLD", "covered_dependant_ids": [DEP1]})
    assert res.status_code == 200, res.text
    assert res.json()["dependant_option_ids"] == {"spouse": OPT_S40}
    # Declining clears the level with the rest of the coverage.
    res = client.put(url, json={"declined": True})
    assert res.status_code == 200, res.text
    assert res.json()["covered_dependant_ids"] is None
    assert res.json()["dependant_option_ids"] is None


def test_manual_override_resolves_sibling_no_cover_tier_and_clears_dependants(
    client: TestClient, _dependant_levels
) -> None:
    from app.services.cohort_tiers import tier_key

    sibling = "a-cat-gold-option"
    target_key = tier_key(sibling, "GOLD")
    with SessionLocal() as s:
        s.add(Category(
            id=sibling, policy_year_id=PY_ID, product_id=PROD_ID, priority=2,
            display_name="All staff (Option 2)",
            raw_description="All staff (Option 2)",
            participation_model="voluntary",
            participation_detail={"employee": "voluntary", "dependant": "voluntary"},
            plan_assignments={"plan_code": "GOLD"},
            source=SourceKind.system_generated.value,
            status=CategoryStatus.confirmed.value, human_modified=False,
        ))
        s.add(FlexPricing(
            id="manual-no-cover-pricing",
            policy_year_id=PY_ID,
            client_id=CLIENT_ID,
            pricing={"products": {PROD_ID: {"dependant": {
                "participation": {target_key: "none"},
            }}}},
        ))
        s.commit()
    url = f"/api/v1/employees/{EMP1}/plan-overrides/MED"
    try:
        first = client.put(url, json={
            "plan_code": "SILVER", "covered_dependant_ids": [DEP1],
            "dependant_option_ids": {"spouse": OPT_S40},
        })
        assert first.status_code == 200, first.text
        moved = client.put(url, json={
            "plan_code": "GOLD", "covered_dependant_ids": [DEP1],
        })
        assert moved.status_code == 200, moved.text
        body = moved.json()
        assert body["covered_dependant_ids"] is None
        assert body["dependant_option_ids"] is None
        with SessionLocal() as s:
            ov = load_overrides(s, PY_ID, [EMP1])[(EMP1, PROD_ID)]
            assert ov.tier_category_id == sibling
    finally:
        with SessionLocal() as s:
            s.query(EmployeePlanOverride).delete()
            s.query(FlexPricing).filter(
                FlexPricing.id == "manual-no-cover-pricing"
            ).delete()
            s.query(Category).filter(Category.id == sibling).delete()
            s.commit()
