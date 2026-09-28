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


# ── Period lifecycle past the deadline ───────────────────────────────────────


def _expire(wid: str) -> None:
    """Move a window's deadline into the past while it stays `open` — the state
    every period is in between its deadline and the broker pressing Close."""
    from datetime import UTC, datetime, timedelta

    with SessionLocal() as s:
        s.get(EnrollmentWindow, wid).closes_at = datetime.now(UTC) - timedelta(days=1)
        s.commit()


def test_close_after_deadline_confirms_submitted(client: TestClient) -> None:
    wid = _make_window(client, default_behavior="deemed_keep_current")
    eid = _enrollment_id(client, wid, "E-1")
    client.put(
        f"/api/v1/enrollments/{eid}/elections",
        json={"elections": [{"product_code": "MED", "plan_code": "GOLD"}]},
    )
    assert client.post(f"/api/v1/enrollments/{eid}/submit").status_code == 200
    _expire(wid)
    res = client.post(f"/api/v1/enrollment-windows/{wid}/close")
    assert res.status_code == 200, res.text
    assert res.json()["confirmed"] == 1
    with SessionLocal() as s:
        assert load_overrides(s, PY_ID, [EMP1])[(EMP1, PROD_ID)].plan_code == "GOLD"


def _save_upgrade(client: TestClient, eid: str) -> None:
    res = client.put(
        f"/api/v1/enrollments/{eid}/elections",
        json={"elections": [{"product_code": "MED", "plan_code": "GOLD"}]},
    )
    assert res.status_code == 200, res.text


def test_confirm_after_deadline_is_review_not_edit(client: TestClient) -> None:
    wid = _make_window(client)
    eid = _enrollment_id(client, wid, "E-1")
    _save_upgrade(client, eid)
    client.post(f"/api/v1/enrollments/{eid}/submit")
    _expire(wid)
    assert client.get(f"/api/v1/enrollment-windows/{wid}").json()["phase"] == "overdue"
    # Members/edits are locked out past the deadline …
    edit = client.put(
        f"/api/v1/enrollments/{eid}/elections",
        json={"elections": [{"product_code": "MED", "plan_code": "SILVER"}]},
    )
    assert edit.status_code == 409
    # … but the broker can still work through the submitted queue.
    res = client.post(f"/api/v1/enrollments/{eid}/confirm")
    assert res.status_code == 200, res.text
    assert res.json()["status"] == "confirmed"


def test_close_preview_counts_saved_but_unsent(client: TestClient) -> None:
    wid = _make_window(client)
    _save_upgrade(client, _enrollment_id(client, wid, "E-1"))
    preview = client.get(f"/api/v1/enrollment-windows/{wid}/close-preview")
    assert preview.status_code == 200, preview.text
    body = preview.json()
    assert body["total"] == 2
    assert body["saved_not_sent"] == 1 and body["not_started"] == 1
    assert body["saved_submittable"] == 1 and body["saved_blocked_count"] == 0
    # The preview is read-only: nothing moved.
    detail = client.get(f"/api/v1/enrollments/{_enrollment_id(client, wid, 'E-1')}")
    assert detail.json()["status"] == "in_progress"


def test_close_discards_saved_unless_submit_saved(client: TestClient) -> None:
    wid = _make_window(client)
    _save_upgrade(client, _enrollment_id(client, wid, "E-1"))
    summary = client.post(f"/api/v1/enrollment-windows/{wid}/close").json()
    assert summary["saved_discarded"] == 1 and summary["submitted_at_close"] == 0
    with SessionLocal() as s:
        assert load_overrides(s, PY_ID, [EMP1]) == {}


def test_close_with_submit_saved_projects_saved_choices(client: TestClient) -> None:
    wid = _make_window(client)
    _save_upgrade(client, _enrollment_id(client, wid, "E-1"))
    _expire(wid)
    res = client.post(
        f"/api/v1/enrollment-windows/{wid}/close", json={"submit_saved": True}
    )
    assert res.status_code == 200, res.text
    summary = res.json()
    assert summary["submitted_at_close"] == 1 and summary["confirmed"] == 1
    assert summary["deemed_kept"] == 1
    with SessionLocal() as s:
        assert load_overrides(s, PY_ID, [EMP1])[(EMP1, PROD_ID)].plan_code == "GOLD"


def test_manual_override_warns_about_pending_election(client: TestClient) -> None:
    wid = _make_window(client)
    _save_upgrade(client, _enrollment_id(client, wid, "E-1"))
    url = f"/api/v1/employees/{EMP1}/plan-overrides/MED"
    blocked = client.put(url, json={"plan_code": "SILVER"})
    assert blocked.status_code == 409
    assert blocked.json()["detail"]["code"] == "open_enrollment_election"
    ok = client.put(url, json={"plan_code": "SILVER", "acknowledge_open_enrollment": True})
    assert ok.status_code == 200, ok.text
    # No pending election for E-2 → no warning.
    other = client.put(f"/api/v1/employees/{EMP2}/plan-overrides/MED", json={"plan_code": "GOLD"})
    assert other.status_code == 200, other.text


