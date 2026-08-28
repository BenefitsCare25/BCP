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
CAT_FOREIGN = "00000000-0000-0000-0000-00000000d009"
CAT_DEN = "00000000-0000-0000-0000-00000000d00a"
# A second benefit year for the SAME client — the request_id replay guard has
# to be scoped to the year, not just the tenant.
PY_OTHER = "00000000-0000-0000-0000-00000000d00b"


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
        for code in ("SILVER", "GOLD", "BRONZE"):
            s.add(Plan(id=f"d-plan-{code}", product_id=PROD_ID, policy_year_id=PY_ID,
                       code=code, display_name=code, status="confirmed"))
        for code in ("BASIC", "PLUS"):
            s.add(Plan(id=f"d-den-{code}", product_id=OTHER_PROD_ID, policy_year_id=PY_ID,
                       code=code, display_name=code, status="confirmed"))
        s.add(Category(
            id=CAT_ID, policy_year_id=PY_ID, product_id=PROD_ID, priority=1,
            display_name="MED cohort", raw_description="MED cohort",
            plan_assignments={"plan_code": "SILVER"},
            source=SourceKind.system_generated.value,
            status=CategoryStatus.confirmed.value, human_modified=False,
        ))
        # A DIFFERENT MED cohort, matched to nobody. It exists so BRONZE is a
        # plan another cohort claims — which is what makes moving the MED cohort
        # to BRONZE an `outside_cohort` change. GOLD stays unclaimed and is
        # therefore still electable, so it remains the plain-move plan.
        s.add(Category(
            id=CAT_FOREIGN, policy_year_id=PY_ID, product_id=PROD_ID, priority=2,
            display_name="MED executives", raw_description="MED executives",
            plan_assignments={"plan_code": "BRONZE"},
            source=SourceKind.system_generated.value,
            status=CategoryStatus.confirmed.value, human_modified=False,
        ))
        s.add(Category(
            id=CAT_DEN, policy_year_id=PY_ID, product_id=OTHER_PROD_ID, priority=1,
            display_name="DEN cohort", raw_description="DEN cohort",
            plan_assignments={"plan_code": "BASIC"},
            source=SourceKind.system_generated.value,
            status=CategoryStatus.confirmed.value, human_modified=False,
        ))
        # EMP1, EMP2 are in the MED and DEN cohorts; EMP_NO_PROD matches nothing.
        matched_both = [
            {"category_id": CAT_ID, "product_code": "MED",
             "method": "rule", "confidence": 1.0},
            {"category_id": CAT_DEN, "product_code": "DEN",
             "method": "rule", "confidence": 1.0},
        ]
        for eid, staff, matched in (
            (EMP1, "D-1", True), (EMP2, "D-2", True), (EMP_NO_PROD, "D-3", False)
        ):
            s.add(Employee(
                id=eid, client_id=CLIENT_ID, policy_year_id=PY_ID,
                staff_id=staff, employee_name=f"Emp {staff}",
                attribute_values={}, derived_attribute_values={},
                matched_categories=(matched_both if matched else []),
                source="csv_import", status="active",
            ))
        s.add(PolicyYear(
            id=PY_OTHER, client_id=CLIENT_ID, year=2031,
            start_date=date(2031, 1, 1), end_date=date(2031, 12, 31),
            status=PolicyYearStatus.draft,
        ))
        s.flush()
        # MED is configured in the other year too, so `_resolve_changes`
        # succeeds there and the request_id guard is what the test reaches.
        s.add(Plan(id="d-other-GOLD", product_id=PROD_ID, policy_year_id=PY_OTHER,
                   code="GOLD", display_name="GOLD", status="confirmed"))
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


def test_apply_exclude_all_dependants(client: TestClient) -> None:
    """The third dependant option. An explicit empty list is NOT the same as "no
    opinion": it means "cover no dependants" and must persist as `[]`, or the
    sparse rule reads it as the cohort default and drops the deviation."""
    res = client.post(
        f"/api/v1/policy-years/{PY_ID}/bulk-plan-updates/apply",
        json={"product_code": "MED", "action": "set_plan", "target_plan_code": "GOLD",
              "selector": {"employee_ids": [EMP1]},
              "dependant_action": {"mode": "exclude_all"}},
    )
    assert res.status_code == 200, res.text
    with SessionLocal() as s:
        ov = load_overrides(s, PY_ID, [EMP1])[(EMP1, PROD_ID)]
        assert ov.covered_dependant_ids == []


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


def test_price_tag_counts_dependants_already_covered(client: TestClient) -> None:
    """A plain plan move (no dependant_action) must still price the dependants
    the member's EXISTING override covers.

    The ids being priced come from the override, not from the request, so a
    batch that loads dependants only when a dependant action is present prices
    every such member as if they covered nobody — writing an understated tag
    onto the override and into the preview's flex impact."""
    from app.models import FlexPricing
    from app.services.cohort_tiers import tier_key

    with SessionLocal() as s:
        s.get(Employee, EMP1).attribute_values = {"date_of_birth": "1980-03-15"}
        s.add(EmployeePlanOverride(
            employee_id=EMP1, policy_year_id=PY_ID, client_id=CLIENT_ID,
            product_id=PROD_ID, product_code="MED", plan_code="SILVER",
            covered_dependant_ids=[DEP1], source="manual",
        ))
        s.add(FlexPricing(
            policy_year_id=PY_ID, client_id=CLIENT_ID,
            pricing={"products": {PROD_ID: {
                "age_bands": [{"label": "all", "min": 0, "max": 200}],
                "price_tags": {tier_key(CAT_ID, "GOLD"): {"all": 1000}},
                "dependant": {
                    "mode": "family_group", "scheme": "ec_es_ef",
                    "family_tags": {tier_key(CAT_ID, "GOLD"): {"spouse": 400}},
                },
            }}},
        ))
        s.commit()
    try:
        body = client.post(
            f"/api/v1/policy-years/{PY_ID}/bulk-plan-updates/preview",
            json={"product_code": "MED", "action": "set_plan",
                  "target_plan_code": "GOLD",
                  "selector": {"employee_ids": [EMP1]}},
        ).json()
        # The spouse must be in the tag: employee 1000 + spouse 400.
        assert body["rows"][0]["flex_price_tag_after"] == 1400.0
    finally:
        with SessionLocal() as s:
            s.query(FlexPricing).delete()
            s.query(EmployeePlanOverride).delete()
            s.get(Employee, EMP1).attribute_values = {}
            s.commit()


