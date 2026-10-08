"""Platform console activity: per-broker counters for the master admin.

Two firms with seeded control-plane and firm-schema rows (one schema on
SQLite). The platform host in tests is "testserver"; a firm's own host is
`<slug>.localhost`.
"""
from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.api.v1.platform_activity import clear_activity_cache
from app.core.auth import CurrentUser, get_current_user
from app.db.session import SessionLocal
from app.main import app
from app.models import (
    AuthEvent,
    AuthSession,
    BrokerFirm,
    Claim,
    Client,
    Employee,
    MemberAccount,
    PolicyYear,
    User,
)
from app.models.ai_spend import AISpendLog
from app.models.enrollment import Enrollment
from app.models.enrollment_window import EnrollmentWindow
from app.models.policy_year import PolicyYearStatus

FIRM_A = "act-firm-a"
FIRM_B = "act-firm-b"
FIRM_EMPTY = "act-firm-empty"
CLIENT_A1 = "act-client-a1"
CLIENT_A2 = "act-client-a2"
CLIENT_B1 = "act-client-b1"
MASTER_ID = "act-master"

NOW = datetime.now(UTC)
TODAY = NOW - timedelta(hours=1)
DAYS_AGO_3 = NOW - timedelta(days=3)
DAYS_AGO_20 = NOW - timedelta(days=20)
DAYS_AGO_60 = NOW - timedelta(days=60)

# Identifying strings seeded below; none may appear in a response.
PERSONAL = [
    "alice.staff@firm-a.test", "Alice Staffer", "ivan.invited@firm-a.test",
    "hannah.hr@firm-a.test", "Hannah Human", "bob.staff@firm-b.test", "Bob Broker",
    "mia.member@firm-a.test", "Mia Member", "nils.member@firm-b.test", "Nils Member",
    "Erin Employee", "Finn Employee", "S-A-001", "S-B-001", "INV-ACT-A1", "Clinic of Secrets",
    "master.admin@platform.test",
]


@pytest.fixture(scope="module", autouse=True)
def _seed() -> None:
    with SessionLocal() as s:
        s.add_all([
            BrokerFirm(id=FIRM_A, name="Alpha Brokers", slug="alpha-act", is_platform_owner=True),
            BrokerFirm(id=FIRM_B, name="Bravo Brokers", slug="bravo-act"),
            BrokerFirm(id=FIRM_EMPTY, name="Empty Brokers", slug="empty-act"),
        ])
        s.flush()
        s.add_all([
            Client(id=CLIENT_A1, name="A One", broker_firm_id=FIRM_A),
            Client(id=CLIENT_A2, name="A Two", broker_firm_id=FIRM_A, portal_enabled=False),
            Client(id=CLIENT_B1, name="B One", broker_firm_id=FIRM_B),
        ])
        s.flush()
        _seed_people(s)
        _seed_auth(s)
        _seed_tenant(s)
        s.commit()


def _seed_people(s: Any) -> None:
    s.add_all([
        User(id="act-u1", email="alice.staff@firm-a.test", display_name="Alice Staffer",
             broker_firm_id=FIRM_A, role="broker_admin", status="active"),
        User(id="act-u2", email="ivan.invited@firm-a.test", broker_firm_id=FIRM_A,
             role="firm_admin", status="invited"),
        User(id="act-u3", email="hannah.hr@firm-a.test", display_name="Hannah Human",
             broker_firm_id=FIRM_A, role="client_hr", status="active"),
        User(id="act-u4", email="bob.staff@firm-b.test", display_name="Bob Broker",
             broker_firm_id=FIRM_B, role="broker_viewer", status="active"),
        User(id="act-u5", email="disabled@firm-b.test", broker_firm_id=FIRM_B,
             role="broker_admin", status="disabled"),
        User(id=MASTER_ID, email="master.admin@platform.test", broker_firm_id=None,
             role="system_admin", status="active"),
        MemberAccount(id="act-m1", client_id=CLIENT_A1, email="mia.member@firm-a.test",
                      staff_id="S-A-001", display_name="Mia Member", status="active"),
        MemberAccount(id="act-m2", client_id=CLIENT_A1, staff_id="S-A-002", status="invited"),
        MemberAccount(id="act-m3", client_id=CLIENT_B1, email="nils.member@firm-b.test",
                      staff_id="S-B-001", display_name="Nils Member", status="active"),
    ])


def _event(when: datetime, surface: str, *, firm: str | None = None,
           client: str | None = None, event: str = "login_success",
           outcome: str = "success") -> AuthEvent:
    return AuthEvent(occurred_at=when, event_type=event, outcome=outcome, surface=surface,
                     broker_firm_id=firm, client_id=client, subject_id="act-subject")