def test_readiness_warns_self_service_on_a_year_that_is_not_live(
    client: TestClient,
) -> None:
    wid = client.post(
        f"/api/v1/policy-years/{PY_ID}/enrollment-windows",
        json={"name": "Draft", "opens_at": "2020-01-01T00:00:00Z",
              "closes_at": "2035-01-01T00:00:00Z"},
    ).json()["id"]
    body = client.get(f"/api/v1/enrollment-windows/{wid}/readiness").json()
    by_code = {i["code"]: i for i in body["issues"]}
    # The fixture year is a draft and nobody has a portal account.
    assert by_code["benefit_year_not_live"]["severity"] == "warning"
    assert by_code["portal_access_incomplete"]["severity"] == "warning"
    # Warnings are the broker's call — they never stop the period opening.
    assert body["ready"] is True
    assert client.post(f"/api/v1/enrollment-windows/{wid}/open").status_code == 200


def test_roster_wipe_guard_counts_untouched_open_period_members(
    client: TestClient,
) -> None:
    _make_window(client)  # both members enrolled, neither has started
    res = client.delete(f"/api/v1/employees?policy_year_id={PY_ID}")
    assert res.status_code == 409, res.text
    assert res.json()["detail"]["open_period_members_at_risk"] == 2


def test_sync_open_windows_enrols_staff_added_after_open(client: TestClient) -> None:
    from app.services.enrollment_lifecycle import sync_open_windows

    wid = _make_window(client)
    new_id = "00000000-0000-0000-0000-00000000a0e9"
    with SessionLocal() as s:
        s.add(Employee(
            id=new_id, client_id=CLIENT_ID, policy_year_id=PY_ID,
            staff_id="E-9", employee_name="Late joiner",
            attribute_values={}, derived_attribute_values={},
            matched_categories=[{"category_id": CAT_ID, "product_code": "MED",
                                 "method": "rule", "confidence": 1.0}],
            source="csv_import", status="active",
        ))
        s.commit()
    try:
        with SessionLocal() as s:
            assert sync_open_windows(s, _user(), PY_ID, trigger="test") == 1
            assert sync_open_windows(s, _user(), PY_ID, trigger="test") == 0
            s.commit()
        roster = client.get(f"/api/v1/enrollment-windows/{wid}/enrollments").json()
        assert roster["total"] == 3
    finally:
        with SessionLocal() as s:
            s.query(Enrollment).filter(Enrollment.employee_id == new_id).delete()
            s.query(Employee).filter(Employee.id == new_id).delete()
            s.commit()


def test_progress_counts_members_by_status(client: TestClient) -> None:
    wid = _make_window(client)
    _save_upgrade(client, _enrollment_id(client, wid, "E-1"))
    body = client.get(f"/api/v1/enrollment-windows/{wid}/progress").json()
    assert body["total"] == 2
    assert body["in_progress"] == 1 and body["not_started"] == 1
    assert body["not_in_period"] == 0


def test_confirm_submitted_confirms_valid_and_reports_the_rest(
    client: TestClient,
) -> None:
    wid = _make_window(client)
    e1 = _enrollment_id(client, wid, "E-1")
    e2 = _enrollment_id(client, wid, "E-2")
    for eid in (e1, e2):
        _save_upgrade(client, eid)
        assert client.post(f"/api/v1/enrollments/{eid}/submit").status_code == 200
    # E-2's elected plan disappears from the product after submit.
    with SessionLocal() as s:
        el = s.query(EnrollmentElection).filter_by(enrollment_id=e2).one()
        el.elected_plan_code = "PLATINUM"
        s.commit()
    _expire(wid)  # review keeps working past the deadline
    res = client.post(f"/api/v1/enrollment-windows/{wid}/confirm-submitted")
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["confirmed"] == 1
    assert [f["staff_id"] for f in body["failed"]] == ["E-2"]
    assert client.get(f"/api/v1/enrollments/{e1}").json()["status"] == "confirmed"
    assert client.get(f"/api/v1/enrollments/{e2}").json()["status"] == "submitted"


def test_option_premiums_are_one_members_not_the_groups() -> None:
    from app.schemas.api import PlanFinancials
    from app.services.enrollment_elections import _per_member_financials

    flat = PlanFinancials(
        num_employees=None, basis=None, sum_insured=None, premium_rate=378.0,
        annual_premium=186_732.0, rate_basis="flat", rate_tiers=None,
    )
    assert _per_member_financials(flat).annual_premium == 378.0
    tiered = PlanFinancials(
        num_employees=252, basis=None, sum_insured=None, premium_rate=None,
        annual_premium=262_332.0, rate_basis="tiered",
        rate_tiers={"EO": {"rate": 1041.0, "premium": 0.0}},
    )
    out = _per_member_financials(tiered)
    assert out.annual_premium == 1041.0 and out.num_employees is None
    # A tier the rate table can't price shows no premium, never the group's.
    unpriced = tiered.model_copy(update={"rate_tiers": None})
    assert _per_member_financials(unpriced).annual_premium is None
    life = PlanFinancials(
        num_employees=None, basis="50000", sum_insured=50_000.0, premium_rate=3.06,
        annual_premium=153.0, rate_basis="per_1000_si", rate_tiers=None,
    )
    assert _per_member_financials(life).annual_premium == 153.0