def test_skipped_members_do_not_mark_the_batch_failed(client: TestClient) -> None:
    """A roster-wide rule necessarily sweeps in members the product doesn't
    cover, so `skipped` is the normal case, not a failure. Filing every
    filter-driven run as partially_failed would make the status meaningless."""
    res = client.post(
        f"/api/v1/policy-years/{PY_ID}/bulk-plan-updates/apply",
        json={"product_code": "MED", "action": "set_plan", "target_plan_code": "GOLD",
              "selector": {"employee_ids": [EMP1, EMP_NO_PROD]}},
    )
    body = res.json()
    assert body["counts"]["skipped"] == 1 and body["counts"]["applied"] == 1
    assert body["status"] == "applied"


def test_clearing_an_override_is_not_reported_as_unpriced(client: TestClient) -> None:
    """Moving members back to their cohort default writes NO tag by design.
    Counting those rows as unpriced told the broker the whole batch had no
    price when every row was a deliberate override deletion."""
    with SessionLocal() as s:
        s.add(EmployeePlanOverride(
            employee_id=EMP1, policy_year_id=PY_ID, client_id=CLIENT_ID,
            product_id=PROD_ID, product_code="MED", plan_code="GOLD",
            source="manual",
        ))
        s.commit()
    try:
        body = client.post(
            f"/api/v1/policy-years/{PY_ID}/bulk-plan-updates/preview",
            json={"product_code": "MED", "action": "set_plan",
                  "target_plan_code": "SILVER",  # == the cohort default
                  "selector": {"employee_ids": [EMP1]}},
        ).json()
        assert body["counts"]["would_apply"] == 1
        assert body["rows"][0]["override_cleared"] is True
        assert body["impact"]["unpriced"] == 0
    finally:
        with SessionLocal() as s:
            s.query(EmployeePlanOverride).delete()
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


def test_no_change_is_not_reported_as_applied(client: TestClient) -> None:
    """Folding no-ops into "applied" made "applied 412" mean anything between 8
    and 412 real changes — the number a broker checks the run against."""
    body = client.post(
        f"/api/v1/policy-years/{PY_ID}/bulk-plan-updates/preview",
        json={"product_code": "MED", "action": "set_plan", "target_plan_code": "SILVER",
              "selector": {"employee_ids": [EMP1, EMP2]}},
    ).json()
    # SILVER is already their cohort default and no override exists.
    assert body["counts"]["no_change"] == 2
    assert body["counts"]["would_apply"] == 0


def test_preview_states_the_price_tag_apply_will_write(client: TestClient) -> None:
    """The dry run used to compute no tag at all, so it structurally could not
    show what the real run would write."""
    from app.models import FlexPricing
    from app.services.cohort_tiers import tier_key

    with SessionLocal() as s:
        s.get(Employee, EMP1).attribute_values = {"date_of_birth": "1980-03-15"}
        s.add(FlexPricing(
            policy_year_id=PY_ID, client_id=CLIENT_ID,
            pricing={"products": {PROD_ID: {
                "age_bands": [{"label": "all", "min": 0, "max": 200}],
                "price_tags": {tier_key(CAT_ID, "GOLD"): {"all": 900}},
            }}},
        ))
        s.commit()
    try:
        body = client.post(
            f"/api/v1/policy-years/{PY_ID}/bulk-plan-updates/preview",
            json={"product_code": "MED", "action": "set_plan", "target_plan_code": "GOLD",
                  "selector": {"employee_ids": [EMP1]}},
        ).json()
        assert body["rows"][0]["flex_price_tag_after"] == 900.0
        assert body["impact"]["flex_price_tag_delta"] == 900.0
        assert body["impact"]["members_changing"] == 1
    finally:
        with SessionLocal() as s:
            s.query(FlexPricing).delete()
            s.get(Employee, EMP1).attribute_values = {}
            s.commit()


def test_preview_groups_rows_by_from_to(client: TestClient) -> None:
    body = client.post(
        f"/api/v1/policy-years/{PY_ID}/bulk-plan-updates/preview",
        json={"product_code": "MED", "action": "set_plan", "target_plan_code": "GOLD",
              "selector": {"employee_ids": [EMP1, EMP2]}},
    ).json()
    assert body["groups"] == [
        {"product_code": "MED", "from_plan": "SILVER", "to_plan": "GOLD",
         "declined_after": False, "reverted": False, "count": 2}
    ]


