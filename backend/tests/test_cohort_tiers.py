"""Cohort-scoped, direction-aware enrollment tiers.

Verifies that the election dropdown (and its validation) is restricted to the
member's own cohort tiers — the voluntary siblings of their matched compulsory
baseline — instead of every plan of the product, and that tiers sharing a
plan_code (GPA "Option N") are electable via ``tier_category_id``.
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest

TEST_DB = Path(__file__).parent / "_test_cohort_tiers.db"
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
    EmployeePlanOverride,
    Enrollment,
    EnrollmentElection,
    EnrollmentWindow,
    Plan,
    PolicyYear,
    Product,
)
from app.models.category import CategoryStatus, SourceKind  # noqa: E402
from app.models.policy_year import PolicyYearStatus  # noqa: E402
from app.services.coverage_resolver import load_overrides  # noqa: E402
from scripts.seed_demo import seed  # noqa: E402

CLIENT_ID = "00000000-0000-0000-0000-00000000e100"
PY_ID = "00000000-0000-0000-0000-00000000e101"
# GL = sibling-category tiers (like GTL); GA = shared-plan_code options (like GPA);
# GM = one category with multiple Plan rows (tiers synthesized from plans).
GL_ID = "00000000-0000-0000-0000-00000000e102"
GA_ID = "00000000-0000-0000-0000-00000000e103"
EMP = "00000000-0000-0000-0000-00000000e104"
GM_ID = "00000000-0000-0000-0000-00000000e105"
GM_BASE = "cat-gm-all"
# Member's GL baseline cohort categories.
GL_BASE = "cat-gl-exec-base"
GL_UP = "cat-gl-exec-up"
GL_DOWN = "cat-gl-exec-down"
GL_FOREIGN = "cat-gl-clerk"  # a different cohort — must NOT be electable
GA_BASE = "cat-ga-ceo-base"
GA_OPT = "cat-ga-ceo-opt"


def _user() -> CurrentUser:
    return CurrentUser(
        user_id="00000000-0000-0000-0000-00000000e1ff",
        broker_firm_id=DEMO_BROKER_FIRM_ID, client_id=CLIENT_ID, role="broker_admin",
    )


def _cat(cid, pid, name, plan_code, part, detail):
    return Category(
        id=cid, policy_year_id=PY_ID, product_id=pid, priority=1,
        display_name=name, raw_description=name,
        participation_model=part, participation_detail=detail,
        plan_assignments={"plan_code": plan_code},
        source=SourceKind.system_generated.value,
        status=CategoryStatus.confirmed.value, human_modified=False,
    )


@pytest.fixture(scope="module", autouse=True)
def _setup_db():
    if TEST_DB.exists():
        TEST_DB.unlink()
    Base.metadata.create_all(bind=engine)
    seed()
    with SessionLocal() as s:
        s.add(Client(id=CLIENT_ID, name="Cohort Co", broker_firm_id=DEMO_BROKER_FIRM_ID))
        s.flush()
        s.add(PolicyYear(
            id=PY_ID, client_id=CLIENT_ID, year=2031,
            start_date=date(2031, 1, 1), end_date=date(2031, 12, 31),
            status=PolicyYearStatus.draft,
        ))
        s.flush()
        s.add(Product(id=GL_ID, client_id=CLIENT_ID, code="GL", display_name="Group Life"))
        s.add(Product(id=GA_ID, client_id=CLIENT_ID, code="GA", display_name="Group PA"))
        s.add(Product(id=GM_ID, client_id=CLIENT_ID, code="GM", display_name="Group Medical"))
        s.flush()
        # GM: one cohort category (SILVER) plus a richer GOLD plan with no category
        # of its own — GOLD is synthesized as a tier sharing the baseline category id.
        for code in ("SILVER", "GOLD"):
            s.add(Plan(id=f"gm-{code}", product_id=GM_ID, policy_year_id=PY_ID,
                       code=code, display_name=code, status="confirmed"))
        s.add(_cat(GM_BASE, GM_ID, "All Staff", "SILVER", "compulsory",
                   {"employee": "compulsory", "dependant": None, "direction": None}))
        # GL has plans for every tier + the foreign cohort.
        for code in ("1", "10", "17", "8"):
            s.add(Plan(id=f"gl-{code}", product_id=GL_ID, policy_year_id=PY_ID,
                       code=code, display_name=f"Plan {code}", status="confirmed"))
        # GA has a single plan; its tiers are "Options" sharing that code.
        s.add(Plan(id="ga-1", product_id=GA_ID, policy_year_id=PY_ID,
                   code="1", display_name="Plan 1", status="confirmed"))
        # GL Exec cohort: compulsory baseline + voluntary up + voluntary down.
        s.add(_cat(GL_BASE, GL_ID, "Exec (Job category: 99)", "1", "compulsory",
                   {"employee": "compulsory", "dependant": None, "direction": None}))
        s.add(_cat(GL_UP, GL_ID, "Exec (Job category: 99)", "10", "voluntary",
                   {"employee": "voluntary", "dependant": None, "direction": "upgrade"}))
        s.add(_cat(GL_DOWN, GL_ID, "Exec (Job category: 99)", "17", "voluntary",
                   {"employee": "voluntary", "dependant": None, "direction": "downgrade"}))
        # A different cohort — its plan (8) must never be offered to the Exec member.
        s.add(_cat(GL_FOREIGN, GL_ID, "Clerical (Job category: C1)", "8", "compulsory",
                   {"employee": "compulsory", "dependant": None, "direction": None}))
        # GA cohort: compulsory baseline + one voluntary Option, same plan_code.
        s.add(_cat(GA_BASE, GA_ID, "CEO", "1", "compulsory",
                   {"employee": "compulsory", "dependant": None, "direction": None}))
        s.add(_cat(GA_OPT, GA_ID, "CEO (Option 1)", "1", "voluntary",
                   {"employee": "voluntary", "dependant": None, "direction": None}))
        s.add(Employee(
            id=EMP, client_id=CLIENT_ID, policy_year_id=PY_ID,
            staff_id="C-1", employee_name="Cohort Member",
            attribute_values={}, derived_attribute_values={},
            matched_categories=[
                {"category_id": GL_BASE, "product_code": "GL", "method": "rule", "confidence": 1.0},
                {"category_id": GA_BASE, "product_code": "GA", "method": "rule", "confidence": 1.0},
                {"category_id": GM_BASE, "product_code": "GM", "method": "rule", "confidence": 1.0},
            ],
            source="csv_import", status="active",
        ))
        s.commit()
    yield
    engine.dispose()
    if TEST_DB.exists():
        TEST_DB.unlink()


@pytest.fixture(autouse=True)
def _reset():
    yield
    with SessionLocal() as s:
        for model in (EnrollmentElection, Enrollment, EmployeePlanOverride, EnrollmentWindow):
            s.query(model).delete()
        s.commit()


@pytest.fixture
def client() -> TestClient:
    app.dependency_overrides[get_current_user] = _user
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.pop(get_current_user, None)


def _window_and_enrollment(client: TestClient) -> str:
    wid = client.post(
        f"/api/v1/policy-years/{PY_ID}/enrollment-windows",
        json={"name": "OE", "window_type": "open",
              "opens_at": "2020-01-01T00:00:00Z", "closes_at": "2035-01-01T00:00:00Z"},
    ).json()["id"]
    client.post(f"/api/v1/enrollment-windows/{wid}/open")
    roster = client.get(f"/api/v1/enrollment-windows/{wid}/enrollments").json()
    return next(i["id"] for i in roster["items"] if i["staff_id"] == "C-1")


def _gl(options: dict) -> dict:
    return next(p for p in options["products"] if p["product_code"] == "GL")


def _ga(options: dict) -> dict:
    return next(p for p in options["products"] if p["product_code"] == "GA")


def _gm(options: dict) -> dict:
    return next(p for p in options["products"] if p["product_code"] == "GM")


def test_options_scoped_to_cohort_with_direction(client: TestClient) -> None:
    eid = _window_and_enrollment(client)
    options = client.get(f"/api/v1/enrollments/{eid}/options").json()
    gl = _gl(options)
    codes = {t["plan_code"] for t in gl["tiers"]}
    # Only the Exec cohort's tiers — the Clerical plan "8" is excluded.
    assert codes == {"1", "10", "17"}
    assert "8" not in codes
    assert gl["employee_participation"] == "compulsory"
    assert gl["can_decline"] is False  # compulsory baseline can't be declined
    assert gl["allow_plan_change"] is True
    by_code = {t["plan_code"]: t for t in gl["tiers"]}
    assert by_code["1"]["is_baseline"] is True
    assert by_code["10"]["direction"] == "upgrade"
    assert by_code["17"]["direction"] == "downgrade"


def test_elect_foreign_cohort_plan_rejected(client: TestClient) -> None:
    eid = _window_and_enrollment(client)
    res = client.put(
        f"/api/v1/enrollments/{eid}/elections",
        json={"elections": [{"product_code": "GL", "plan_code": "8"}]},
    )
    assert res.status_code == 422
    assert "cohort" in res.json()["detail"].lower()


def test_elect_sibling_tier_records_direction(client: TestClient) -> None:
    eid = _window_and_enrollment(client)
    res = client.put(
        f"/api/v1/enrollments/{eid}/elections",
        json={"elections": [{"product_code": "GL", "plan_code": "17"}]},
    )
    assert res.status_code == 200, res.text
    gl = next(e for e in res.json()["elections"] if e["product_code"] == "GL")
    assert gl["elected_plan_code"] == "17"
    assert gl["tier_category_id"] == GL_DOWN
    assert gl["action"] == "downgrade"


def test_synthesized_plan_tiers_have_unique_keys_and_resolve(client: TestClient) -> None:
    # GM has one category but two plan rows: baseline SILVER + synthesized GOLD,
    # both carrying the baseline category id. They must stay distinguishable.
    eid = _window_and_enrollment(client)
    gm = _gm(client.get(f"/api/v1/enrollments/{eid}/options").json())
    by_plan = {t["plan_code"]: t for t in gm["tiers"]}
    assert set(by_plan) == {"SILVER", "GOLD"}
    # Both share the baseline category id, so the unique key must differ.
    assert by_plan["SILVER"]["tier_category_id"] == by_plan["GOLD"]["tier_category_id"] == GM_BASE
    assert by_plan["SILVER"]["key"] != by_plan["GOLD"]["key"]

    # Electing GOLD via the (tier_category_id, plan_code) pair the UI sends works.
    res = client.put(
        f"/api/v1/enrollments/{eid}/elections",
        json={"elections": [
            {"product_code": "GM", "tier_category_id": GM_BASE, "plan_code": "GOLD"},
        ]},
    )
    assert res.status_code == 200, res.text
    gm_el = next(e for e in res.json()["elections"] if e["product_code"] == "GM")
    assert gm_el["elected_plan_code"] == "GOLD"

    # tier_category_id alone is ambiguous here (two tiers share it) → must 422.
    ambiguous = client.put(
        f"/api/v1/enrollments/{eid}/elections",
        json={"elections": [{"product_code": "GM", "tier_category_id": GM_BASE}]},
    )
    assert ambiguous.status_code == 422

    client.post(f"/api/v1/enrollments/{eid}/submit")
    client.post(f"/api/v1/enrollments/{eid}/confirm")
    with SessionLocal() as s:
        assert load_overrides(s, PY_ID, [EMP])[(EMP, GM_ID)].plan_code == "GOLD"


def test_gpa_option_electable_via_tier_category_id(client: TestClient) -> None:
    eid = _window_and_enrollment(client)
    options = client.get(f"/api/v1/enrollments/{eid}/options").json()
    ga = _ga(options)
    # Two tiers share plan_code "1" — distinguished by tier_category_id.
    assert {t["tier_category_id"] for t in ga["tiers"]} == {GA_BASE, GA_OPT}
    res = client.put(
        f"/api/v1/enrollments/{eid}/elections",
        json={"elections": [{"product_code": "GA", "tier_category_id": GA_OPT}]},
    )
    assert res.status_code == 200, res.text
    # A bare plan_code "1" would be ambiguous — must require the tier id.
    ambiguous = client.put(
        f"/api/v1/enrollments/{eid}/elections",
        json={"elections": [{"product_code": "GA", "plan_code": "1"}]},
    )
    assert ambiguous.status_code == 422

    client.post(f"/api/v1/enrollments/{eid}/submit")
    client.post(f"/api/v1/enrollments/{eid}/confirm")
    # The Option election persists into the override even though plan_code is
    # unchanged from the baseline tier.
    with SessionLocal() as s:
        ov = load_overrides(s, PY_ID, [EMP])[(EMP, GA_ID)]
        assert ov.tier_category_id == GA_OPT
        assert ov.plan_code == "1"


def test_equal_sum_insured_is_not_a_false_upgrade() -> None:
    """A higher plan-code with the SAME sum insured must read as 'same', not
    'upgrade' — a voluntary buy-up replicated from the compulsory tier (GCI does
    this) is identical coverage, so its direction tag must be suppressed."""
    from app.services.cohort_tiers import _direction

    base = _cat("c-base", GL_ID, "Exec", "5", "compulsory",
                {"employee": "compulsory", "dependant": None, "direction": None})
    base.plan_assignments = {"plan_code": "5", "sum_insured": 7_600_000.0}
    # Same SI, higher plan code, no slip-stated direction → "same".
    same = _cat("c-same", GL_ID, "Exec", "20", "voluntary",
                {"employee": "voluntary", "dependant": None, "direction": None})
    same.plan_assignments = {"plan_code": "20", "sum_insured": 7_600_000.0}
    assert _direction(base, same) == "same"

    # A genuinely richer SI still reads as an upgrade.
    richer = _cat("c-rich", GL_ID, "Exec", "21", "voluntary",
                  {"employee": "voluntary", "dependant": None, "direction": None})
    richer.plan_assignments = {"plan_code": "21", "sum_insured": 9_000_000.0}
    assert _direction(base, richer) == "upgrade"

    # A slip-stated direction still wins even when SI is tied.
    stated = _cat("c-stated", GL_ID, "Exec", "13", "voluntary",
                  {"employee": "voluntary", "dependant": None, "direction": "upgrade"})
    stated.plan_assignments = {"plan_code": "13", "sum_insured": 7_600_000.0}
    assert _direction(base, stated) == "upgrade"

    # No SI on either side and plan codes that don't order → 'unknown', NOT
    # 'same': the action layer must keep its plan-rank fallback so a real change
    # (e.g. SILVER→GOLD) still registers as up/down instead of collapsing to keep.
    no_si_base = _cat("c-silver", GL_ID, "All", "SILVER", "compulsory",
                      {"employee": "compulsory", "dependant": None, "direction": None})
    no_si_tier = _cat("c-gold", GL_ID, "All", "GOLD", "voluntary",
                      {"employee": "voluntary", "dependant": None, "direction": None})
    assert _direction(no_si_base, no_si_tier) == "unknown"


def test_per_member_basis_drives_direction_and_financials() -> None:
    """Per-member ``basis`` is the true tier signal: GCI copies the GROUP
    sum_insured across a cohort's tiers (identical), but each tier's per-member
    basis differs. Direction and the displayed financials must follow basis."""
    from app.services.cohort_tiers import _direction
    from app.services.plan_hydration import member_financials as _member_financials

    # Manager cohort: group sum_insured identical ($10M), per-member basis differs.
    base = _cat("m-base", GL_ID, "Manager", "4", "compulsory",
                {"employee": "compulsory", "dependant": None, "direction": None})
    base.plan_assignments = {"plan_code": "4", "sum_insured": 10_000_000.0,
                             "basis": "100000.0", "num_employees": 100,
                             "premium_rate": 3.06, "rate_basis": "per_1000_si",
                             "annual_premium": 30_600.0}
    up = _cat("m-up", GL_ID, "Manager", "12", "voluntary",
              {"employee": "voluntary", "dependant": None, "direction": None})
    up.plan_assignments = {"plan_code": "12", "sum_insured": 10_000_000.0,
                           "basis": "150000.0", "premium_rate": 3.06,
                           "rate_basis": "per_1000_si", "annual_premium": 30_600.0}
    down = _cat("m-down", GL_ID, "Manager", "19", "voluntary",
                {"employee": "voluntary", "dependant": None, "direction": None})
    down.plan_assignments = {"plan_code": "19", "sum_insured": 10_000_000.0,
                             "basis": "50000.0", "premium_rate": 3.06,
                             "rate_basis": "per_1000_si", "annual_premium": 30_600.0}

    # Group SI ties; basis decides → real upgrade / downgrade.
    assert _direction(base, up) == "upgrade"
    assert _direction(base, down) == "downgrade"

    # Financials are per-member: covered amount = basis, premium = basis/1k * rate.
    fin = _member_financials(base.plan_assignments)
    assert fin is not None
    assert fin.sum_insured == 100_000.0
    assert fin.annual_premium == 306.0  # 100000 / 1000 * 3.06
    assert fin.num_employees is None
    assert _member_financials(up.plan_assignments).sum_insured == 150_000.0
    assert _member_financials(down.plan_assignments).sum_insured == 50_000.0

    # A salary-multiple basis has no per-member number → keep the parsed figures.
    txt = _cat("m-mult", GL_ID, "Manager", "99", "voluntary",
               {"employee": "voluntary", "dependant": None, "direction": None})
    txt.plan_assignments = {"plan_code": "99", "sum_insured": 10_000_000.0,
                            "basis": "12 times basic monthly salary"}
    assert _member_financials(txt.plan_assignments).sum_insured == 10_000_000.0


def test_voluntary_life_tier_premium_is_age_banded() -> None:
    """A voluntary life tier carrying a ``voluntary_rates`` table prices off the
    member's age band — basis/1000 x rate[age] — not the flat compulsory rate."""
    from app.services.plan_hydration import member_financials

    bands = [
        {"label": "<=34", "min": None, "max": 34, "rate": 0.88},
        {"label": "45-49", "min": 45, "max": 49, "rate": 1.65},
        {"label": "50-54", "min": 50, "max": 54, "rate": 2.04},
    ]
    pa = {"plan_code": "10", "basis": "500000.0", "rate_basis": "per_1000_si",
          "premium_rate": 1.62, "voluntary_rates": bands}
    # age 47 → band 45-49 → 1.65 → 500000/1000*1.65 = 825
    fin = member_financials(pa, 47)
    assert fin.premium_rate == 1.65
    assert fin.annual_premium == 825.0
    assert fin.sum_insured == 500_000.0
    # A child (age 8) falls in the open lowest band.
    assert member_financials({**pa, "basis": "30000.0"}, 8).annual_premium == 26.4
    # No age (aggregate view) → premium can't be pinned to a band.
    assert member_financials(pa, None).annual_premium is None
    # An out-of-table age (no band covers it) → no rate, no premium.
    assert member_financials(pa, 80).annual_premium is None