def test_extending_an_overdue_deadline_reopens_the_period(client: TestClient) -> None:
    wid = _make_window(client)
    _expire(wid)
    res = client.patch(
        f"/api/v1/enrollment-windows/{wid}", json={"closes_at": "2035-06-30T09:00:00Z"}
    )
    assert res.status_code == 200, res.text
    assert res.json()["phase"] == "open"
    _save_upgrade(client, _enrollment_id(client, wid, "E-1"))  # edits work again


def test_sync_skips_a_period_past_its_deadline(client: TestClient) -> None:
    """A new hire added to an overdue period could never choose, and under
    deemed-decline would lose voluntary cover at close unasked."""
    from app.services.enrollment_lifecycle import sync_open_windows

    wid = _make_window(client, default_behavior="deemed_decline")
    _expire(wid)
    new_id = "00000000-0000-0000-0000-00000000a0e8"
    with SessionLocal() as s:
        s.add(Employee(
            id=new_id, client_id=CLIENT_ID, policy_year_id=PY_ID,
            staff_id="E-8", employee_name="Late hire",
            attribute_values={}, derived_attribute_values={},
            matched_categories=[{"category_id": CAT_ID, "product_code": "MED",
                                 "method": "rule", "confidence": 1.0}],
            source="csv_import", status="active",
        ))
        s.commit()
    try:
        with SessionLocal() as s:
            assert sync_open_windows(s, _user(), PY_ID, trigger="test") == 0
            s.commit()
        body = client.get(f"/api/v1/enrollment-windows/{wid}/progress").json()
        assert body["not_in_period"] == 1  # visible to the broker, not silently added
    finally:
        with SessionLocal() as s:
            s.query(Employee).filter(Employee.id == new_id).delete()
            s.commit()


def test_confirm_submitted_isolates_a_projection_failure(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.services import enrollment_lifecycle

    wid = _make_window(client)
    e1 = _enrollment_id(client, wid, "E-1")
    e2 = _enrollment_id(client, wid, "E-2")
    for eid in (e1, e2):
        _save_upgrade(client, eid)
        client.post(f"/api/v1/enrollments/{eid}/submit")
    real = enrollment_lifecycle.project_enrollment

    def flaky(db, enr, user):  # type: ignore[no-untyped-def]
        if enr.id == e2:
            raise RuntimeError("boom")
        return real(db, enr, user)

    monkeypatch.setattr(enrollment_lifecycle, "project_enrollment", flaky)
    res = client.post(f"/api/v1/enrollment-windows/{wid}/confirm-submitted")
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["confirmed"] == 1
    assert [f["staff_id"] for f in body["failed"]] == ["E-2"]
    assert client.get(f"/api/v1/enrollments/{e1}").json()["status"] == "confirmed"
    assert client.get(f"/api/v1/enrollments/{e2}").json()["status"] == "submitted"


def test_manual_override_warns_when_close_will_decline(client: TestClient) -> None:
    url = f"/api/v1/employees/{EMP1}/plan-overrides/MED"
    # Untouched member in a deemed-decline period: close declines MED.
    _make_window(client, default_behavior="deemed_decline")
    blocked = client.put(url, json={"plan_code": "GOLD"})
    assert blocked.status_code == 409, blocked.text
    assert "declined" in blocked.json()["detail"]["message"]
    assert client.put(
        url, json={"plan_code": "GOLD", "acknowledge_open_enrollment": True}
    ).status_code == 200


def test_manual_override_warns_when_member_declined_all(client: TestClient) -> None:
    wid = _make_window(client, default_behavior="deemed_keep_current")
    eid = _enrollment_id(client, wid, "E-1")
    with SessionLocal() as s:
        s.get(Enrollment, eid).status = "declined"
        s.commit()
    res = client.put(f"/api/v1/employees/{EMP1}/plan-overrides/MED", json={"plan_code": "GOLD"})
    assert res.status_code == 409
    assert "decline all" in res.json()["detail"]["message"]
    # An untouched member under keep-current is NOT warned.
    other = client.put(f"/api/v1/employees/{EMP2}/plan-overrides/MED", json={"plan_code": "GOLD"})
    assert other.status_code == 200, other.text


def test_close_declines_a_declined_member_even_under_keep_current(
    client: TestClient,
) -> None:
    wid = _make_window(client, default_behavior="deemed_keep_current")
    eid = _enrollment_id(client, wid, "E-1")
    with SessionLocal() as s:
        s.get(Enrollment, eid).status = "declined"
        s.commit()
    preview = client.get(f"/api/v1/enrollment-windows/{wid}/close-preview").json()
    assert preview["declined"] == 1 and preview["not_started"] == 1
    summary = client.post(f"/api/v1/enrollment-windows/{wid}/close").json()
    assert summary["deemed_declined"] == 1 and summary["deemed_kept"] == 1