def test_preview_pages_its_rows(client: TestClient) -> None:
    res = client.post(
        f"/api/v1/policy-years/{PY_ID}/bulk-plan-updates/preview?offset=1&limit=1",
        json={"product_code": "MED", "action": "set_plan", "target_plan_code": "GOLD",
              "selector": {"employee_ids": [EMP1, EMP2]}},
    )
    body = res.json()
    assert body["rows_total"] == 2 and len(body["rows"]) == 1
    assert body["rows_offset"] == 1


def test_apply_refuses_a_stale_selection(client: TestClient) -> None:
    """The digest fingerprints WHO is selected and WHAT their coverage is, so an
    apply cannot land on a population that moved after the broker approved it."""
    preview = client.post(
        f"/api/v1/policy-years/{PY_ID}/bulk-plan-updates/preview",
        json={"product_code": "MED", "action": "set_plan", "target_plan_code": "GOLD",
              "selector": {"current_plan_codes": ["SILVER"]}},
    ).json()
    digest = preview["selection_digest"]
    assert digest

    # Somebody else moves one of them in the meantime.
    with SessionLocal() as s:
        s.add(EmployeePlanOverride(
            employee_id=EMP2, policy_year_id=PY_ID, client_id=CLIENT_ID,
            product_id=PROD_ID, product_code="MED", plan_code="GOLD", source="manual",
        ))
        s.commit()

    res = client.post(
        f"/api/v1/policy-years/{PY_ID}/bulk-plan-updates/apply",
        json={"product_code": "MED", "action": "set_plan", "target_plan_code": "GOLD",
              "selector": {"current_plan_codes": ["SILVER"]},
              "selection_digest": digest},
    )
    assert res.status_code == 409
    assert res.json()["detail"]["code"] == "selection_changed"
    with SessionLocal() as s:  # nothing was written by the refused apply
        assert (EMP1, PROD_ID) not in load_overrides(s, PY_ID, [EMP1])


def test_apply_accepts_a_fresh_digest(client: TestClient) -> None:
    body = {"product_code": "MED", "action": "set_plan", "target_plan_code": "GOLD",
            "selector": {"current_plan_codes": ["SILVER"]}}
    digest = client.post(
        f"/api/v1/policy-years/{PY_ID}/bulk-plan-updates/preview", json=body
    ).json()["selection_digest"]
    res = client.post(
        f"/api/v1/policy-years/{PY_ID}/bulk-plan-updates/apply",
        json={**body, "selection_digest": digest},
    )
    assert res.status_code == 200, res.text
    assert res.json()["counts"]["applied"] == 2


def test_unticking_a_row_does_not_invalidate_the_preview(client: TestClient) -> None:
    """The digest is taken BEFORE exclusions are subtracted.

    Otherwise removing three people from a 400-member preview would 409 the
    broker's own preview and cost a full re-run each time. Applying to a SUBSET
    of an approved population is safe; applying to one that moved underneath is
    what the guard is for."""
    base = {"product_code": "MED", "action": "set_plan", "target_plan_code": "GOLD",
            "selector": {"current_plan_codes": ["SILVER"]}}
    digest = client.post(
        f"/api/v1/policy-years/{PY_ID}/bulk-plan-updates/preview", json=base
    ).json()["selection_digest"]

    res = client.post(
        f"/api/v1/policy-years/{PY_ID}/bulk-plan-updates/apply",
        json={**base,
              "selector": {"current_plan_codes": ["SILVER"],
                           "exclude_employee_ids": [EMP2]},
              "selection_digest": digest},
    )
    assert res.status_code == 200, res.text
    assert res.json()["counts"]["applied"] == 1
    with SessionLocal() as s:
        ovs = load_overrides(s, PY_ID, [EMP1, EMP2])
        assert (EMP1, PROD_ID) in ovs and (EMP2, PROD_ID) not in ovs


def test_filter_selection_needs_no_ids(client: TestClient) -> None:
    """The whole point: a rule, not a list of people keyed one by one."""
    res = client.post(
        f"/api/v1/policy-years/{PY_ID}/bulk-plan-updates/apply",
        json={"product_code": "MED", "action": "set_plan", "target_plan_code": "GOLD",
              "selector": {"category_ids": [CAT_ID],
                           "exclude_employee_ids": [EMP2]}},
    )
    assert res.status_code == 200, res.text
    assert res.json()["counts"]["applied"] == 1
    with SessionLocal() as s:
        ovs = load_overrides(s, PY_ID, [EMP1, EMP2])
        assert (EMP1, PROD_ID) in ovs and (EMP2, PROD_ID) not in ovs


def test_apply_resyncs_underwriting(monkeypatch) -> None:
    """A bulk plan change moves eligible sum insured, which is what the NEL
    gates key on — it used to leave the underwriting queue stale. Scoped to the
    batch's members, never the whole roster."""
    import app.api.v1.bulk_plan_updates as router_mod

    seen: dict[str, object] = {}

    def _fake(db, policy_year, employee_ids=None):
        seen["ids"] = employee_ids
        return None

    monkeypatch.setattr(router_mod, "refresh_underwriting_cases", _fake)
    app.dependency_overrides[get_current_user] = _user
    try:
        with TestClient(app) as c:
            res = c.post(
                f"/api/v1/policy-years/{PY_ID}/bulk-plan-updates/apply",
                json={"product_code": "MED", "action": "set_plan",
                      "target_plan_code": "GOLD",
                      "selector": {"employee_ids": [EMP1]}},
            )
        assert res.status_code == 200, res.text
    finally:
        app.dependency_overrides.pop(get_current_user, None)
    assert seen["ids"] == {EMP1}


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
        # The existing coverage was projected from an enrolment, so this batch
        # overwrites what the member chose — an acknowledgement, not a block.
        json={"product_code": "MED", "action": "set_plan", "target_plan_code": "GOLD",
              "selector": {"employee_ids": [EMP1]},
              "acknowledge": ["enrollment_confirmed"]},
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
        assert ov.declined is True
        assert ov.covered_dependant_ids is None
        assert ov.dependant_option_ids is None


