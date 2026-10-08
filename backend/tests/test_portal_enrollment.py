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
from io import BytesIO
from pathlib import Path
from uuid import uuid4

import pytest

TEST_DB = Path(__file__).parent / "_test_portal_enrollment.db"
os.environ["INSPRO_DATABASE_URL"] = f"sqlite:///{TEST_DB}"

from datetime import date  # noqa: E402

from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import select  # noqa: E402

from app.core.auth import DEMO_BROKER_FIRM_ID, CurrentUser, get_current_user  # noqa: E402
from app.core.portal_auth import issue_member_token  # noqa: E402
from app.core.settings import clear_settings_cache  # noqa: E402
from app.core.storage import LocalStorage  # noqa: E402
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
    EnrollmentEvent,
    EnrollmentFormSubmission,
    EnrollmentWindow,
    LeaveElection,
    LeavePolicy,
    MemberAccount,
    Plan,
    PolicyYear,
    Product,
    StoredDocument,
    WorkflowNotification,
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
def _setup_db(tmp_path_factory):
    if TEST_DB.exists():
        TEST_DB.unlink()
    # Retained blobs go to a temp store, never the developer's backend/var.
    os.environ["INSPRO_STORAGE_DIR"] = str(tmp_path_factory.mktemp("portal_enrollment_storage"))
    clear_settings_cache()
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
    os.environ.pop("INSPRO_STORAGE_DIR", None)
    clear_settings_cache()
    engine.dispose()
    if TEST_DB.exists():
        TEST_DB.unlink()