def _session(firm: str | None, client: str | None, *, revoked: bool = False,
             expired: bool = False, idle: bool = False) -> AuthSession:
    seen = NOW - (timedelta(hours=2) if idle else timedelta(minutes=1))
    return AuthSession(
        subject_type="broker", subject_id="act-subject", client_id=client, broker_firm_id=firm,
        family_id=f"fam-{firm}-{client}-{revoked}-{expired}-{idle}", refresh_hash="x" * 64,
        issued_at=seen, last_seen_at=seen,
        expires_at=NOW + (timedelta(hours=-1) if expired else timedelta(hours=4)),
        revoked_at=NOW if revoked else None,
    )


def _seed_auth(s: Any) -> None:
    s.add_all([
        # Firm A: staff today and 20 days ago, HR 3 days ago, member (company only) today.
        _event(TODAY, "broker", firm=FIRM_A),
        _event(DAYS_AGO_20, "broker", firm=FIRM_A),
        _event(DAYS_AGO_3, "hr", firm=FIRM_A, client=CLIENT_A1),
        _event(TODAY, "portal", client=CLIENT_A1),
        # Not sign-ins: a failure, an MFA challenge, the master admin (no firm).
        _event(TODAY, "broker", firm=FIRM_A, event="login_fail", outcome="fail"),
        _event(TODAY, "broker", firm=FIRM_A, event="mfa_challenge"),
        _event(TODAY, "broker"),
        # Firm B: a member 3 days ago, staff 60 days ago (90-day period only).
        _event(DAYS_AGO_3, "portal", client=CLIENT_B1),
        _event(DAYS_AGO_60, "broker", firm=FIRM_B),
        # Sessions: A has one live staff and one live member session.
        _session(FIRM_A, None),
        _session(None, CLIENT_A1),
        _session(FIRM_A, None, revoked=True),
        _session(FIRM_A, None, expired=True),
        _session(FIRM_A, None, idle=True),
        _session(FIRM_B, None, revoked=True),
    ])


def _claim(cid: str, client: str, year: str, employee: str, *, status: str,
           submitted: datetime | None) -> Claim:
    return Claim(
        id=cid, client_id=client, policy_year_id=year, employee_id=employee,
        claim_kind="flex", flex_category_name="Wellness", claim_type="Wellness",
        incurred_date=date(2026, 9, 20), provider_name="Clinic of Secrets",
        invoice_number=f"INV-{cid.upper()}", amount_claimed=Decimal("25.00"), currency="SGD",
        status=status, submitted_at=submitted,
    )


def _seed_tenant(s: Any) -> None:
    for client, suffix, name in ((CLIENT_A1, "a1", "Erin Employee"),
                                 (CLIENT_B1, "b1", "Finn Employee")):
        s.add(PolicyYear(id=f"act-year-{suffix}", client_id=client, year=2026,
                         start_date=date(2026, 1, 1), end_date=date(2026, 12, 31),
                         status=PolicyYearStatus.active))
        s.flush()
        s.add(Employee(id=f"act-emp-{suffix}", client_id=client,
                       policy_year_id=f"act-year-{suffix}",
                       staff_id="S-A-001" if suffix == "a1" else "S-B-001", employee_name=name))
        s.add(EnrollmentWindow(id=f"act-win-{suffix}", client_id=client,
                               policy_year_id=f"act-year-{suffix}", name="Open enrolment",
                               opens_at=NOW - timedelta(days=100),
                               closes_at=NOW + timedelta(days=10)))
    s.flush()
    year_a, emp_a, year_b, emp_b = "act-year-a1", "act-emp-a1", "act-year-b1", "act-emp-b1"
    s.add_all([
        _claim("act-a1", CLIENT_A1, year_a, emp_a, status="submitted", submitted=TODAY),
        _claim("act-a2", CLIENT_A1, year_a, emp_a, status="paid", submitted=DAYS_AGO_3),
        _claim("act-a3", CLIENT_A1, year_a, emp_a, status="approved", submitted=DAYS_AGO_20),
        _claim("act-a4", CLIENT_A1, year_a, emp_a, status="draft", submitted=None),
        _claim("act-b1", CLIENT_B1, year_b, emp_b, status="needs_info", submitted=DAYS_AGO_60),
        Enrollment(id="act-en-a", window_id="act-win-a1", policy_year_id=year_a,
                   client_id=CLIENT_A1, employee_id=emp_a, status="submitted",
                   submitted_at=DAYS_AGO_3),
        Enrollment(id="act-en-b", window_id="act-win-b1", policy_year_id=year_b,
                   client_id=CLIENT_B1, employee_id=emp_b, status="submitted",
                   submitted_at=TODAY),
        AISpendLog(client_id=CLIENT_A1, operation="claim_review", model="m",
                   input_tokens=100, output_tokens=50, created_at=TODAY),
        AISpendLog(client_id=CLIENT_A1, operation="claim_review", model="m",
                   input_tokens=999, output_tokens=1, cache_hit=True, created_at=TODAY),
        AISpendLog(client_id=CLIENT_B1, operation="claim_review", model="m",
                   input_tokens=7, output_tokens=3, created_at=DAYS_AGO_20),
    ])