def test_enumerated_cohort_suppresses_unclaimed_plan_tiers() -> None:
    """When the slip ENUMERATED a cohort's alternatives as voluntary sibling
    categories, that enumeration is authoritative: product plans no cohort
    claims must NOT be appended as heuristic tiers. Single-category cohorts
    (no siblings) keep the synthesized-plan fallback."""
    from app.services.cohort_tiers import _build_tier_set

    base = _cat("g-base", GL_ID, "Ops (Job category: 5)", "1", "compulsory",
                {"employee": "compulsory", "dependant": None, "direction": None})
    up = _cat("g-up", GL_ID, "Ops (Job category: 5)", "2", "voluntary",
              {"employee": "voluntary", "dependant": None, "direction": "upgrade"})
    ts = _build_tier_set(base, [base, up], {"1", "2", "99"}, "GL")
    assert {t.plan_code for t in ts.tiers} == {"1", "2"}  # 99 suppressed

    solo = _cat("g-solo", GL_ID, "All Staff", "S1", "compulsory",
                {"employee": "compulsory", "dependant": None, "direction": None})
    ts2 = _build_tier_set(solo, [solo], {"S1", "S2"}, "GM2")
    assert {t.plan_code for t in ts2.tiers} == {"S1", "S2"}  # fallback intact