def test_plan_level_no_dependant_cover_uses_sibling_tier_and_clears_stale_cover(
    client: TestClient,
) -> None:
    """A bulk plan code resolves to its sibling category before reading exact
    participation. A no-cover target clears both stored dependant fields."""
    from app.models import FlexPricing
    from app.services.cohort_tiers import tier_key

    sibling = "00000000-0000-0000-0000-00000000d0ab"
    target_key = tier_key(sibling, "GOLD")
    with SessionLocal() as s:
        s.add(Category(
            id=sibling, policy_year_id=PY_ID, product_id=PROD_ID, priority=3,
            display_name="MED cohort (Option 2)",
            raw_description="MED cohort (Option 2)",
            participation_model="voluntary",
            participation_detail={"employee": "voluntary", "dependant": "voluntary"},
            plan_assignments={"plan_code": "GOLD"},
            source=SourceKind.system_generated.value,
            status=CategoryStatus.confirmed.value, human_modified=False,
        ))
        s.add(EmployeePlanOverride(
            employee_id=EMP1, policy_year_id=PY_ID, client_id=CLIENT_ID,
            product_id=PROD_ID, product_code="MED", plan_code="SILVER",
            covered_dependant_ids=[DEP1],
            dependant_option_ids={"spouse": "stale-level"},
            source="manual",
        ))
        s.add(FlexPricing(
            policy_year_id=PY_ID, client_id=CLIENT_ID,
            pricing={"products": {PROD_ID: {
                "age_bands": [{"label": "all", "min": 0, "max": 200}],
                "price_tags": {target_key: {"all": 321}},
                "dependant": {
                    "participation": {target_key: "none"},
                    "modes": {target_key: "per_pax"},
                    "per_pax": {target_key: {"flat": 99}},
                },
            }}},
        ))
        s.get(Employee, EMP1).attribute_values = {"date_of_birth": "1980-03-15"}
        s.commit()
    try:
        res = client.post(
            f"/api/v1/policy-years/{PY_ID}/bulk-plan-updates/apply",
            json={"product_code": "MED", "action": "set_plan",
                  "target_plan_code": "GOLD",
                  "selector": {"employee_ids": [EMP1]}},
        )
        assert res.status_code == 200, res.text
        with SessionLocal() as s:
            ov = load_overrides(s, PY_ID, [EMP1])[(EMP1, PROD_ID)]
            assert ov.tier_category_id == sibling
            assert ov.covered_dependant_ids is None
            assert ov.dependant_option_ids is None
            assert ov.flex_price_tag == 321.0
    finally:
        with SessionLocal() as s:
            s.query(FlexPricing).delete()
            s.query(Category).filter(Category.id == sibling).delete()
            s.get(Employee, EMP1).attribute_values = {}
            s.commit()


# ── Phase 3: change sets, revert, warnings + acknowledgement ────────────────


def _apply(client: TestClient, **body):
    return client.post(
        f"/api/v1/policy-years/{PY_ID}/bulk-plan-updates/apply", json=body
    )


def _preview(client: TestClient, **body):
    return client.post(
        f"/api/v1/policy-years/{PY_ID}/bulk-plan-updates/preview", json=body
    )


def test_revert_to_default_deletes_the_override(client: TestClient) -> None:
    """Reverting is its own action, not "set the plan the cohort happens to use".

    A broker undoing a mis-applied batch does not know the default's code, and
    writing it explicitly would leave the member pinned off future cohort
    changes — so the override has to be REMOVED and reported as a revert."""
    with SessionLocal() as s:
        s.add(EmployeePlanOverride(
            employee_id=EMP1, policy_year_id=PY_ID, client_id=CLIENT_ID,
            product_id=PROD_ID, product_code="MED", plan_code="GOLD",
            source="manual_admin",
        ))
        s.commit()
    res = _apply(
        client,
        changes=[{"product_code": "MED", "action": "revert_to_default"}],
        query={"employee_ids": [EMP1, EMP2]},
    )
    assert res.status_code == 200, res.text
    body = res.json()
    # EMP2 has no override, so reverting them changes nothing.
    assert body["counts"]["applied"] == 1 and body["counts"]["no_change"] == 1
    row = next(r for r in body["rows"] if r["outcome"] == "applied")
    assert row["override_cleared"] is True and row["to_plan"] == "SILVER"
    assert body["groups"][0]["reverted"] is True
    with SessionLocal() as s:
        assert load_overrides(s, PY_ID, [EMP1, EMP2]) == {}


def test_revert_rejects_a_target_plan_and_a_dependant_action(client: TestClient) -> None:
    for extra in ({"target_plan_code": "GOLD"},
                  {"dependant_action": {"mode": "include_all", "dependant_ids": []}}):
        res = _preview(
            client,
            changes=[{"product_code": "MED", "action": "revert_to_default", **extra}],
            query={"employee_ids": [EMP1]},
        )
        assert res.status_code == 422, res.text