def _as(role: str, firm: str | None, *, host: str = "testserver") -> TestClient:
    app.dependency_overrides[get_current_user] = lambda: CurrentUser(
        user_id=MASTER_ID if role == "system_admin" else "act-actor",
        broker_firm_id=firm, client_id=None, role=role,  # type: ignore[arg-type]
    )
    return TestClient(app, base_url=f"http://{host}")


@pytest.fixture(autouse=True)
def _fresh() -> Iterator[None]:
    clear_activity_cache()
    yield
    app.dependency_overrides.pop(get_current_user, None)
    clear_activity_cache()


@pytest.fixture
def master() -> TestClient:
    return _as("system_admin", None)


def _firm(body: dict[str, Any], firm_id: str) -> dict[str, Any]:
    return next(f for f in body["firms"] if f["firm_id"] == firm_id)


# ── Permissions ───────────────────────────────────────────────────────────────
def test_activity_is_master_admin_only_on_a_platform_host() -> None:
    paths = ["/api/v1/platform/activity", f"/api/v1/platform/firms/{FIRM_B}/activity"]
    for role in ("broker_admin", "firm_admin", "broker_viewer"):
        client = _as(role, FIRM_A)
        for path in paths:
            assert client.get(path).status_code == 403, (role, path)
    off_platform = _as("system_admin", None, host="bravo-act.localhost")
    for path in paths:
        assert off_platform.get(path).status_code == 404, path
    on_platform = _as("system_admin", None)
    for path in paths:
        assert on_platform.get(path).status_code == 200, path


# ── Counters ──────────────────────────────────────────────────────────────────
def test_counts_per_firm_for_thirty_days(master: TestClient) -> None:
    res = master.get("/api/v1/platform/activity?days=30")
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["days"] == 30
    a = _firm(body, FIRM_A)
    assert (a["name"], a["slug"], a["status"], a["is_platform_owner"]) == (
        "Alpha Brokers", "alpha-act", "active", True,
    )
    assert {k: a[k] for k in (
        "companies", "companies_portal_enabled", "staff_active", "staff_invited", "hr_users",
        "members_active", "active_sessions", "claims_submitted", "claims_open",
        "enrolments_submitted", "ai_tokens",
    )} == {
        "companies": 2, "companies_portal_enabled": 1, "staff_active": 1, "staff_invited": 1,
        "hr_users": 1, "members_active": 1, "active_sessions": 2, "claims_submitted": 3,
        "claims_open": 1, "enrolments_submitted": 1, "ai_tokens": 150,
    }
    assert a["sign_ins"] == {"staff": 2, "hr": 1, "member": 1}
    assert a["last_activity_at"] is not None


def test_a_firms_counts_never_include_another_firms_rows(master: TestClient) -> None:
    body = master.get("/api/v1/platform/activity?days=30").json()
    b = _firm(body, FIRM_B)
    assert b["companies"] == 1 and b["companies_portal_enabled"] == 1
    assert (b["staff_active"], b["staff_invited"], b["hr_users"]) == (1, 0, 0)
    assert b["members_active"] == 1
    assert b["sign_ins"] == {"staff": 0, "hr": 0, "member": 1}
    assert b["active_sessions"] == 0
    # B's one claim was submitted 60 days ago but is still open.
    assert (b["claims_submitted"], b["claims_open"]) == (0, 1)
    assert (b["enrolments_submitted"], b["ai_tokens"]) == (1, 10)
    empty = _firm(body, FIRM_EMPTY)
    assert empty["companies"] == 0 and empty["claims_submitted"] == 0
    assert empty["sign_ins"] == {"staff": 0, "hr": 0, "member": 0}
    assert empty["last_activity_at"] is None


def test_period_changes_the_window(master: TestClient) -> None:
    week = master.get("/api/v1/platform/activity?days=7").json()
    a, b = _firm(week, FIRM_A), _firm(week, FIRM_B)
    assert a["sign_ins"] == {"staff": 1, "hr": 1, "member": 1}
    assert (a["claims_submitted"], a["ai_tokens"]) == (2, 150)
    assert b["ai_tokens"] == 0
    quarter = master.get("/api/v1/platform/activity?days=90").json()
    assert _firm(quarter, FIRM_B)["sign_ins"] == {"staff": 1, "hr": 0, "member": 1}
    assert _firm(quarter, FIRM_B)["claims_submitted"] == 1