def test_dependant_option_overlay_linkage_rules() -> None:
    """Dependant option rows stick to the employee plan: marker match (GPA
    "(Option N)"), plan-code match for composition rows (VDL dependants sheet),
    sole unmarked row -> every tier; ambiguous multi-level rows stay unlinked."""
    from app.services.cohort_tiers import tier_key as tk
    from app.services.flex_pricing_resolver import dependant_option_overlay

    def _pa(**kw):
        return kw

    rows = [
        # Employee tiers.
        _cat("do-emp-o1", GA_ID, "Manager (Option 1)", "9", "voluntary",
             {"employee": "voluntary", "dependant": None, "direction": None}),
        _cat("do-emp-o2", GA_ID, "Manager (Option 2)", "10", "voluntary",
             {"employee": "voluntary", "dependant": None, "direction": None}),
        # Marker-matched dependant options.
        _cat("do-dep-s1", GA_ID, "Spouse (Option 1)", "23", "voluntary",
             {"employee": None, "dependant": "voluntary", "direction": None}),
        _cat("do-dep-s2", GA_ID, "Spouse (Option 2)", "24", "voluntary",
             {"employee": None, "dependant": "voluntary", "direction": None}),
        # Sole unmarked child row -> applies to every employee tier.
        _cat("do-dep-c", GA_ID, "Child", "26", "voluntary",
             {"employee": None, "dependant": "voluntary", "direction": None}),
        # Composition row (dependants sheet): same plan code as an employee tier.
        _cat("do-emp-b3", GM_ID, "Grade 40 & above (S Pass)", "B3", "compulsory",
             {"employee": "compulsory", "dependant": "voluntary", "direction": None}),
        _cat("do-dep-b3", GM_ID, "Grade 40 & above (S Pass) eligible dependants",
             "B3", "voluntary", {"employee": None, "dependant": "voluntary", "direction": None}),
    ]
    pas = {
        "do-emp-o1": {"plan_code": "9"},
        "do-emp-o2": {"plan_code": "10"},
        "do-dep-s1": {"plan_code": "23", "sum_insured": 20000.0, "premium_rate": 0.072,
                      "rate_basis": "per_1000_si", "member_scope": "dependant"},
        "do-dep-s2": {"plan_code": "24", "sum_insured": 40000.0, "premium_rate": 0.072,
                      "rate_basis": "per_1000_si", "member_scope": "dependant"},
        "do-dep-c": {"plan_code": "26", "sum_insured": 10000.0, "premium_rate": 0.072,
                     "rate_basis": "per_1000_si", "member_scope": "dependant"},
        "do-emp-b3": {"plan_code": "B3"},
        "do-dep-b3": {"plan_code": "B3", "member_scope": "dependant",
                      "rate_tiers": {"SO": {"rate": 407.0, "premium": 0.0},
                                     "CO": {"rate": 407.0, "premium": 0.0},
                                     "SC": {"rate": 678.0, "premium": 0.0}}},
    }
    with SessionLocal() as s:
        for r in rows:
            r.plan_assignments = pas[r.id]
            s.add(r)
        s.commit()
        try:
            overlay = dependant_option_overlay(s, PY_ID)
            # Marker match: each employee option gets ITS OWN spouse amount.
            o1 = overlay[GA_ID][tk("do-emp-o1", "9")]
            o2 = overlay[GA_ID][tk("do-emp-o2", "10")]
            assert o1["options"]["spouse"] == 1.44   # 20k/1000 x 0.072
            assert o2["options"]["spouse"] == 2.88   # 40k/1000 x 0.072
            # Sole unmarked child row applies to both employee tiers.
            assert o1["options"]["child"] == 0.72    # 10k/1000 x 0.072
            assert o2["options"]["child"] == 0.72
            # Composition row -> standalone family amounts on the same-plan tier.
            b3 = overlay[GM_ID][tk("do-emp-b3", "B3")]
            assert b3 == {"spouse": 407.0, "child": 407.0, "both": 678.0}
        finally:
            for r in rows:
                s.query(Category).filter(Category.id == r.id).delete()
            s.commit()


