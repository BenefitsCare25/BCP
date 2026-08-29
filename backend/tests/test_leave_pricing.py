"""Buy/sell-leave price tag — rate by employee attribute → flex-wallet impact.

Covers the pure resolver (rate lookup, signed flex amount, shape validation, the
config option builder) and the API integration (snapshot on set_leave, the leave
trade folded into the flex balance, and revert clearing it).
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest

TEST_DB = Path(__file__).parent / "_test_leave_pricing.db"
os.environ["INSPRO_DATABASE_URL"] = f"sqlite:///{TEST_DB}"

from datetime import UTC, date, datetime, timedelta  # noqa: E402

from fastapi import HTTPException  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.core.auth import DEMO_BROKER_FIRM_ID, CurrentUser, get_current_user  # noqa: E402
from app.db.base import Base  # noqa: E402
from app.db.session import SessionLocal, engine  # noqa: E402
from app.main import app  # noqa: E402
from app.models import (  # noqa: E402
    Client,
    Employee,
    Enrollment,
    EnrollmentWindow,
    LeaveElection,
    LeavePolicy,
    PolicyYear,
)
from app.models.enrollment import EnrollmentStatus  # noqa: E402
from app.models.enrollment_window import WindowStatus  # noqa: E402
from app.models.leave_election import LeaveElectionStatus  # noqa: E402
from app.models.policy_year import PolicyYearStatus  # noqa: E402
from app.services.enrollment_validation import validate_leave  # noqa: E402
from app.services.leave_pricing_resolver import (  # noqa: E402
    build_leave_rate_options,
    leave_flex_amount,
    leave_limits_for,
    leave_rate_for,
    validate_leave_rates_shape,
)

# ── Pure resolver units (no DB) ─────────────────────────────────────────────


def test_leave_flex_amount_signs():
    # Buy spends (negative), sell credits (positive), none/zero → None.
    assert leave_flex_amount("buy", 3, 300) == -900.0
    assert leave_flex_amount("sell", 2, 250) == 500.0
    assert leave_flex_amount("none", 0, 300) is None
    assert leave_flex_amount("buy", 0, 300) is None
    assert leave_flex_amount("buy", 3, None) is None


def test_leave_rate_for_reads_attribute():
    pol = LeavePolicy(leave_rates={"attribute": "job_grade", "rates": {"Manager": 300}})
    mgr = Employee(derived_attribute_values={"job_grade": "Manager"}, attribute_values={})
    other = Employee(derived_attribute_values={"job_grade": "Analyst"}, attribute_values={})
    assert leave_rate_for(pol, mgr) == 300.0
    assert leave_rate_for(pol, other) is None  # no rate configured for Analyst
    # Raw attribute_values is a fallback when derived lacks the key.
    raw = Employee(derived_attribute_values={}, attribute_values={"job_grade": "Manager"})
    assert leave_rate_for(pol, raw) == 300.0
    # Empty bag → no rate.
    assert leave_rate_for(LeavePolicy(leave_rates={}), mgr) is None


def test_validate_leave_rates_shape():
    assert validate_leave_rates_shape({}) == []
    assert validate_leave_rates_shape(
        {"attribute": "job_grade", "rates": {"Manager": 300, "Exec": 0}}
    ) == []
    # Negative rate rejected.
    assert any(
        "must be ≥ 0" in e
        for e in validate_leave_rates_shape(
            {"attribute": "g", "rates": {"Manager": -5}}
        )
    )
    # Rates without an attribute rejected.
    assert validate_leave_rates_shape({"rates": {"Manager": 300}})
    # Per-tier limits are valid, sparse (one field is enough) and bounded.
    assert validate_leave_rates_shape(
        {"attribute": "g", "rates": {}, "limits": {"Manager": {"max_buy_days": 10}}}
    ) == []
    assert any(
        "must be ≥ 0" in e
        for e in validate_leave_rates_shape(
            {"attribute": "g", "limits": {"Manager": {"max_sell_days": -1}}}
        )
    )
    # Limits without an attribute are unkeyable — rejected like rates.
    assert validate_leave_rates_shape({"limits": {"Manager": {"max_buy_days": 3}}})
    # A tier max BELOW the company minimum makes the range unsatisfiable
    # (lo=2, hi=1 rejects every value) — caught at the write boundary.
    bag = {"attribute": "g", "limits": {"Manager": {"max_buy_days": 1}}}
    assert validate_leave_rates_shape(bag) == []  # fine when the minimum is 0
    assert any(
        "below the company minimum" in e
        for e in validate_leave_rates_shape(bag, min_buy_days=2)
    )


def _tiered_policy() -> LeavePolicy:
    """Company default 5 buy / 3 sell, with Manager overriding ONLY the buy cap."""
    return LeavePolicy(
        allow_buy=True,
        allow_sell=True,
        min_buy_days=0,
        max_buy_days=5,
        min_sell_days=0,
        max_sell_days=3,
        increment_days=1,
        leave_rates={
            "attribute": "job_grade",
            "rates": {"Manager": 350},
            "limits": {"Manager": {"max_buy_days": 10}},
        },
    )


def _emp(grade: str | None) -> Employee:
    return Employee(
        derived_attribute_values={"job_grade": grade} if grade else {},
        attribute_values={},
    )


def test_leave_limits_are_per_tier_and_sparse():
    pol = _tiered_policy()
    # The overriding tier gets its own buy cap but INHERITS the sell default —
    # a partial entry must not zero the field it didn't set.
    mgr = leave_limits_for(pol, _emp("Manager"))
    assert (mgr.max_buy_days, mgr.max_sell_days) == (10.0, 3.0)
    assert mgr.from_tier is True and mgr.tier_value == "Manager"
    # A tier with no entry, and a member with no value for the attribute, both
    # fall back to the company default — never to zero, which would silently
    # revoke trading the company has switched on.
    for employee in (_emp("Analyst"), _emp(None)):
        lim = leave_limits_for(pol, employee)
        assert (lim.max_buy_days, lim.max_sell_days) == (5.0, 3.0)
        assert lim.from_tier is False
    # No policy at all → nothing tradable.
    assert leave_limits_for(None, _emp("Manager")).max_buy_days == 0.0


def test_validate_leave_enforces_the_members_tier_cap():
    pol = _tiered_policy()
    mgr, analyst = _emp("Manager"), _emp("Analyst")
    # Manager's raised cap is honoured...
    validate_leave(pol, "buy", 8, mgr)
    # ...but is NOT granted to a tier that never overrode it.
    with pytest.raises(HTTPException) as exc:
        validate_leave(pol, "buy", 8, analyst)
    assert exc.value.status_code == 422
    # Past even the raised cap still fails, and the message names the tier so a
    # broker isn't hunting for a number the global fields don't show.
    with pytest.raises(HTTPException) as exc:
        validate_leave(pol, "buy", 11, mgr)
    assert "Manager" in exc.value.detail
    # The inherited sell cap still binds for the overriding tier.
    validate_leave(pol, "sell", 3, mgr)
    with pytest.raises(HTTPException):
        validate_leave(pol, "sell", 4, mgr)


def test_tier_can_buy_when_the_company_default_is_zero():
    """"Nobody by default, Managers up to 10" must actually work.

    `allow_buy` is the company-wide gate and `validate_leave` checks it BEFORE
    the per-tier cap, so a UI that derives it from the global maximum alone
    saves the tier override and then refuses every member on that tier.
    """
    pol = LeavePolicy(
        allow_buy=True,  # what the card must send once a tier grants days
        allow_sell=True,
        min_buy_days=0,
        max_buy_days=0,  # nobody by default
        min_sell_days=0,
        max_sell_days=0,
        increment_days=1,
        leave_rates={
            "attribute": "job_grade",
            "rates": {"Manager": 350},
            "limits": {"Manager": {"max_buy_days": 10}},
        },
    )
    validate_leave(pol, "buy", 10, _emp("Manager"))
    # Everyone else still can't — the default of 0 stands for them.
    with pytest.raises(HTTPException):
        validate_leave(pol, "buy", 1, _emp("Analyst"))


def test_build_leave_rate_options_distinct_values():
    emps = [
        Employee(attribute_values={"job_grade": "Manager"}, derived_attribute_values={}),
        Employee(attribute_values={"job_grade": "Manager"}, derived_attribute_values={}),
        Employee(attribute_values={}, derived_attribute_values={"job_grade": "Exec"}),
    ]
    opts = build_leave_rate_options(emps)
    assert "job_grade" in opts["attributes"]
    vals = {v["value"]: v["count"] for v in opts["values"]["job_grade"]}
    assert vals == {"Manager": 2, "Exec": 1}


# ── API integration ─────────────────────────────────────────────────────────

CLIENT_ID = "00000000-0000-0000-0000-0000001ea000"
PY_ID = "00000000-0000-0000-0000-0000001ea001"
EMP1 = "00000000-0000-0000-0000-0000001ea005"
WINDOW_ID = "00000000-0000-0000-0000-0000001ea010"
ENROLL_ID = "00000000-0000-0000-0000-0000001ea011"
USER_ID = "00000000-0000-0000-0000-0000001ea0ff"


def _user() -> CurrentUser:
    return CurrentUser(
        user_id=USER_ID, broker_firm_id=DEMO_BROKER_FIRM_ID,
        client_id=CLIENT_ID, role="broker_admin",
    )


@pytest.fixture(scope="module", autouse=True)
def _setup_db():
    if TEST_DB.exists():
        TEST_DB.unlink()
    Base.metadata.create_all(bind=engine)
    from scripts.seed_demo import seed
    seed()
    with SessionLocal() as s:
        s.add(Client(id=CLIENT_ID, name="Leave Co", broker_firm_id=DEMO_BROKER_FIRM_ID))
        s.flush()
        s.add(PolicyYear(
            id=PY_ID, client_id=CLIENT_ID, year=2032,
            start_date=date(2032, 1, 1), end_date=date(2032, 12, 31),
            status=PolicyYearStatus.draft,
        ))
        s.flush()
        s.add(Employee(
            id=EMP1, client_id=CLIENT_ID, policy_year_id=PY_ID,
            staff_id="L-1", employee_name="Leave One",
            attribute_values={}, derived_attribute_values={"job_grade": "Manager"},
            matched_categories=[], source="csv_import", status="active",
            flex_wallet_amount=5000.0, flex_currency="SGD", flex_tier_name="Manager",
        ))
        s.add(LeavePolicy(
            policy_year_id=PY_ID, client_id=CLIENT_ID, allow_buy=True, allow_sell=True,
            max_buy_days=10, max_sell_days=10, increment_days=1,
            leave_rates={"attribute": "job_grade", "rates": {"Manager": 300}},
        ))
        s.add(EnrollmentWindow(
            id=WINDOW_ID, policy_year_id=PY_ID, client_id=CLIENT_ID, name="OE",
            opens_at=datetime.now(UTC) - timedelta(days=1),
            closes_at=datetime.now(UTC) + timedelta(days=7),
            status=WindowStatus.open, allow_leave=True, uses_flex=True,
        ))
        s.flush()
        s.add(Enrollment(
            id=ENROLL_ID, window_id=WINDOW_ID, policy_year_id=PY_ID,
            client_id=CLIENT_ID, employee_id=EMP1,
            status=EnrollmentStatus.in_progress, baseline_snapshot={"products": {}},
        ))
        s.commit()
    yield
    engine.dispose()
    if TEST_DB.exists():
        TEST_DB.unlink()


@pytest.fixture(autouse=True)
def _reset_leave():
    yield
    with SessionLocal() as s:
        for el in s.query(LeaveElection).all():
            el.action = "none"
            el.days = 0.0
            el.flex_amount = None
            el.status = LeaveElectionStatus.draft
        # Restore the enrollment to editable so each test can submit→confirm fresh.
        enr = s.get(Enrollment, ENROLL_ID)
        if enr is not None:
            enr.status = EnrollmentStatus.in_progress
            enr.submitted_at = None
            enr.confirmed_at = None
        # Restore the window (close_window tests mutate status/default_behavior).
        win = s.get(EnrollmentWindow, WINDOW_ID)
        if win is not None:
            win.status = WindowStatus.open
            win.default_behavior = "deemed_keep_current"
        s.commit()


@pytest.fixture
def client() -> TestClient:
    app.dependency_overrides[get_current_user] = _user
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.pop(get_current_user, None)


def test_leave_rate_options_endpoint(client: TestClient) -> None:
    res = client.get(f"/api/v1/policy-years/{PY_ID}/leave-rate-options")
    assert res.status_code == 200, res.text
    assert "job_grade" in res.json()["attributes"]


def test_leave_policy_rejects_negative_rate(client: TestClient) -> None:
    res = client.put(
        f"/api/v1/policy-years/{PY_ID}/leave-policy",
        json={"allow_buy": True, "leave_rates": {"attribute": "job_grade",
                                                  "rates": {"Manager": -5}}},
    )
    assert res.status_code == 422


def test_set_leave_snapshots_flex_amount(client: TestClient) -> None:
    res = client.put(
        f"/api/v1/enrollments/{ENROLL_ID}/leave", json={"action": "buy", "days": 3}
    )
    assert res.status_code == 200, res.text
    # Buy 3 days @ 300 = -900 (spend).
    assert res.json()["leave"]["flex_amount"] == -900.0


def test_leave_folds_into_flex_balance_and_history(client: TestClient) -> None:
    client.put(f"/api/v1/enrollments/{ENROLL_ID}/leave", json={"action": "sell", "days": 2})
    client.post(f"/api/v1/enrollments/{ENROLL_ID}/submit")
    client.post(f"/api/v1/enrollments/{ENROLL_ID}/confirm")

    from app.services.flex_pricing_resolver import summarize_employee
    with SessionLocal() as s:
        summary = summarize_employee(s, s.get(Employee, EMP1))
    # Sell 2 days @ 300 = +600 credit → 5000 + 600.
    assert summary is not None
    assert summary.leave_flex_amount == 600.0
    assert summary.balance == 5600.0

    hist = client.get(f"/api/v1/employees/{EMP1}/coverage-history").json()["entries"]
    assert any(e["action"] == "update_enrollment_leave" for e in hist)


def test_revert_clears_leave_trade(client: TestClient) -> None:
    client.put(f"/api/v1/enrollments/{ENROLL_ID}/leave", json={"action": "buy", "days": 4})
    client.post(f"/api/v1/enrollments/{ENROLL_ID}/submit")
    client.post(f"/api/v1/enrollments/{ENROLL_ID}/confirm")

    res = client.post(f"/api/v1/employees/{EMP1}/coverage/revert", json={"target": "default"})
    assert res.status_code == 200, res.text
    assert any(c["product_code"] == "(leave)" for c in res.json()["changes"])
    with SessionLocal() as s:
        el = s.query(LeaveElection).filter_by(enrollment_id=ENROLL_ID).one()
        assert el.action == "none" and el.flex_amount is None


def test_set_leave_resets_status_to_draft(client: TestClient) -> None:
    # Even after a prior confirm, editing leave returns it to draft (must re-confirm).
    client.put(f"/api/v1/enrollments/{ENROLL_ID}/leave", json={"action": "buy", "days": 2})
    client.post(f"/api/v1/enrollments/{ENROLL_ID}/submit")
    client.post(f"/api/v1/enrollments/{ENROLL_ID}/confirm")
    client.put(f"/api/v1/enrollments/{ENROLL_ID}/leave", json={"action": "sell", "days": 1})
    with SessionLocal() as s:
        el = s.query(LeaveElection).filter_by(enrollment_id=ENROLL_ID).one()
        assert el.status == LeaveElectionStatus.draft


def test_close_window_deemed_keep_confirms_leave(client: TestClient) -> None:
    # A member sets leave but never submits; deemed-keep at close must confirm it so
    # the trade still counts in the flex balance.
    client.put(f"/api/v1/enrollments/{ENROLL_ID}/leave", json={"action": "sell", "days": 3})
    from app.services.enrollment_lifecycle import close_window
    with SessionLocal() as s:
        win = s.get(EnrollmentWindow, WINDOW_ID)
        win.default_behavior = "deemed_keep_current"
        close_window(s, win, _user())
        s.commit()
    from app.services.flex_pricing_resolver import summarize_employee
    with SessionLocal() as s:
        el = s.query(LeaveElection).filter_by(enrollment_id=ENROLL_ID).one()
        assert el.status == LeaveElectionStatus.confirmed and el.action == "sell"
        summary = summarize_employee(s, s.get(Employee, EMP1))
        assert summary is not None and summary.leave_flex_amount == 900.0


def test_close_window_deemed_decline_clears_leave(client: TestClient) -> None:
    client.put(f"/api/v1/enrollments/{ENROLL_ID}/leave", json={"action": "buy", "days": 5})
    from app.services.enrollment_lifecycle import close_window
    with SessionLocal() as s:
        win = s.get(EnrollmentWindow, WINDOW_ID)
        win.default_behavior = "deemed_decline"
        close_window(s, win, _user())
        s.commit()
    with SessionLocal() as s:
        el = s.query(LeaveElection).filter_by(enrollment_id=ENROLL_ID).one()
        assert el.action == "none" and el.flex_amount is None
        assert el.status == LeaveElectionStatus.confirmed


def test_sell_blocked_for_ineligible_member(client: TestClient) -> None:
    # Roster flag "Eligible to Sell Leave" = false → sell elections 422; buy
    # stays allowed (the flag only governs selling).
    with SessionLocal() as s:
        emp = s.get(Employee, EMP1)
        emp.attribute_values = {**(emp.attribute_values or {}),
                                "leave_sell_eligible": False}
        s.commit()
    try:
        res = client.put(
            f"/api/v1/enrollments/{ENROLL_ID}/leave",
            json={"action": "sell", "days": 2},
        )
        assert res.status_code == 422
        assert "not eligible to sell" in res.json()["detail"]
        res = client.put(
            f"/api/v1/enrollments/{ENROLL_ID}/leave",
            json={"action": "buy", "days": 2},
        )
        assert res.status_code == 200, res.text
    finally:
        with SessionLocal() as s:
            emp = s.get(Employee, EMP1)
            attrs = dict(emp.attribute_values or {})
            attrs.pop("leave_sell_eligible", None)
            emp.attribute_values = attrs
            s.commit()