def test_totals_sum_the_firms(master: TestClient) -> None:
    body = master.get("/api/v1/platform/activity?days=30").json()
    totals = body["totals"]
    assert (totals["firms"], totals["firms_with_activity"], totals["firms_unavailable"]) == (
        3, 2, 0,
    )
    assert totals["companies"] == 3 and totals["members_active"] == 2
    assert totals["sign_ins"] == {"staff": 2, "hr": 1, "member": 2}
    assert (totals["claims_submitted"], totals["enrolments_submitted"]) == (3, 2)
    assert sum(d["sign_ins"] for d in body["daily"]) == 5
    assert sum(d["claims_submitted"] for d in body["daily"]) == 3
    assert sum(d["enrolments_submitted"] for d in body["daily"]) == 2


def test_days_must_be_seven_thirty_or_ninety(master: TestClient) -> None:
    for bad in ("0", "14", "365", "abc"):
        assert master.get(f"/api/v1/platform/activity?days={bad}").status_code == 422, bad
        assert master.get(
            f"/api/v1/platform/firms/{FIRM_A}/activity?days={bad}"
        ).status_code == 422, bad


# ── Daily series ──────────────────────────────────────────────────────────────
@pytest.mark.parametrize("days", [7, 30, 90])
def test_daily_series_is_zero_filled(master: TestClient, days: int) -> None:
    res = master.get(f"/api/v1/platform/firms/{FIRM_A}/activity?days={days}")
    assert res.status_code == 200, res.text
    body = res.json()
    assert (body["firm_id"], body["days"]) == (FIRM_A, days)
    dates = [date.fromisoformat(d["date"]) for d in body["daily"]]
    today = datetime.now(UTC).date()
    assert dates == [today - timedelta(days=days - 1 - n) for n in range(days)]
    by_day = {d["date"]: d for d in body["daily"]}
    assert by_day[TODAY.date().isoformat()]["claims_submitted"] == 1
    assert by_day[DAYS_AGO_3.date().isoformat()]["sign_ins"] == 1
    assert by_day[DAYS_AGO_3.date().isoformat()]["enrolments_submitted"] == 1
    totals = _firm(master.get(f"/api/v1/platform/activity?days={days}").json(), FIRM_A)
    assert sum(d["sign_ins"] for d in body["daily"]) == sum(totals["sign_ins"].values())
    assert sum(d["claims_submitted"] for d in body["daily"]) == totals["claims_submitted"]


def test_daily_series_for_a_quiet_firm_and_an_unknown_one(master: TestClient) -> None:
    quiet = master.get(f"/api/v1/platform/firms/{FIRM_EMPTY}/activity?days=7").json()
    assert len(quiet["daily"]) == 7
    assert all(
        (d["sign_ins"], d["claims_submitted"], d["enrolments_submitted"]) == (0, 0, 0)
        for d in quiet["daily"]
    )
    assert master.get("/api/v1/platform/firms/no-such-firm/activity").status_code == 404


# ── Privacy and caching ───────────────────────────────────────────────────────
def test_responses_carry_no_personal_values(master: TestClient) -> None:
    texts = [master.get(f"/api/v1/platform/activity?days={d}").text for d in (7, 30, 90)]
    texts += [master.get(f"/api/v1/platform/firms/{f}/activity?days=90").text
              for f in (FIRM_A, FIRM_B)]
    for text in texts:
        for value in PERSONAL:
            assert value not in text, value


def test_platform_view_is_cached_per_period(master: TestClient) -> None:
    first = master.get("/api/v1/platform/activity?days=7").json()
    late = _event(NOW, "broker", firm=FIRM_B)
    late.id = "act-late-event"
    with SessionLocal() as s:
        s.add(late)
        s.commit()
    again = master.get("/api/v1/platform/activity?days=7").json()
    assert again["generated_at"] == first["generated_at"]
    assert _firm(again, FIRM_B)["sign_ins"]["staff"] == 0
    # Another period is computed on its own; clearing the cache recomputes.
    assert _firm(master.get("/api/v1/platform/activity?days=30").json(),
                 FIRM_B)["sign_ins"]["staff"] == 1
    clear_activity_cache()
    fresh = master.get("/api/v1/platform/activity?days=7").json()
    assert _firm(fresh, FIRM_B)["sign_ins"]["staff"] == 1
    with SessionLocal() as s:
        s.query(AuthEvent).filter(AuthEvent.id == "act-late-event").delete()
        s.commit()