def test_dependant_option_overlay_freestanding_levels_become_choices() -> None:
    """Rule 4 (CDL GTL shape): MULTIPLE unmarked Spouse/Child rows are
    freestanding option LEVELS — attached to every employee tier as electable
    ``choices`` (sorted by cover amount), priced from the slip's voluntary
    age-band table on the dependant's own age."""
    from app.services.cohort_tiers import tier_key as tk
    from app.services.flex_pricing_resolver import (
        dependant_option_overlay,
        option_amount,
    )

    bands = [
        {"label": "34 & below", "min": None, "max": 34, "rate": 0.88},
        {"label": "35 to 44", "min": 35, "max": 44, "rate": 1.32},
    ]
    rows = [
        _cat("fl-emp-1", GL_ID, "Manager (Job category: E5)", "4", "compulsory",
             {"employee": "compulsory", "dependant": None, "direction": None}),
        _cat("fl-emp-2", GL_ID, "Officer (Job category: J1)", "6", "compulsory",
             {"employee": "compulsory", "dependant": None, "direction": None}),
        _cat("fl-dep-s40", GL_ID, "Spouse", "1", "voluntary",
             {"employee": None, "dependant": "voluntary", "direction": None}),
        _cat("fl-dep-s60", GL_ID, "Spouse", "3", "voluntary",
             {"employee": None, "dependant": "voluntary", "direction": None}),
        _cat("fl-dep-s20", GL_ID, "Spouse", "5", "voluntary",
             {"employee": None, "dependant": "voluntary", "direction": None}),
        _cat("fl-dep-c30", GL_ID, "Child", "2", "voluntary",
             {"employee": None, "dependant": "voluntary", "direction": None}),
        _cat("fl-dep-c10", GL_ID, "Child", "6", "voluntary",
             {"employee": None, "dependant": "voluntary", "direction": None}),
    ]
    pas = {
        "fl-emp-1": {"plan_code": "4"},
        "fl-emp-2": {"plan_code": "6"},
        "fl-dep-s40": {"plan_code": "1", "basis": "40000.0", "member_scope": "dependant",
                       "rate_basis": "age_banded", "voluntary_rates": bands},
        "fl-dep-s60": {"plan_code": "3", "basis": "60000.0", "member_scope": "dependant",
                       "rate_basis": "age_banded", "voluntary_rates": bands},
        "fl-dep-s20": {"plan_code": "5", "basis": "20000.0", "member_scope": "dependant",
                       "rate_basis": "age_banded", "voluntary_rates": bands},
        "fl-dep-c30": {"plan_code": "2", "basis": "30000.0", "member_scope": "dependant",
                       "rate_basis": "age_banded", "voluntary_rates": bands},
        "fl-dep-c10": {"plan_code": "6", "basis": "10000.0", "member_scope": "dependant",
                       "rate_basis": "age_banded", "voluntary_rates": bands},
    }
    with SessionLocal() as s:
        for r in rows:
            r.plan_assignments = pas[r.id]
            s.add(r)
        s.commit()
        try:
            overlay = dependant_option_overlay(s, PY_ID)
            for emp_id, plan in (("fl-emp-1", "4"), ("fl-emp-2", "6")):
                row = overlay[GL_ID][tk(emp_id, plan)]
                # NOT auto-priced ("options") — surfaced as electable choices.
                assert "options" not in row
                spouse = row["choices"]["spouse"]
                child = row["choices"]["child"]
                # Sorted ascending by cover amount; plan numbers ignored
                # (they collide with employee plans by renumbering).
                assert [c["sum_insured"] for c in spouse] == [20000.0, 40000.0, 60000.0]
                assert [c["sum_insured"] for c in child] == [10000.0, 30000.0]
                # Each level prices on the dependant's own age band.
                assert option_amount(spouse[1]["spec"], 30) == 35.2   # 40k x 0.88
                assert option_amount(spouse[1]["spec"], 40) == 52.8   # 40k x 1.32
                assert option_amount(child[0]["spec"], 8) == 8.8      # 10k x 0.88
        finally:
            for r in rows:
                s.query(Category).filter(Category.id == r.id).delete()
            s.commit()


def test_insured_key_folds_suffix_variance() -> None:
    """Cohort splitting shares the matching engine's entity normalization, so
    "Pte. Ltd." and "Pte Ltd" are ONE entity here too. Before this, cohorts used
    a local lower()/whitespace key and split a cohort the match gate treated as
    one — the two disagreed."""
    from app.services.cohort_tiers import _insured_key, _same_insured

    a = _cat("a", None, "Managers", "1", "compulsory", None)
    b = _cat("b", None, "Managers", "2", "compulsory", None)
    a.plan_assignments = {"plan_code": "1", "insured": "CityNexus Pte. Ltd."}
    b.plan_assignments = {"plan_code": "2", "insured": ["CityNexus Pte Ltd"]}
    assert _insured_key(a) == _insured_key(b)
    assert _same_insured(a, b)

    # A genuinely different entity still splits the cohort.
    c = _cat("c", None, "Managers", "3", "compulsory", None)
    c.plan_assignments = {"plan_code": "3", "insured": ["Le Grove Pte Ltd"]}
    assert not _same_insured(a, c)

    # Blank on either side stays a wildcard.
    d = _cat("d", None, "Managers", "4", "compulsory", None)
    d.plan_assignments = {"plan_code": "4"}
    assert _same_insured(a, d)