def test_a_change_set_moves_two_products_in_one_run(client: TestClient) -> None:
    res = _apply(
        client,
        changes=[
            {"product_code": "MED", "action": "set_plan", "target_plan_code": "GOLD"},
            {"product_code": "DEN", "action": "set_plan", "target_plan_code": "PLUS"},
        ],
        query={"employee_ids": [EMP1, EMP2]},
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["counts"]["applied"] == 4  # 2 members x 2 products
    assert {g["product_code"] for g in body["groups"]} == {"MED", "DEN"}
    with SessionLocal() as s:
        ovs = load_overrides(s, PY_ID, [EMP1, EMP2])
        assert ovs[(EMP1, PROD_ID)].plan_code == "GOLD"
        assert ovs[(EMP1, OTHER_PROD_ID)].plan_code == "PLUS"
        assert ovs[(EMP2, OTHER_PROD_ID)].plan_code == "PLUS"


def test_a_change_set_is_one_transaction(client: TestClient, monkeypatch) -> None:
    """A fault anywhere in the run leaves NOTHING written.

    A batch that moved GHS and then failed on GTL is the state nobody can
    reason about afterwards, so the whole set shares one commit. The fault is
    injected at the underwriting re-sync — after every override has been
    written, which is exactly where a partial commit would show up."""
    from app.api.v1 import bulk_plan_updates as bulk_router

    def _boom(*_a, **_k):
        raise RuntimeError("underwriting exploded")

    monkeypatch.setattr(bulk_router, "refresh_underwriting_cases", _boom)
    failing = TestClient(app, raise_server_exceptions=False)
    res = failing.post(
        f"/api/v1/policy-years/{PY_ID}/bulk-plan-updates/apply",
        json={
            "changes": [
                {"product_code": "MED", "action": "set_plan", "target_plan_code": "GOLD"},
                {"product_code": "DEN", "action": "set_plan", "target_plan_code": "PLUS"},
            ],
            "query": {"employee_ids": [EMP1]},
        },
    )
    assert res.status_code == 500
    with SessionLocal() as s:
        assert load_overrides(s, PY_ID, [EMP1]) == {}
        assert s.query(BulkPlanUpdate).count() == 0


def test_a_product_may_appear_once_in_a_set(client: TestClient) -> None:
    res = _preview(
        client,
        changes=[
            {"product_code": "MED", "action": "set_plan", "target_plan_code": "GOLD"},
            {"product_code": "MED", "action": "decline"},
        ],
        query={"employee_ids": [EMP1]},
    )
    assert res.status_code == 422, res.text


def test_the_digest_covers_every_product_in_the_set(client: TestClient) -> None:
    """A change set has to prove the state of ALL its products. With only the
    first product hashed, somebody moving the second product's coverage between
    preview and apply went unnoticed."""
    body = {
        "changes": [
            {"product_code": "MED", "action": "set_plan", "target_plan_code": "GOLD"},
            {"product_code": "DEN", "action": "set_plan", "target_plan_code": "PLUS"},
        ],
        "query": {"employee_ids": [EMP1, EMP2]},
    }
    digest = _preview(client, **body).json()["selection_digest"]
    with SessionLocal() as s:  # somebody moves the SECOND product's coverage
        s.add(EmployeePlanOverride(
            employee_id=EMP2, policy_year_id=PY_ID, client_id=CLIENT_ID,
            product_id=OTHER_PROD_ID, product_code="DEN", plan_code="PLUS",
            source="manual_admin",
        ))
        s.commit()
    res = _apply(client, **body, selection_digest=digest)
    assert res.status_code == 409
    assert res.json()["detail"]["code"] == "selection_changed"


def test_outside_cohort_warns_and_needs_acknowledging(client: TestClient) -> None:
    """BRONZE belongs to another MED cohort, so it is not a tier these members
    can elect. The change is still allowed — a slip typo or a negotiated
    exception is a real reason to make it — but not silently."""
    body = {
        "changes": [
            {"product_code": "MED", "action": "set_plan", "target_plan_code": "BRONZE"}
        ],
        "query": {"employee_ids": [EMP1]},
    }
    preview = _preview(client, **body).json()
    codes = {w["code"]: w for w in preview["warnings"]}
    assert codes["outside_cohort"]["count"] == 1
    assert codes["outside_cohort"]["requires_ack"] is True
    assert preview["rows"][0]["warnings"] == ["outside_cohort"]

    blocked = _apply(client, **body)
    assert blocked.status_code == 409
    detail = blocked.json()["detail"]
    assert detail["code"] == "unacknowledged_warnings"
    assert [w["code"] for w in detail["warnings"]] == ["outside_cohort"]
    with SessionLocal() as s:  # a refused apply writes NOTHING
        assert load_overrides(s, PY_ID, [EMP1]) == {}
        assert s.query(BulkPlanUpdate).count() == 0

    ok = _apply(client, **body, acknowledge=["outside_cohort"])
    assert ok.status_code == 200, ok.text
    with SessionLocal() as s:
        assert load_overrides(s, PY_ID, [EMP1])[(EMP1, PROD_ID)].plan_code == "BRONZE"
        assert s.query(BulkPlanUpdate).one().acknowledged == ["outside_cohort"]


def test_a_plan_the_cohort_offers_raises_no_cohort_warning(client: TestClient) -> None:
    preview = _preview(
        client,
        changes=[{"product_code": "MED", "action": "set_plan", "target_plan_code": "GOLD"}],
        query={"employee_ids": [EMP1]},
    ).json()
    assert [w["code"] for w in preview["warnings"]] == []


def test_open_enrollment_warns(client: TestClient) -> None:
    from datetime import UTC, datetime

    from app.models.enrollment import Enrollment
    from app.models.enrollment_window import EnrollmentWindow, WindowStatus

    with SessionLocal() as s:
        s.add(EnrollmentWindow(
            id="d-win-1", policy_year_id=PY_ID, client_id=CLIENT_ID,
            name="Open period", status=WindowStatus.open,
            opens_at=datetime(2030, 1, 1, tzinfo=UTC),
            closes_at=datetime(2030, 3, 1, tzinfo=UTC),
        ))
        s.flush()
        s.add(Enrollment(
            id="d-enr-1", window_id="d-win-1", policy_year_id=PY_ID,
            client_id=CLIENT_ID, employee_id=EMP1,
        ))
        s.commit()
    try:
        preview = _preview(
            client,
            changes=[{"product_code": "MED", "action": "set_plan",
                      "target_plan_code": "GOLD"}],
            query={"employee_ids": [EMP1, EMP2]},
        ).json()
        codes = {w["code"]: w["count"] for w in preview["warnings"]}
        assert codes == {"open_enrollment": 1}  # EMP2 has no enrolment
    finally:
        with SessionLocal() as s:
            s.query(Enrollment).delete()
            s.query(EnrollmentWindow).delete()
            s.commit()


def test_flex_overdraft_warns_on_the_whole_wallet(client: TestClient) -> None:
    """The overdraft is a whole-member total, so it counts the products the
    batch does NOT touch as well — they keep drawing exactly what they draw
    today, and a check that ignored them would clear a member who is already
    spent up."""
    from app.models import FlexPricing
    from app.services.cohort_tiers import tier_key

    with SessionLocal() as s:
        emp = s.get(Employee, EMP1)
        emp.attribute_values = {"date_of_birth": "1980-03-15"}
        emp.flex_wallet_amount = 1000.0
        s.add(FlexPricing(
            policy_year_id=PY_ID, client_id=CLIENT_ID,
            pricing={"products": {
                PROD_ID: {
                    "age_bands": [{"label": "all", "min": 0, "max": 200}],
                    "price_tags": {tier_key(CAT_ID, "GOLD"): {"all": 800}},
                },
                OTHER_PROD_ID: {
                    "age_bands": [{"label": "all", "min": 0, "max": 200}],
                    "price_tags": {tier_key(CAT_DEN, "BASIC"): {"all": 700}},
                },
            }},
        ))
        s.commit()
    try:
        # 800 (the MED move) + 700 (DEN, untouched and on its default) > 1000.
        preview = _preview(
            client,
            changes=[{"product_code": "MED", "action": "set_plan",
                      "target_plan_code": "GOLD"}],
            query={"employee_ids": [EMP1]},
        ).json()
        codes = {w["code"] for w in preview["warnings"]}
        assert "flex_overdraft" in codes
        assert "flex_overdraft" in preview["rows"][0]["warnings"]
    finally:
        with SessionLocal() as s:
            s.query(FlexPricing).delete()
            emp = s.get(Employee, EMP1)
            emp.attribute_values = {}
            emp.flex_wallet_amount = None
            s.commit()


def test_no_flex_configured_reports_nothing_unpriced(client: TestClient) -> None:
    """With no flex in the year there are no price tags to resolve, so every
    changing row is not "unpriced" — it simply has no price."""
    preview = _preview(
        client,
        changes=[{"product_code": "MED", "action": "set_plan", "target_plan_code": "GOLD"}],
        query={"employee_ids": [EMP1, EMP2]},
    ).json()
    assert preview["impact"]["unpriced"] == 0
    assert all("unpriced" not in r["warnings"] for r in preview["rows"])


# ── Phase 4: idempotency, history, undo ─────────────────────────────────────


def test_replaying_a_request_id_applies_once(client: TestClient) -> None:
    body = {
        "changes": [{"product_code": "MED", "action": "set_plan",
                     "target_plan_code": "GOLD"}],
        "query": {"employee_ids": [EMP1]},
        "request_id": "attempt-1",
    }
    first = _apply(client, **body).json()
    second = _apply(client, **body).json()
    assert second["id"] == first["id"]
    assert second["replayed"] is True and first["replayed"] is False
    assert second["counts"] == first["counts"]
    with SessionLocal() as s:
        assert s.query(BulkPlanUpdate).count() == 1


def test_history_lists_and_details_a_batch(client: TestClient) -> None:
    applied = _apply(
        client,
        changes=[{"product_code": "MED", "action": "set_plan", "target_plan_code": "GOLD"}],
        query={"employee_ids": [EMP1], "category_ids": [CAT_ID]},
    ).json()

    listing = client.get(f"/api/v1/policy-years/{PY_ID}/bulk-plan-updates").json()
    assert [b["id"] for b in listing] == [applied["id"]]
    assert listing[0]["product_codes"] == ["MED"]
    # The cohort filter matched BOTH members — the explicit id ADDS to whatever
    # the filters found, it does not narrow it.
    assert listing[0]["restorable"] == 2 and listing[0]["undone_by"] is None

    detail = client.get(f"/api/v1/bulk-plan-updates/{applied['id']}").json()
    # The stored selection is the RULE, so the batch is re-runnable in the
    # builder rather than being a frozen list of ids.
    assert detail["query"]["category_ids"] == [CAT_ID]
    assert detail["changes"][0]["target_plan_code"] == "GOLD"
    assert detail["rows_total"] == 2 and detail["rows_truncated"] is False


def test_undo_restores_the_previous_coverage(client: TestClient) -> None:
    with SessionLocal() as s:
        s.add(EmployeePlanOverride(
            employee_id=EMP1, policy_year_id=PY_ID, client_id=CLIENT_ID,
            product_id=PROD_ID, product_code="MED", plan_code="BRONZE",
            source="enrollment", source_ref="an-enrollment",
        ))
        s.commit()
    applied = _apply(
        client,
        changes=[{"product_code": "MED", "action": "set_plan", "target_plan_code": "GOLD"}],
        query={"employee_ids": [EMP1, EMP2]},
        acknowledge=["enrollment_confirmed"],
    ).json()

    res = client.post(f"/api/v1/bulk-plan-updates/{applied['id']}/undo")
    assert res.status_code == 200, res.text
    assert res.json()["counts"]["applied"] == 2
    with SessionLocal() as s:
        ovs = load_overrides(s, PY_ID, [EMP1, EMP2])
        restored = ovs[(EMP1, PROD_ID)]
        # Provenance comes back too: coverage the member chose in an enrolment
        # must not read as something a bulk tool picked.
        assert restored.plan_code == "BRONZE"
        assert restored.source == "enrollment" and restored.source_ref == "an-enrollment"
        # EMP2 had no override before the batch, so the undo removes the one it
        # created rather than writing the cohort default back explicitly.
        assert (EMP2, PROD_ID) not in ovs
    # The undo is its own batch, pointing at the one it reversed — history is
    # append-only, so the original stays and is marked as undone.
    listing = client.get(f"/api/v1/policy-years/{PY_ID}/bulk-plan-updates").json()
    assert {b["undo_of"] for b in listing} == {None, applied["id"]}
    assert next(b for b in listing if b["id"] == applied["id"])["undone_by"]


def test_undo_skips_a_pair_somebody_moved_since(client: TestClient) -> None:
    applied = _apply(
        client,
        changes=[{"product_code": "MED", "action": "set_plan", "target_plan_code": "GOLD"}],
        query={"employee_ids": [EMP1, EMP2]},
    ).json()
    with SessionLocal() as s:  # somebody edits EMP2's coverage afterwards
        ov = load_overrides(s, PY_ID, [EMP2])[(EMP2, PROD_ID)]
        ov.plan_code = "BRONZE"
        s.commit()

    body = client.post(f"/api/v1/bulk-plan-updates/{applied['id']}/undo").json()
    assert body["counts"] == {"applied": 1, "skipped": 1, "error": 0}
    assert body["superseded"][0]["staff_id"] == "D-2"
    with SessionLocal() as s:
        ovs = load_overrides(s, PY_ID, [EMP1, EMP2])
        assert (EMP1, PROD_ID) not in ovs  # restored to "no override"
        assert ovs[(EMP2, PROD_ID)].plan_code == "BRONZE"  # left alone


def test_a_batch_can_only_be_undone_once(client: TestClient) -> None:
    applied = _apply(
        client,
        changes=[{"product_code": "MED", "action": "set_plan", "target_plan_code": "GOLD"}],
        query={"employee_ids": [EMP1]},
    ).json()
    assert client.post(f"/api/v1/bulk-plan-updates/{applied['id']}/undo").status_code == 200
    again = client.post(f"/api/v1/bulk-plan-updates/{applied['id']}/undo")
    assert again.status_code == 409
    assert again.json()["detail"]["code"] == "already_undone"


def test_an_undo_cannot_itself_be_undone(client: TestClient) -> None:
    applied = _apply(
        client,
        changes=[{"product_code": "MED", "action": "set_plan", "target_plan_code": "GOLD"}],
        query={"employee_ids": [EMP1]},
    ).json()
    undo = client.post(f"/api/v1/bulk-plan-updates/{applied['id']}/undo").json()
    res = client.post(f"/api/v1/bulk-plan-updates/{undo['id']}/undo")
    assert res.status_code == 409
    assert res.json()["detail"]["code"] == "cannot_undo_an_undo"


def test_a_product_with_no_cover_figure_is_not_reported_unresolved(
    client: TestClient,
) -> None:
    """MED quotes no sum insured — it is a reimbursement product whose
    entitlement is the schedule of benefits. Counting those rows as "no
    per-member figure" told the broker every one of 506 rows had a data
    problem, when it is simply the shape of GP/SP/dental/hospital cover."""
    preview = _preview(
        client,
        changes=[{"product_code": "MED", "action": "set_plan", "target_plan_code": "GOLD"}],
        query={"employee_ids": [EMP1, EMP2]},
    ).json()
    assert preview["impact"]["financials_unresolved"] == 0
    assert preview["rows"][0]["sum_insured_after"] is None


def test_a_salary_multiple_with_no_salary_is_reported_unresolved(
    client: TestClient,
) -> None:
    """A tier that DOES quote cover but will not reduce to this member is the
    gap worth reporting — and the figure itself must stay None rather than
    printing the cohort aggregate as one person's cover."""
    with SessionLocal() as s:
        for cid, plan in ((CAT_ID, "SILVER"), (CAT_FOREIGN, "BRONZE")):
            cat = s.get(Category, cid)
            cat.plan_assignments = {
                "plan_code": plan,
                "basis": "24 times basic monthly salary",
                "sum_insured": 970_000,  # the COHORT aggregate
            }
        s.commit()
    try:
        preview = _preview(
            client,
            changes=[{"product_code": "MED", "action": "set_plan",
                      "target_plan_code": "GOLD"}],
            query={"employee_ids": [EMP1]},
        ).json()
        assert preview["impact"]["financials_unresolved"] == 1
        assert preview["impact"]["sum_insured_delta"] == 0
        assert preview["rows"][0]["sum_insured_after"] is None
    finally:
        with SessionLocal() as s:
            for cid, plan in ((CAT_ID, "SILVER"), (CAT_FOREIGN, "BRONZE")):
                s.get(Category, cid).plan_assignments = {"plan_code": plan}
            s.commit()


# ── Fixes from the 2026-08-04 code review ──────────────────────────────────


def test_a_reused_request_id_with_a_different_change_is_refused(
    client: TestClient,
) -> None:
    """A replay answers "did that go through?" with `applied: N`. Returning that
    for a body which was never run reports work that did not happen — worse
    than applying twice."""
    first = _apply(
        client,
        changes=[{"product_code": "MED", "action": "set_plan", "target_plan_code": "GOLD"}],
        query={"employee_ids": [EMP1]},
        request_id="reused",
    )
    assert first.status_code == 200, first.text
    res = _apply(
        client,
        changes=[{"product_code": "MED", "action": "decline"}],
        query={"employee_ids": [EMP1]},
        request_id="reused",
    )
    assert res.status_code == 409
    assert res.json()["detail"]["code"] == "request_id_reused"
    with SessionLocal() as s:  # the decline never happened
        assert load_overrides(s, PY_ID, [EMP1])[(EMP1, PROD_ID)].plan_code == "GOLD"


def test_a_request_id_is_scoped_to_the_benefit_year(client: TestClient) -> None:
    """Replay is matched per (client, request_id); without the year in the
    check, reusing an id against a different year returns another year's
    counts as if this one had applied."""
    body = {
        "changes": [{"product_code": "MED", "action": "set_plan",
                     "target_plan_code": "GOLD"}],
        "query": {"employee_ids": [EMP1]},
        "request_id": "cross-year",
    }
    assert _apply(client, **body).status_code == 200
    other = client.post(
        f"/api/v1/policy-years/{PY_OTHER}/bulk-plan-updates/apply", json=body
    )
    # Same client, same id, different year → not a replay of that batch.
    assert other.status_code == 409
    assert other.json()["detail"]["code"] == "request_id_reused"


def test_a_batch_reports_what_undo_cannot_put_back(client: TestClient, monkeypatch) -> None:
    """`restore` is one entry per written (member, PRODUCT) pair, so a
    multi-product batch over a big roster exceeds the storage cap. An
    unreported cap has the confirm dialog promise the whole batch while the
    tail silently stays on its new coverage."""
    from app.api.v1 import bulk_plan_updates as bulk_router

    monkeypatch.setattr(bulk_router, "MAX_STORED_ROWS", 1)
    applied = _apply(
        client,
        changes=[{"product_code": "MED", "action": "set_plan", "target_plan_code": "GOLD"}],
        query={"employee_ids": [EMP1, EMP2]},
    ).json()
    listing = client.get(f"/api/v1/policy-years/{PY_ID}/bulk-plan-updates").json()
    row = next(b for b in listing if b["id"] == applied["id"])
    assert row["restorable"] == 1 and row["not_restorable"] == 1

    # And the undo really does only restore the one it recorded.
    undo = client.post(f"/api/v1/bulk-plan-updates/{applied['id']}/undo").json()
    assert undo["counts"]["applied"] == 1
    with SessionLocal() as s:
        assert len(load_overrides(s, PY_ID, [EMP1, EMP2])) == 1


def test_the_premium_delta_is_grossed_up_like_every_other_surface(
    client: TestClient,
) -> None:
    """Stored premiums are GST-EXCLUSIVE and the benefit statement / enrolment
    page gross them up. A raw figure here sits 9% below the same movement
    everywhere else the broker can read it."""
    from app.models import ProductTerm

    SIBLING = "00000000-0000-0000-0000-00000000d0aa"
    with SessionLocal() as s:
        # A richer tier of the SAME cohort, so the move resolves to a category
        # with its own basis — which is what gives a bare plan code a premium.
        s.add(Category(
            id=SIBLING, policy_year_id=PY_ID, product_id=PROD_ID, priority=3,
            display_name="MED cohort (Option 2)",
            raw_description="MED cohort (Option 2)",
            plan_assignments={
                "plan_code": "GOLD", "basis": "200000", "premium_rate": 10.0,
                "rate_basis": "per_1000_si",
            },
            source=SourceKind.system_generated.value,
            status=CategoryStatus.confirmed.value, human_modified=False,
        ))
        s.get(Category, CAT_ID).plan_assignments = {
            "plan_code": "SILVER", "basis": "100000", "premium_rate": 10.0,
            "rate_basis": "per_1000_si",
        }
        s.commit()

    def premium_delta() -> float:
        return _preview(
            client,
            changes=[{"product_code": "MED", "action": "set_plan",
                      "target_plan_code": "GOLD"}],
            query={"employee_ids": [EMP1]},
        ).json()["impact"]["annual_premium_delta"]

    try:
        bare = premium_delta()
        # 200,000/1000 x 10 - 100,000/1000 x 10.
        assert bare == pytest.approx(1000.0)
        with SessionLocal() as s:
            s.add(ProductTerm(
                policy_year_id=PY_ID, product_id=PROD_ID,
                gst_included=True, gst_rate=9.0,  # a PERCENTAGE (0-100), not a fraction
            ))
            s.commit()
        assert premium_delta() == pytest.approx(1090.0)
    finally:
        with SessionLocal() as s:
            s.query(ProductTerm).delete()
            s.query(Category).filter(Category.id == SIBLING).delete()
            s.get(Category, CAT_ID).plan_assignments = {"plan_code": "SILVER"}
            s.commit()