@pytest.fixture(autouse=True)
def _reset_enrollment_state():
    yield
    with SessionLocal() as s:
        for model in (WorkflowNotification, EnrollmentEvent, EnrollmentFormSubmission,
                      LeaveElection, EnrollmentElection, Enrollment,
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


def test_non_flex_period_suppresses_a_stale_wallet(broker: TestClient) -> None:
    with SessionLocal() as s:
        employee = s.get(Employee, EMP1)
        employee.flex_wallet_amount = 999.0
        employee.flex_currency = "SGD"
        s.commit()
    try:
        _make_window(broker)
        body = broker.get(
            "/api/v1/portal/enrollment", headers=_member_auth()
        ).json()
        assert body["window"]["uses_flex"] is False
        assert body["options"]["flex_wallet"] is None
        assert all(
            tier["price_tag"] is None
            for product in body["options"]["products"]
            for tier in product["tiers"]
        )
    finally:
        with SessionLocal() as s:
            employee = s.get(Employee, EMP1)
            employee.flex_wallet_amount = None
            employee.flex_currency = None
            s.commit()


def test_flex_period_suppresses_wallet_without_currency(broker: TestClient) -> None:
    with SessionLocal() as s:
        employee = s.get(Employee, EMP1)
        employee.flex_wallet_amount = 999.0
        employee.flex_currency = None
        s.commit()
    try:
        window_id = _make_window(broker)
        # Isolate runtime defense from the draft-opening readiness test: a
        # currency can be removed after a valid period opens, and that must not
        # leave an unlabelled wallet amount visible to the member.
        with SessionLocal() as s:
            s.get(EnrollmentWindow, window_id).uses_flex = True
            s.commit()
        body = broker.get(
            "/api/v1/portal/enrollment", headers=_member_auth()
        ).json()
        assert body["options"]["flex_wallet"] is None
        assert body["options"]["flex_currency"] is None
        assert all(
            tier["price_tag"] is None
            for product in body["options"]["products"]
            for tier in product["tiers"]
        )
    finally:
        with SessionLocal() as s:
            employee = s.get(Employee, EMP1)
            employee.flex_wallet_amount = None
            s.commit()


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
    # Members send by SIGNING the e-form; the old unsigned submit is refused.
    unsigned = broker.post(
        "/api/v1/portal/enrollment/submit", json={}, headers=_member_auth()
    )
    assert unsigned.status_code == 409
    assert unsigned.json()["detail"]["code"] == "signature_required"
    form = broker.get("/api/v1/portal/enrollment/form", headers=_member_auth())
    assert form.status_code == 200, form.text
    sub = broker.post(
        "/api/v1/portal/enrollment/sign",
        json={
            "accepted_clause_ids": [c["id"] for c in form.json()["clauses"]],
            "signature_name": "Portal Member",
            "confirm": True,
        },
        headers=_member_auth(),
    )
    assert sub.status_code == 200, sub.text
    assert sub.json()["enrollment_status"] == "submitted"
    assert sub.json()["has_pdf"] is True
    detail = broker.get("/api/v1/portal/enrollment", headers=_member_auth()).json()
    assert detail["enrollment"]["status"] == "submitted"
    assert detail["enrollment"]["elections"][0]["previous_plan_code"] == "SILVER"

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

    # A salary-multiple basis reaches the member as its wording; the amount it
    # multiplies out to from their salary is withheld.
    assert fin.basis == "12 times basic monthly salary"
    assert fin.sum_insured is None
    assert _member_safe_options(options).products[0].tiers[0].price_tag == 120.0

    # A plain-amount basis IS the stated cover, so that amount survives.
    flat_tier = options.products[0].tiers[0].model_copy(
        update={"financials": PlanFinancials(basis="10000.0", sum_insured=10_000.0)}
    )
    flat = options.model_copy(
        update={"products": [options.products[0].model_copy(update={"tiers": [flat_tier]})]}
    )
    flat_fin = _member_safe_options(flat).products[0].tiers[0].financials
    assert flat_fin is not None
    assert flat_fin.sum_insured == 10_000.0
    assert flat_fin.basis is None

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


def _signed_enrollment(broker: TestClient) -> tuple[str, str, dict]:
    window_id = _make_window(broker)
    detail = broker.get("/api/v1/portal/enrollment", headers=_member_auth()).json()
    form = broker.get("/api/v1/portal/enrollment/form", headers=_member_auth()).json()
    payload = {
        "request_id": "regression-sign-request-001",
        "expected_event_id": detail["enrollment"]["latest_event_id"],
        "elections": [{"product_code": "MED", "plan_code": "GOLD"}],
        "accepted_clause_ids": [c["id"] for c in form["clauses"]],
        "signature_name": "Portal Member",
        "confirm": True,
    }
    response = broker.post("/api/v1/portal/enrollment/sign", json=payload, headers=_member_auth())
    assert response.status_code == 200, response.text
    return window_id, detail["enrollment"]["id"], payload


def test_return_retains_choices_invalidates_signature_and_records_notice(
    broker: TestClient,
) -> None:
    window_id, eid, payload = _signed_enrollment(broker)
    with SessionLocal() as db:
        original = db.scalar(select(EnrollmentFormSubmission))
        original_hash, original_snapshot = original.content_sha256, dict(original.snapshot)
    for reason in ("", "   "):
        assert (
            broker.post(f"/api/v1/enrollments/{eid}/return", json={"reason": reason}).status_code
            == 422
        )
    assert (
        broker.post(
            f"/api/v1/enrollments/{eid}/return", json={"reason": "Check family cover"}
        ).status_code
        == 200
    )
    detail = broker.get(f"/api/v1/enrollments/{eid}").json()
    assert detail["status"] == "returned"
    assert detail["elections"][0]["elected_plan_code"] == "GOLD"
    assert broker.post(f"/api/v1/enrollments/{eid}/confirm").status_code == 409
    assert (
        broker.post(
            f"/api/v1/enrollment-windows/{window_id}/close", json={"submit_saved": True}
        ).status_code
        == 409
    )
    notices = broker.get("/api/v1/portal/enrollment/notices", headers=_member_auth()).json()
    assert notices["unread"] == 2
    returned = notices["items"][0]
    assert returned["kind"] == "returned" and returned["reason"] == "Check family cover"
    assert returned["email_status"] == "unavailable"
    assert (
        broker.post(
            f"/api/v1/portal/enrollment/notices/{returned['id']}/read", headers=_member_auth()
        ).status_code
        == 200
    )
    assert (
        broker.get("/api/v1/portal/enrollment/notices", headers=_member_auth()).json()["unread"]
        == 1
    )
    with SessionLocal() as db:
        original = db.scalar(select(EnrollmentFormSubmission))
        assert original.status == "returned"
        assert original.content_sha256 == original_hash and original.snapshot == original_snapshot
    # Neither an old retry nor a fresh request from a stale page can undo the return.
    assert (
        broker.post(
            "/api/v1/portal/enrollment/sign", json=payload, headers=_member_auth()
        ).status_code
        == 409
    )
    payload["request_id"] = "regression-sign-request-002"
    assert (
        broker.post(
            "/api/v1/portal/enrollment/sign", json=payload, headers=_member_auth()
        ).status_code
        == 409
    )
    payload["expected_event_id"] = detail["latest_event_id"]
    result = broker.post("/api/v1/portal/enrollment/sign", json=payload, headers=_member_auth())
    assert result.status_code == 200, result.text
    assert result.json()["version"] == 2
    assert broker.post(f"/api/v1/enrollments/{eid}/confirm").status_code == 200
    assert broker.post(f"/api/v1/enrollments/{eid}/confirm").status_code == 200
    with SessionLocal() as db:
        assert (
            len(
                list(db.scalars(select(EnrollmentEvent).where(EnrollmentEvent.kind == "confirmed")))
            )
            == 1
        )


def test_cancel_notifies_and_preserves_signed_pdf(broker: TestClient) -> None:
    from dataclasses import replace

    _, eid, _ = _signed_enrollment(broker)
    assert (
        broker.post(f"/api/v1/enrollments/{eid}/reset", json={"reason": "Wrong period"}).status_code
        == 403
    )
    app.dependency_overrides[get_current_user] = lambda: replace(_broker(), role="system_admin")
    try:
        response = broker.post(f"/api/v1/enrollments/{eid}/reset", json={"reason": "Wrong period"})
        assert response.status_code == 200, response.text
        assert response.json()["elections"] == []
        assert (
            broker.post(f"/api/v1/enrollments/{eid}/reset", json={"reason": "Retry"}).status_code
            == 409
        )
    finally:
        app.dependency_overrides[get_current_user] = _broker
    forms = broker.get("/api/v1/portal/enrollment-forms", headers=_member_auth()).json()
    assert forms[0]["status"] == "cancelled" and forms[0]["has_pdf"]
    assert (
        broker.get(
            f"/api/v1/portal/enrollment-forms/{forms[0]['id']}/pdf", headers=_member_auth()
        ).status_code
        == 200
    )
    notices = broker.get("/api/v1/portal/enrollment/notices", headers=_member_auth()).json()
    assert notices["items"][0]["kind"] == "cancelled"
    assert notices["items"][0]["reason"] == "Wrong period"


@pytest.fixture
def form_storage(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> LocalStorage:
    """Signed forms and form documents for these tests live in a temp store."""
    from app.api.v1 import enrollment_forms as forms_api
    from app.api.v1 import portal_enrollment as portal_api
    from app.services import claims as claims_service
    from app.services.enrollment_forms import register, submission

    local = LocalStorage(tmp_path)
    for module in (forms_api, portal_api, claims_service, register, submission):
        monkeypatch.setattr(module, "get_storage", lambda: local)
    return local


def _point_at_another_tenant(storage: LocalStorage, doc_id: str, content: bytes) -> None:
    """Re-point a stored document at a key under another firm and company that
    really holds bytes — what a copied or tampered row would reach."""
    foreign = f"other-firm/other-client/enrollment_form/{uuid4()}/{uuid4()}.pdf"
    storage.save(BytesIO(content), foreign)
    with SessionLocal() as db:
        db.get(StoredDocument, doc_id).storage_path = foreign
        db.commit()


def test_signed_forms_are_read_only_from_inside_the_company(
    broker: TestClient, form_storage: LocalStorage
) -> None:
    _signed_enrollment(broker)
    with SessionLocal() as db:
        sub = db.scalar(select(EnrollmentFormSubmission))
        sub_id, doc_id = sub.id, sub.document_id
        assert db.get(StoredDocument, doc_id).storage_path.startswith(
            f"{DEMO_BROKER_FIRM_ID}/{CLIENT_ID}/"
        )
    pdf = f"/api/v1/enrollment-forms/{sub_id}/pdf"
    member_pdf = f"/api/v1/portal/enrollment-forms/{sub_id}/pdf"
    archive = f"/api/v1/policy-years/{PY_ID}/enrollment-forms/export.zip"
    assert broker.get(pdf).status_code == 200
    assert broker.get(archive).status_code == 200

    _point_at_another_tenant(form_storage, doc_id, b"%PDF-1.4 another tenant")
    assert broker.get(pdf).status_code == 404
    assert broker.get(member_pdf, headers=_member_auth()).status_code == 404
    # The only matching form is out of scope, so there is nothing to archive.
    assert broker.get(archive).status_code == 404


def test_form_documents_are_read_only_from_inside_the_company(
    broker: TestClient, form_storage: LocalStorage
) -> None:
    window_id = _make_window(broker)
    uploaded = broker.post(
        f"/api/v1/enrollment-windows/{window_id}/form-config/documents",
        files={"file": ("guide.pdf", b"%PDF-1.4 product guide", "application/pdf")},
    )
    assert uploaded.status_code == 200, uploaded.text
    doc_id = uploaded.json()["document_id"]
    config = f"/api/v1/enrollment-windows/{window_id}/form-config"
    settings = broker.get(config).json()["settings"]
    settings["documents"] = [{"id": "guide", "label": "Product guide", "document_id": doc_id}]
    assert broker.put(config, json=settings).status_code == 200

    as_broker = f"{config}/documents/{doc_id}"
    as_member = f"/api/v1/portal/enrollment/form/documents/{doc_id}"
    assert broker.get(as_broker).content == b"%PDF-1.4 product guide"
    assert broker.get(as_member, headers=_member_auth()).content == b"%PDF-1.4 product guide"

    _point_at_another_tenant(form_storage, doc_id, b"%PDF-1.4 another tenant")
    assert broker.get(as_broker).status_code == 404
    assert broker.get(as_member, headers=_member_auth()).status_code == 404


def test_sign_retry_is_idempotent_and_broker_edits_require_return(broker: TestClient) -> None:
    _, eid, payload = _signed_enrollment(broker)
    retry = broker.post("/api/v1/portal/enrollment/sign", json=payload, headers=_member_auth())
    assert retry.status_code == 200 and retry.json()["version"] == 1
    with SessionLocal() as db:
        assert len(list(db.scalars(select(EnrollmentFormSubmission)))) == 1
        assert len(list(db.scalars(select(EnrollmentEvent)))) == 1
    assert (
        broker.put(
            f"/api/v1/enrollments/{eid}/elections", json={"elections": payload["elections"]}
        ).status_code
        == 409
    )
    assert (
        broker.put(
            f"/api/v1/enrollments/{eid}/leave", json={"action": "none", "days": 0}
        ).status_code
        == 409
    )


def test_enrollment_email_retries_without_exposing_reason(broker: TestClient, monkeypatch) -> None:
    from dataclasses import replace

    from app.core import settings
    from app.services import enrollment_events, workflow_delivery

    configured = replace(settings.get_settings(), mail_mode="smtp")
    monkeypatch.setattr(settings, "get_settings", lambda: configured)
    monkeypatch.setattr(enrollment_events, "get_settings", lambda: configured)
    monkeypatch.setattr(enrollment_events, "mail_deliverable", lambda: True)
    _, eid, _ = _signed_enrollment(broker)
    assert (
        broker.post(
            f"/api/v1/enrollments/{eid}/return", json={"reason": "Private correction detail"}
        ).status_code
        == 200
    )
    sent = []

    class Mailer:
        def send_workflow_notice(self, *args):
            sent.append(args)

    monkeypatch.setattr(workflow_delivery, "get_mailer", lambda *_: Mailer())
    assert workflow_delivery.process_one_workflow_notification(None)
    assert workflow_delivery.process_one_workflow_notification(None)
    assert not workflow_delivery.process_one_workflow_notification(None)
    assert len(sent) == 2
    assert all("Private correction detail" not in str(message) for message in sent)
    with SessionLocal() as db:
        assert {n.status for n in db.scalars(select(WorkflowNotification))} == {"sent"}


def test_notice_scope_rejects_another_employee(broker: TestClient) -> None:
    from app.core.portal_auth import CurrentMember, get_current_member

    _, _, _ = _signed_enrollment(broker)
    notice = broker.get("/api/v1/portal/enrollment/notices", headers=_member_auth()).json()[
        "items"
    ][0]
    # A forged client scope cannot acknowledge the first employee's notice.
    app.dependency_overrides[get_current_member] = lambda: CurrentMember(
        member_account_id="unrelated",
        client_id="unrelated",
        broker_firm_id=None,
        email=None,
        staff_id="unrelated",
    )
    try:
        response = broker.post(f"/api/v1/portal/enrollment/notices/{notice['id']}/read")
        assert response.status_code in (403, 404)
    finally:
        app.dependency_overrides.pop(get_current_member, None)


def test_return_after_deadline_and_viewer_are_rejected(broker: TestClient) -> None:
    from dataclasses import replace
    from datetime import UTC, datetime

    wid, eid, _ = _signed_enrollment(broker)
    app.dependency_overrides[get_current_user] = lambda: replace(_broker(), role="broker_viewer")
    try:
        assert (
            broker.post(f"/api/v1/enrollments/{eid}/return", json={"reason": "Review"}).status_code
            == 403
        )
    finally:
        app.dependency_overrides[get_current_user] = _broker
    with SessionLocal() as db:
        db.get(EnrollmentWindow, wid).closes_at = datetime(2021, 1, 1, tzinfo=UTC)
        db.commit()
    assert (
        broker.post(f"/api/v1/enrollments/{eid}/return", json={"reason": "Review"}).status_code
        == 409
    )
    assert broker.post(f"/api/v1/enrollments/{eid}/confirm").status_code == 200


def test_submission_failure_rolls_back_notices_and_elections(
    broker: TestClient, monkeypatch
) -> None:
    from app.services.enrollment_forms import submission

    _make_window(broker)
    form = broker.get("/api/v1/portal/enrollment/form", headers=_member_auth()).json()

    def fail(*args, **kwargs):
        from fastapi import HTTPException

        raise HTTPException(503, "Storage unavailable")

    monkeypatch.setattr(submission, "store_pdf", fail)
    response = broker.post(
        "/api/v1/portal/enrollment/sign",
        headers=_member_auth(),
        json={
            "elections": [{"product_code": "MED", "plan_code": "GOLD"}],
            "signature_name": "Portal Member",
            "confirm": True,
            "accepted_clause_ids": [c["id"] for c in form["clauses"]],
        },
    )
    assert response.status_code == 503
    with SessionLocal() as db:
        assert not list(db.scalars(select(EnrollmentEvent)))
        assert not list(db.scalars(select(EnrollmentFormSubmission)))
        assert not list(db.scalars(select(EnrollmentElection)))


def test_email_failure_retry_and_recipient_change(broker: TestClient, monkeypatch) -> None:
    from dataclasses import replace
    from datetime import UTC, datetime

    from app.core import settings
    from app.services import enrollment_events, workflow_delivery

    _, eid, _ = _signed_enrollment(broker)
    notice = broker.get(f"/api/v1/enrollments/{eid}/events").json()[0]
    configured = replace(settings.get_settings(), mail_mode="smtp")
    monkeypatch.setattr(settings, "get_settings", lambda: configured)
    monkeypatch.setattr(enrollment_events, "get_settings", lambda: configured)
    monkeypatch.setattr(enrollment_events, "mail_deliverable", lambda: True)
    url = f"/api/v1/enrollments/{eid}/events/{notice['id']}/retry-email"
    assert broker.post(url).json()["email_status"] == "queued"
    assert broker.post(url).json()["email_status"] == "queued"

    class FailingMailer:
        def send_workflow_notice(self, *args):
            raise RuntimeError("private provider response")

    monkeypatch.setattr(workflow_delivery, "get_mailer", lambda *_: FailingMailer())
    assert workflow_delivery.process_one_workflow_notification(None)
    with SessionLocal() as db:
        outbox = db.scalar(select(WorkflowNotification))
        assert outbox.status == "queued" and outbox.attempts == 1
        assert "private" not in outbox.last_error
        outbox.available_at = datetime(2020, 1, 1, tzinfo=UTC)
        db.get(MemberAccount, ACC1).status = "disabled"
        db.commit()
    try:
        assert workflow_delivery.process_one_workflow_notification(None)
        with SessionLocal() as db:
            assert db.scalar(select(WorkflowNotification)).status == "cancelled"
    finally:
        with SessionLocal() as db:
            db.get(MemberAccount, ACC1).status = MEMBER_STATUS_ACTIVE
            db.commit()


def test_draft_save_is_atomic_and_cannot_overwrite_a_return(broker: TestClient) -> None:
    _, eid, _ = _signed_enrollment(broker)
    response = broker.put(
        "/api/v1/portal/enrollment/draft",
        headers=_member_auth(),
        json={
            "elections": [{"product_code": "MED", "plan_code": "SILVER"}],
            "leave": {"action": "buy", "days": 999},
        },
    )
    assert response.status_code == 422, response.text
    detail = broker.get(f"/api/v1/enrollments/{eid}").json()
    assert detail["status"] == "submitted"
    assert detail["elections"][0]["elected_plan_code"] == "GOLD"
    assert (
        broker.post(
            f"/api/v1/enrollments/{eid}/return", json={"reason": "Correct this"}
        ).status_code
        == 200
    )
    response = broker.put(
        "/api/v1/portal/enrollment/draft",
        headers=_member_auth(),
        json={
            "expected_event_id": detail["latest_event_id"],
            "elections": [{"product_code": "MED", "plan_code": "SILVER"}],
        },
    )
    assert response.status_code == 409
    assert broker.post(f"/api/v1/enrollments/{eid}/submit").status_code == 409


def test_signed_reopen_requires_self_service_and_fresh_signature(broker: TestClient) -> None:
    wid, eid, _ = _signed_enrollment(broker)
    assert broker.post(f"/api/v1/enrollments/{eid}/confirm").status_code == 200
    with SessionLocal() as db:
        db.get(EnrollmentWindow, wid).member_self_service = False
        db.commit()
    response = broker.post(f"/api/v1/enrollments/{eid}/reopen", json={"reason": "Correction"})
    assert response.status_code == 409, response.text
    with SessionLocal() as db:
        assert db.get(Enrollment, eid).status == "confirmed"
        assert db.scalar(select(EnrollmentFormSubmission)).status == "submitted"
        db.get(EnrollmentWindow, wid).member_self_service = True
        db.commit()
    response = broker.post(f"/api/v1/enrollments/{eid}/reopen", json={"reason": "Correction"})
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "returned"
    assert broker.post(f"/api/v1/enrollments/{eid}/submit").status_code == 409
    with SessionLocal() as db:
        assert db.scalar(select(EnrollmentFormSubmission)).status == "returned"
        events = list(db.scalars(select(EnrollmentEvent).where(EnrollmentEvent.kind == "reopened")))
        assert len(events) == 1 and events[0].reason == "Correction"


@pytest.mark.parametrize("enrollment_status", ["not_started", "in_progress"])
def test_current_paper_receipt_does_not_require_election_submission(broker, enrollment_status):
    from io import BytesIO

    from pypdf import PdfWriter

    wid = _make_window(broker)
    detail = broker.get("/api/v1/portal/enrollment", headers=_member_auth()).json()
    with SessionLocal() as db:
        db.get(Enrollment, detail["enrollment"]["id"]).status = enrollment_status
        db.commit()
    writer = PdfWriter()
    writer.add_blank_page(width=200, height=200)
    content = BytesIO()
    writer.write(content)
    filed = broker.post(f"/api/v1/policy-years/{PY_ID}/enrollment-forms/paper",
        data={"employee_id": EMP1, "window_id": wid},
        files={"file": ("synthetic-form.pdf", content.getvalue(), "application/pdf")})
    assert filed.status_code == 201, filed.text
    url = f"/api/v1/enrollment-forms/{filed.json()['id']}/acknowledge"
    acknowledged = broker.post(url, json={})
    assert acknowledged.status_code == 200, acknowledged.text
    with SessionLocal() as db:
        assert db.get(Enrollment, detail["enrollment"]["id"]).status == enrollment_status
        form = db.get(EnrollmentFormSubmission, filed.json()["id"])
        form.status = "cancelled"
        db.commit()
    assert broker.post(url, json={}).status_code == 409


def test_mailbox_ownership_is_batched_and_refreshed_between_transactions(broker):
    from sqlalchemy import event

    from app.services.enrollment_events import recipient_for

    _, eid, _ = _signed_enrollment(broker)
    scans = []
    def capture(conn, cursor, statement, parameters, context, many):
        if "SELECT employees.staff_id, employees.attribute_values" in statement:
            scans.append(statement)
    event.listen(engine, "before_cursor_execute", capture)
    try:
        with SessionLocal() as db:
            enrollment = db.get(Enrollment, eid)
            for _ in range(100):
                assert recipient_for(db, enrollment) == "pe1@a.test"
            assert len(scans) == 1
            db.add(Employee(client_id=CLIENT_ID, policy_year_id=PY_ID, staff_id="SHARED-MAIL",
                employee_name="Synthetic shared mailbox", status="active", source="csv_import",
                attribute_values={"email": " PE1@A.TEST "}))
            db.commit()
            assert recipient_for(db, enrollment) is None
            assert len(scans) == 2
            db.query(Employee).filter_by(staff_id="SHARED-MAIL").delete()
            db.commit()
    finally:
        event.remove(engine, "before_cursor_execute", capture)


def test_disabled_mail_never_resolves_roster_recipients(broker, monkeypatch):
    from dataclasses import replace

    from app.services import enrollment_events
    configured = replace(enrollment_events.get_settings(), mail_mode="disabled")
    monkeypatch.setattr(enrollment_events, "get_settings", lambda: configured)
    def unexpected(*args, **kwargs):
        raise AssertionError("Disabled delivery must not inspect the roster")
    monkeypatch.setattr(enrollment_events, "recipient_for", unexpected)
    _signed_enrollment(broker)


def test_live_state_locks_closed_period_and_rejects_late_drafts(broker):
    from datetime import UTC, datetime
    wid = _make_window(broker)
    detail = broker.get("/api/v1/portal/enrollment", headers=_member_auth()).json()
    eid = detail["enrollment"]["id"]
    url = f"/api/v1/portal/enrollment/state/{eid}"
    assert broker.get(url, headers=_member_auth()).json()["window_status"] == "open"
    foreign = broker.get("/api/v1/portal/enrollment/state/foreign", headers=_member_auth())
    assert foreign.status_code == 404
    with SessionLocal() as db:
        db.get(EnrollmentWindow, wid).closes_at = datetime(2020, 1, 2, tzinfo=UTC)
        db.commit()
    payload = {"elections": [{"product_code": "MED", "plan_code": "GOLD"}]}
    assert broker.put("/api/v1/portal/enrollment/draft", headers=_member_auth(),
        json=payload).status_code in (404, 409)
    with SessionLocal() as db:
        db.get(EnrollmentWindow, wid).status = "closed"
        db.commit()
    assert broker.get(url, headers=_member_auth()).json()["window_status"] == "closed"
    with SessionLocal() as db:
        assert not list(db.scalars(select(EnrollmentElection)))


def test_migration_recovers_only_audited_cancellations(broker: TestClient, monkeypatch) -> None:
    import importlib.util
    from datetime import UTC, datetime, timedelta

    _, eid, _ = _signed_enrollment(broker)
    path = Path(__file__).parents[1] / "alembic/versions/c7e9a1b3d5f7_enrollment_events.py"
    spec = importlib.util.spec_from_file_location("enrollment_event_migration", path)
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    with SessionLocal() as db:
        original = db.scalar(select(EnrollmentFormSubmission))
        original_hash = original.content_sha256
        db.get(Enrollment, eid).status = "not_started"
        db.query(EnrollmentEvent).delete()
        db.add(AuditLog(client_id=CLIENT_ID, employee_id=EMP1,
            action="reset_enrollment", entity_type="enrollment", entity_id=eid,
            created_at=datetime.now(UTC) + timedelta(seconds=1)))
        db.flush()
        # Provisioning from current models can create the event table before
        # Alembic reaches this revision; upgrading must reuse it without loss.
        monkeypatch.setattr(migration.op, "get_bind", lambda: db.connection())
        migration.upgrade()
        migration.upgrade()
        db.expire_all()
        assert db.get(EnrollmentFormSubmission, original.id).status == "cancelled"
        assert db.get(EnrollmentFormSubmission, original.id).content_sha256 == original_hash
        events = list(db.scalars(select(EnrollmentEvent)))
        assert len(events) == 1 and events[0].kind == "cancelled"
        assert events[0].notification_id is None
        assert "before employee reasons" in events[0].reason
