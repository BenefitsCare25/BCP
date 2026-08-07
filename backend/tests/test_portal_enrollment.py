"""Member-facing enrollment (/portal/enrollment) + its employee-view preview.

Covers the full interconnect: broker opens a window → member sees it in the
portal (options scoped to their cohort), upgrades their plan + names a covered
dependant, submits → broker confirms → the election projects into the SAME
EmployeePlanOverride rows the broker flow writes. Also: member edits are
blocked after finalization, window toggles are honored, audit rows carry
actor_type="member", and the broker preview returns the member payload without
materializing an enrollment row.
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest

TEST_DB = Path(__file__).parent / "_test_portal_enrollment.db"
os.environ["INSPRO_DATABASE_URL"] = f"sqlite:///{TEST_DB}"

from datetime import date  # noqa: E402

from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import select  # noqa: E402

from app.core.auth import DEMO_BROKER_FIRM_ID, CurrentUser, get_current_user  # noqa: E402
from app.core.portal_auth import issue_member_token  # noqa: E402
from app.db.base import Base  # noqa: E402
from app.db.session import SessionLocal, engine  # noqa: E402
from app.main import app  # noqa: E402
from app.models import (  # noqa: E402
    AuditLog,
    Category,
    Client,
    Dependant,
    Employee,
    EmployeePlanOverride,
    Enrollment,
    EnrollmentElection,
    EnrollmentWindow,
    LeaveElection,
    LeavePolicy,
    MemberAccount,
    Plan,
    PolicyYear,
    Product,
)
from app.models.category import CategoryStatus, SourceKind  # noqa: E402
from app.models.member_account import MEMBER_STATUS_ACTIVE  # noqa: E402
from app.models.policy_year import PolicyYearStatus  # noqa: E402
from app.services.coverage_resolver import load_overrides  # noqa: E402
from scripts.seed_demo import seed  # noqa: E402

CLIENT_ID = "00000000-0000-0000-0000-00000000pe00"
PY_ID = "00000000-0000-0000-0000-00000000pe01"
PROD_ID = "00000000-0000-0000-0000-00000000pe02"
CAT_ID = "00000000-0000-0000-0000-00000000pe03"
EMP1 = "00000000-0000-0000-0000-00000000pe04"
DEP1 = "00000000-0000-0000-0000-00000000pe05"
ACC1 = "00000000-0000-0000-0000-00000000pe06"


def _broker() -> CurrentUser:
    return CurrentUser(
        user_id="00000000-0000-0000-0000-00000000peff",
        broker_firm_id=DEMO_BROKER_FIRM_ID, client_id=CLIENT_ID, role="broker_admin",
    )


@pytest.fixture(scope="module", autouse=True)
def _setup_db():
    if TEST_DB.exists():
        TEST_DB.unlink()
    Base.metadata.create_all(bind=engine)
    seed()
    with SessionLocal() as s:
        s.add(Client(id=CLIENT_ID, name="Portal Elect Co", broker_firm_id=DEMO_BROKER_FIRM_ID))
        s.flush()
        # ACTIVE policy year — resolve_member_employee only sees the active one.
        s.add(PolicyYear(
            id=PY_ID, client_id=CLIENT_ID, year=2029,
            start_date=date(2029, 1, 1), end_date=date(2029, 12, 31),
            status=PolicyYearStatus.active,
        ))
        s.flush()
        s.add(Product(
            id=PROD_ID, client_id=CLIENT_ID, code="MED",
            display_name="Medical", insurer="ACME", has_dependants=True,
        ))
        s.flush()
        for code, name in (("SILVER", "Silver"), ("GOLD", "Gold")):
            s.add(Plan(
                id=f"pe-plan-{code}", product_id=PROD_ID, policy_year_id=PY_ID,
                code=code, display_name=name, status="confirmed",
            ))
        s.add(Category(
            id=CAT_ID, policy_year_id=PY_ID, product_id=PROD_ID, priority=1,
            display_name="All staff", raw_description="All staff",
            plan_assignments={"plan_code": "SILVER"},
            source=SourceKind.system_generated.value,
            status=CategoryStatus.confirmed.value, human_modified=False,
        ))
        s.add(MemberAccount(
            id=ACC1, client_id=CLIENT_ID, email="pe1@a.test", staff_id="PE-1",
            status=MEMBER_STATUS_ACTIVE,
        ))
        s.flush()
        s.add(Employee(
            id=EMP1, client_id=CLIENT_ID, policy_year_id=PY_ID,
            staff_id="PE-1", employee_name="Portal Emp",
            member_account_id=ACC1,
            attribute_values={}, derived_attribute_values={},
            matched_categories=[{"category_id": CAT_ID, "product_code": "MED",
                                 "method": "rule", "confidence": 1.0}],
            source="csv_import", status="active",
        ))
        s.flush()
        s.add(Dependant(
            id=DEP1, client_id=CLIENT_ID, policy_year_id=PY_ID, employee_id=EMP1,
            attribute_values={"name": "Kid One", "relationship": "child"},
            link_method="staff_id", status="active",
        ))
        s.add(LeavePolicy(
            id="pe-leave", policy_year_id=PY_ID, client_id=CLIENT_ID,
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
    yield
    with SessionLocal() as s:
        for model in (LeaveElection, EnrollmentElection, Enrollment,
                      EmployeePlanOverride, EnrollmentWindow):
            s.query(model).delete()
        s.query(AuditLog).delete()
        s.commit()


@pytest.fixture
def broker() -> TestClient:
    app.dependency_overrides[get_current_user] = _broker
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.pop(get_current_user, None)


def _member_auth() -> dict[str, str]:
    token, _ = issue_member_token(ACC1, CLIENT_ID)
    return {"Authorization": f"Bearer {token}"}


def _make_window(broker: TestClient, **over) -> str:
    body = {
        "name": "OE", "window_type": "open",
        "opens_at": "2020-01-01T00:00:00Z", "closes_at": "2035-01-01T00:00:00Z",
        "allow_leave": True,
    }
    body.update(over)
    wid = broker.post(
        f"/api/v1/policy-years/{PY_ID}/enrollment-windows", json=body
    ).json()["id"]
    assert broker.post(f"/api/v1/enrollment-windows/{wid}/open").status_code == 200
    return wid


def test_no_open_window_returns_empty(broker: TestClient) -> None:
    res = broker.get("/api/v1/portal/enrollment", headers=_member_auth())
    assert res.status_code == 200
    assert res.json() == {"window": None, "enrollment": None, "options": None}
    me = broker.get("/api/v1/portal/me", headers=_member_auth()).json()
    assert me["enrollment_open"] is False


def test_member_upgrade_submit_broker_confirm(broker: TestClient) -> None:
    wid = _make_window(broker)

    me = broker.get("/api/v1/portal/me", headers=_member_auth()).json()
    assert me["enrollment_open"] is True

    # Member sees the window + their pre-created enrollment + cohort options.
    res = broker.get("/api/v1/portal/enrollment", headers=_member_auth())
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["window"]["id"] == wid
    assert body["enrollment"]["status"] == "not_started"
    med = next(p for p in body["options"]["products"] if p["product_code"] == "MED")
    assert med["baseline_plan_code"] == "SILVER"
    plan_codes = {t["plan_code"] for t in med["tiers"]}
    assert {"SILVER", "GOLD"} <= plan_codes

    # Member upgrades to GOLD and names their child as covered.
    put = broker.put(
        "/api/v1/portal/enrollment/elections",
        json={"elections": [{"product_code": "MED", "plan_code": "GOLD",
                             "covered_dependant_ids": [DEP1]}]},
        headers=_member_auth(),
    )
    assert put.status_code == 200, put.text
    election = put.json()["elections"][0]
    assert election["elected_plan_code"] == "GOLD"
    assert election["action"] in ("upgrade", "downgrade")
    assert election["covered_dependant_ids"] == [DEP1]

    # Member buys leave, then submits.
    lv = broker.put(
        "/api/v1/portal/enrollment/leave",
        json={"action": "buy", "days": 2}, headers=_member_auth(),
    )
    assert lv.status_code == 200 and lv.json()["leave"]["days"] == 2
    sub = broker.post(
        "/api/v1/portal/enrollment/submit", json={}, headers=_member_auth()
    )
    assert sub.status_code == 200 and sub.json()["status"] == "submitted"
    assert sub.json()["elections"][0]["previous_plan_code"] == "SILVER"

    # The member-submitted enrollment lands in the broker roster as submitted.
    roster = broker.get(f"/api/v1/enrollment-windows/{wid}/enrollments").json()
    row = next(i for i in roster["items"] if i["staff_id"] == "PE-1")
    assert row["status"] == "submitted"

    # Broker confirms → projects into the same overrides the broker flow uses.
    conf = broker.post(f"/api/v1/enrollments/{row['id']}/confirm")
    assert conf.status_code == 200 and conf.json()["status"] == "confirmed"
    with SessionLocal() as s:
        ov = load_overrides(s, PY_ID, [EMP1])[(EMP1, PROD_ID)]
        assert ov.plan_code == "GOLD"
        assert ov.covered_dependant_ids == [DEP1]

    # Once finalized, the member can no longer edit — broker must reopen.
    blocked = broker.put(
        "/api/v1/portal/enrollment/elections",
        json={"elections": [{"product_code": "MED", "plan_code": "SILVER"}]},
        headers=_member_auth(),
    )
    assert blocked.status_code == 409

    # Audit trail: the member mutations carry actor_type="member".
    with SessionLocal() as s:
        rows = s.execute(
            select(AuditLog).where(
                AuditLog.action == "update_enrollment_elections"
            )
        ).scalars().all()
        assert rows and all(r.actor_type == "member" for r in rows)
        assert all(r.member_account_id == ACC1 for r in rows)


def test_member_get_lazily_creates_enrollment(broker: TestClient) -> None:
    _make_window(broker)
    # Simulate an employee added after the window opened: drop their row.
    with SessionLocal() as s:
        s.query(Enrollment).filter(Enrollment.employee_id == EMP1).delete()
        s.commit()
    res = broker.get("/api/v1/portal/enrollment", headers=_member_auth())
    assert res.status_code == 200
    enr = res.json()["enrollment"]
    assert enr is not None and enr["status"] == "not_started"
    assert enr["baseline_snapshot"]["products"]["MED"]["plan_code"] == "SILVER"


def test_window_toggles_block_member_edits(broker: TestClient) -> None:
    _make_window(broker, allow_plan_change=False, allow_leave=False)
    put = broker.put(
        "/api/v1/portal/enrollment/elections",
        json={"elections": [{"product_code": "MED", "plan_code": "GOLD"}]},
        headers=_member_auth(),
    )
    assert put.status_code == 409
    lv = broker.put(
        "/api/v1/portal/enrollment/leave",
        json={"action": "buy", "days": 1}, headers=_member_auth(),
    )
    assert lv.status_code == 409


def test_dependant_changes_disabled_blocks_covered_ids(broker: TestClient) -> None:
    _make_window(broker, allow_dependant_changes=False)
    put = broker.put(
        "/api/v1/portal/enrollment/elections",
        json={"elections": [{"product_code": "MED", "plan_code": "GOLD",
                             "covered_dependant_ids": [DEP1]}]},
        headers=_member_auth(),
    )
    assert put.status_code == 409


def test_member_cannot_cover_foreign_dependant(broker: TestClient) -> None:
    _make_window(broker)
    put = broker.put(
        "/api/v1/portal/enrollment/elections",
        json={"elections": [{"product_code": "MED", "plan_code": "GOLD",
                             "covered_dependant_ids": ["not-my-dependant"]}]},
        headers=_member_auth(),
    )
    assert put.status_code == 422


def test_preview_matches_portal_and_never_creates(broker: TestClient) -> None:
    _make_window(broker)
    # Preview before the member ever visits: enrollment row exists (open_window
    # pre-created it) — drop it to prove the preview does NOT recreate.
    with SessionLocal() as s:
        s.query(Enrollment).filter(Enrollment.employee_id == EMP1).delete()
        s.commit()
    preview = broker.get(f"/api/v1/employees/{EMP1}/portal-preview/enrollment")
    assert preview.status_code == 200
    assert preview.json()["enrollment"] is None
    assert preview.json()["window"] is not None
    with SessionLocal() as s:
        assert s.execute(
            select(Enrollment).where(Enrollment.employee_id == EMP1)
        ).scalar_one_or_none() is None

    # Member GET materializes their row; preview then mirrors it exactly.
    portal = broker.get("/api/v1/portal/enrollment", headers=_member_auth())
    preview = broker.get(f"/api/v1/employees/{EMP1}/portal-preview/enrollment")
    assert preview.status_code == portal.status_code == 200
    assert preview.json() == portal.json()

    # Preview context flags the open window.
    ctx = broker.get(f"/api/v1/employees/{EMP1}/portal-preview").json()
    assert ctx["enrollment_open"] is True


def test_member_safe_options_scrubs_premiums() -> None:
    """A member electing a plan must not see what the employer is charged.

    `member_statement.py` already nulls premium figures before the portal sees a
    benefit statement, but the enrollment surface called
    `build_enrollment_options` directly and served the broker's payload — so the
    member's own election screen printed "Rate (per $1k SI)" and the annual
    premium beside their flex wallet. Two surfaces disagreeing about what a
    member may see is how a leak survives review.

    Asserted against a CONSTRUCTED payload rather than the seed: the demo
    fixture's tiers happen to carry no financials, so an end-to-end assertion
    would pass whether or not the scrub existed.
    """
    from app.schemas.api import PlanFinancials
    from app.schemas.enrollment import (
        CohortTierOut,
        EnrollmentOptionsOut,
        ProductTierSetOut,
    )
    from app.services.enrollment_elections import _member_safe_options

    options = EnrollmentOptionsOut(
        products=[
            ProductTierSetOut(
                product_id="prod-1",
                product_code="GTL",
                employee_participation="voluntary",
                dependant_participation=None,
                baseline_tier_category_id="cat-1",
                baseline_plan_code="PLAN A",
                allow_plan_change=True,
                can_decline=True,
                tiers=[
                    CohortTierOut(
                        key="cat-1|PLAN A",
                        tier_category_id="cat-1",
                        plan_code="PLAN A",
                        label="Plan A",
                        participation="voluntary",
                        direction="same",
                        is_baseline=True,
                        financials=PlanFinancials(
                            num_employees=51,
                            basis="12 times basic monthly salary",
                            sum_insured=250_000.0,
                            premium_rate=454.0,
                            annual_premium=1_234.5,
                            rate_basis="per_1000_si",
                            estimated_annual_earnings=60_000.0,
                            gst_included=True,
                        ),
                        price_tag=120.0,
                    )
                ],
            )
        ]
    )

    fin = _member_safe_options(options).products[0].tiers[0].financials
    assert fin is not None
    for field in (
        "num_employees",
        "basis",
        "premium_rate",
        "annual_premium",
        "rate_basis",
        "rate_tiers",
        "dependant_rate",
        "estimated_annual_earnings",
        "voluntary_rates",
    ):
        assert getattr(fin, field) is None, f"leaked {field}"
    assert fin.gst_included is False, "the GST badge only means anything beside a premium"

    # What a member actually decides on survives untouched.
    assert fin.sum_insured == 250_000.0
    assert _member_safe_options(options).products[0].tiers[0].price_tag == 120.0

    # The source object is not mutated — the broker's own payload is built from
    # the same builder and must keep its premiums.
    assert options.products[0].tiers[0].financials.premium_rate == 454.0


def test_preview_also_hides_premiums(broker: TestClient) -> None:
    """The employee-view preview must show exactly what the member sees — so it
    goes through the same gate, not a parallel one."""
    _make_window(broker)
    broker.get("/api/v1/portal/enrollment", headers=_member_auth())
    preview = broker.get(f"/api/v1/employees/{EMP1}/portal-preview/enrollment")
    portal = broker.get("/api/v1/portal/enrollment", headers=_member_auth())
    assert preview.status_code == 200
    assert preview.json() == portal.json()


# ── Broker-managed periods (member_self_service off) ─────────────────────────
#
# The period stays OPEN — brokers elect on members' behalf and confirm as
# normal — while the portal's enrolment surface goes dark. Every member-facing
# read AND write has to honour it: hiding only the marker would leave a member
# who bookmarked /portal/enrollment able to elect.


def test_broker_managed_window_is_invisible_to_the_member(broker: TestClient) -> None:
    wid = _make_window(broker, member_self_service=False)

    # No marker in the shell, and the payload is the same empty shape as
    # "no period open" — not an error, so the page renders its empty state.
    me = broker.get("/api/v1/portal/me", headers=_member_auth()).json()
    assert me["enrollment_open"] is False
    res = broker.get("/api/v1/portal/enrollment", headers=_member_auth())
    assert res.status_code == 200
    assert res.json() == {"window": None, "enrollment": None, "options": None}

    # Writes are refused, not merely hidden.
    for path, body in (
        (
            "/api/v1/portal/enrollment/elections",
            {"elections": [{"product_code": "MED", "plan_code": "GOLD"}]},
        ),
        ("/api/v1/portal/enrollment/leave", {"action": "buy", "days": 2}),
    ):
        assert broker.put(path, json=body, headers=_member_auth()).status_code == 404
    assert broker.post(
        "/api/v1/portal/enrollment/submit", json={}, headers=_member_auth()
    ).status_code == 404

    # The broker still owns the period: it is open and listed.
    assert broker.get(f"/api/v1/enrollment-windows/{wid}").json()["status"] == "open"


def test_hiding_is_reversible_mid_period(broker: TestClient) -> None:
    """The toggle is a mid-period control, so flipping it back must restore the
    member's surface — including the enrollment row already created at open."""
    wid = _make_window(broker, member_self_service=False)
    assert broker.patch(
        f"/api/v1/enrollment-windows/{wid}", json={"member_self_service": True}
    ).status_code == 200

    me = broker.get("/api/v1/portal/me", headers=_member_auth()).json()
    assert me["enrollment_open"] is True
    body = broker.get("/api/v1/portal/enrollment", headers=_member_auth()).json()
    assert body["window"]["id"] == wid


def test_employee_view_preview_mirrors_a_hidden_period(broker: TestClient) -> None:
    """The preview's whole contract is "exactly what the member sees" — so it
    must go dark too. A preview still showing the enrolment would be the one
    screen a broker checks to confirm the toggle worked."""
    _make_window(broker, member_self_service=False)
    res = broker.get(f"/api/v1/employees/{EMP1}/portal-preview/enrollment")
    assert res.status_code == 200, res.text
    assert res.json() == {"window": None, "enrollment": None, "options": None}
    me = broker.get(f"/api/v1/employees/{EMP1}/portal-preview").json()
    assert me["enrollment_open"] is False
