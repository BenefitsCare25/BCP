"""Member portal access — the derived state machine and the gate on it.

Steps 2-3 of `docs/LEAVER_ACCESS_PLAN.md`: which capabilities a member still
holds and until when, and the `requires=` gate that `resolve_member_employee`
applies to every portal endpoint.
"""
from __future__ import annotations

from datetime import date, timedelta

import pytest

# No `INSPRO_DATABASE_URL` here: `conftest.py` pins one suite database and
# recreates its schema before every module.
from app.db.session import SessionLocal
from app.models import BrokerFirm, Claim, Client, Employee, PolicyYear
from app.models.claim import (
    CLAIM_STATUS_DRAFT,
    CLAIM_STATUS_NEEDS_INFO,
    CLAIM_STATUS_PAID,
    CLAIM_STATUS_REJECTED,
    PENDING_STATUSES,
)
from app.models.employee import (
    EMPLOYEE_STATUS_ACTIVE,
    EMPLOYEE_STATUS_TERMINATED,
)
from app.models.policy_year import PolicyYearStatus
from app.services.member_access import (
    DEFAULT_LEAVER_ACCESS_DAYS,
    Capability,
    access_for_account,
    access_for_employee,
    has_live_claim,
    leaver_access_days,
)

YEAR_START = date(2029, 1, 1)
YEAR_END = date(2029, 12, 31)
LAST_DAY = date(2029, 6, 30)


def _year(days: int | None = None) -> PolicyYear:
    return PolicyYear(
        id="py-access",
        client_id="c1",
        year=2029,
        start_date=YEAR_START,
        end_date=YEAR_END,
        status=PolicyYearStatus.active,
        leaver_access_days=days,
    )


def _emp(
    *,
    status: str = EMPLOYEE_STATUS_ACTIVE,
    terminated_effective: date | None = None,
    attrs: dict | None = None,
) -> Employee:
    return Employee(
        id="e1",
        client_id="c1",
        policy_year_id="py-access",
        staff_id="S1",
        status=status,
        terminated_effective=terminated_effective,
        attribute_values=attrs or {},
    )


def _access(employee, year=None, *, today: date, live: bool = False):
    return access_for_employee(
        employee, year if year is not None else _year(), today=today,
        has_live_claim=lambda: live,
    )


# ── The run-off window ────────────────────────────────────────────────────────


def test_configured_days_override_the_default_and_zero_is_a_real_value():
    assert leaver_access_days(_year(None)) == DEFAULT_LEAVER_ACCESS_DAYS
    assert leaver_access_days(None) == DEFAULT_LEAVER_ACCESS_DAYS
    # 0 means "access ends on the last day" — it must NOT read as "unset".
    assert leaver_access_days(_year(0)) == 0
    assert leaver_access_days(_year(14)) == 14
    # A negative would push the end date BEFORE the last day, revoking access
    # from someone still covered.
    assert leaver_access_days(_year(-5)) == 0


# ── Who counts as a leaver ────────────────────────────────────────────────────


def test_a_stale_last_day_on_an_ACTIVE_row_changes_nothing():
    """`Last Day of Service` is a member-listing column, so it round-trips on
    every sync and an active row can carry a stale past date (a rehire, a date
    nobody cleared). Reading the date alone would sign a live employee out of
    their own portal — the same defect `flex_proration._has_left` exists to
    avoid, and the same predicate is now shared."""
    emp = _emp(attrs={"last_day_of_service": "2029-02-01"})
    access = _access(emp, today=date(2029, 8, 1))
    assert access.state == "active"
    assert access.capabilities == frozenset(Capability)
    # Nothing is printed either — the date is not evidence, so it is not a fact
    # about this member's cover.
    assert access.last_day is None and access.access_ends_on is None


def test_a_future_last_day_is_someone_on_notice_and_still_fully_covered():
    emp = _emp(status=EMPLOYEE_STATUS_TERMINATED, terminated_effective=LAST_DAY)
    access = _access(emp, today=date(2029, 6, 1))
    assert access.state == "active"
    assert access.allows(Capability.ENTITLEMENT)  # the panel card still works
    assert access.last_day == LAST_DAY
    assert access.access_ends_on == LAST_DAY + timedelta(days=DEFAULT_LEAVER_ACCESS_DAYS)


def test_the_last_day_itself_is_still_covered():
    emp = _emp(status=EMPLOYEE_STATUS_TERMINATED, terminated_effective=LAST_DAY)
    assert _access(emp, today=LAST_DAY).state == "active"


# ── Run-off ───────────────────────────────────────────────────────────────────


def test_run_off_keeps_the_record_and_claim_filing_and_drops_the_rest():
    emp = _emp(status=EMPLOYEE_STATUS_TERMINATED, terminated_effective=LAST_DAY)
    access = _access(emp, today=LAST_DAY + timedelta(days=1))
    assert access.state == "run_off"
    assert access.capabilities == frozenset(
        {Capability.RECORD, Capability.RESPOND, Capability.CLAIM}
    )
    # The one that carries an entitlement a third party acts on, and the one
    # that changes next year's cover, go the moment cover ends.
    assert not access.allows(Capability.ENTITLEMENT)
    assert not access.allows(Capability.ELECT)


def test_the_run_off_boundary_is_inclusive():
    emp = _emp(status=EMPLOYEE_STATUS_TERMINATED, terminated_effective=LAST_DAY)
    ends = LAST_DAY + timedelta(days=30)
    assert _access(emp, _year(30), today=ends).state == "run_off"
    assert _access(emp, _year(30), today=ends + timedelta(days=1)).state == "ended"


def test_zero_days_ends_access_the_day_after_the_last_day():
    emp = _emp(status=EMPLOYEE_STATUS_TERMINATED, terminated_effective=LAST_DAY)
    assert _access(emp, _year(0), today=LAST_DAY).state == "active"
    assert _access(emp, _year(0), today=LAST_DAY + timedelta(days=1)).state == "ended"


def test_terminated_with_no_date_is_bounded_by_the_YEAR_not_by_today():
    """A terminated row with nothing on file has left — the dangerous
    capabilities go immediately — but the run-off has to be anchored to
    something fixed. Anchoring it on `today` would restart the window every day,
    which is unlimited access wearing a bound's clothes."""
    emp = _emp(status=EMPLOYEE_STATUS_TERMINATED)
    access = _access(emp, _year(30), today=date(2029, 7, 1))
    assert access.state == "run_off"
    assert not access.allows(Capability.ENTITLEMENT)
    # No date is PRINTED (the roster never stated one) but the bound exists.
    assert access.last_day is None
    assert access.access_ends_on == YEAR_END + timedelta(days=30)
    assert _access(emp, _year(30), today=date(2030, 2, 1)).state == "ended"


# ── Settling ──────────────────────────────────────────────────────────────────


def test_a_live_claim_holds_read_access_open_past_the_window():
    """Otherwise a `needs_info` becomes unanswerable the moment the clock
    expires, and the only outcomes are a silent rejection or broker chase-work."""
    emp = _emp(status=EMPLOYEE_STATUS_TERMINATED, terminated_effective=LAST_DAY)
    expired = LAST_DAY + timedelta(days=DEFAULT_LEAVER_ACCESS_DAYS + 1)

    settling = _access(emp, today=expired, live=True)
    assert settling.state == "settling"
    assert settling.capabilities == frozenset(
        {Capability.RECORD, Capability.RESPOND}
    )
    # RESPOND is what makes this state worth having: answering a `needs_info`
    # means attaching the document that was asked for and resubmitting, so
    # folding those into CLAIM would leave the member able to read the question
    # and unable to answer it.
    assert settling.allows(Capability.RESPOND)
    # They still cannot start anything new against cover that ended two months
    # ago, nor reach the panel card or the enrolment window.
    assert not settling.allows(Capability.CLAIM)
    assert not settling.allows(Capability.ENTITLEMENT)

    assert _access(emp, today=expired, live=False).state == "ended"


def test_settling_is_only_reachable_after_the_window_expires():
    """A live claim inside the run-off must not DOWNGRADE anyone — during
    run-off they can still file, and a member with one open claim would
    otherwise lose that."""
    emp = _emp(status=EMPLOYEE_STATUS_TERMINATED, terminated_effective=LAST_DAY)
    access = _access(emp, today=LAST_DAY + timedelta(days=1), live=True)
    assert access.state == "run_off"
    assert access.allows(Capability.CLAIM)


def test_ended_holds_nothing():
    emp = _emp(status=EMPLOYEE_STATUS_TERMINATED, terminated_effective=LAST_DAY)
    access = _access(emp, today=date(2030, 6, 1))
    assert access.state == "ended"
    assert access.capabilities == frozenset()
    assert not any(access.allows(c) for c in Capability)


def test_the_live_claim_check_is_not_run_unless_it_can_change_the_answer():
    """It is a DB query, so it must not fire on every portal request for every
    member — only in the one branch where the run-off has already expired."""
    calls: list[int] = []
    emp = _emp(status=EMPLOYEE_STATUS_TERMINATED, terminated_effective=LAST_DAY)

    def counted() -> bool:
        calls.append(1)
        return False

    access_for_employee(emp, _year(), today=LAST_DAY, has_live_claim=counted)
    access_for_employee(
        emp, _year(), today=LAST_DAY + timedelta(days=1), has_live_claim=counted
    )
    assert calls == []

    access_for_employee(
        emp, _year(), today=date(2030, 6, 1), has_live_claim=counted
    )
    assert calls == [1]


# ── The live-claim query ──────────────────────────────────────────────────────


@pytest.fixture(scope="module", autouse=True)
def _db():
    with SessionLocal() as s:
        s.add(BrokerFirm(id="f1", name="Test Firm"))
        s.flush()
        s.add(Client(id="c1", name="C1", broker_firm_id="f1"))
        s.flush()
        s.add(_year())
        s.flush()
        s.add(_emp())
        s.commit()
    yield


@pytest.fixture(autouse=True)
def _clean_claims():
    yield
    with SessionLocal() as s:
        s.query(Claim).delete()
        s.commit()


def _claim(status: str) -> None:
    with SessionLocal() as s:
        s.add(
            Claim(
                client_id="c1",
                policy_year_id="py-access",
                employee_id="e1",
                claim_kind="insured",
                product_code="GHS",
                claim_type="outpatient",
                incurred_date=date(2029, 5, 1),
                amount_claimed=100.0,
                currency="SGD",
                status=status,
            )
        )
        s.commit()


@pytest.mark.parametrize("status", sorted(PENDING_STATUSES))
def test_every_pending_status_counts_as_live(status: str):
    """Parametrised over the SERVER's own set rather than a list written here —
    `PENDING_STATUSES` is defined by subtraction, so a hand-written copy would
    stop covering a status the day one is added, which is exactly how a leaver
    would quietly lose the access that lets them answer it."""
    _claim(status)
    with SessionLocal() as s:
        assert has_live_claim(s, s.get(Employee, "e1")) is True


@pytest.mark.parametrize(
    "status", [CLAIM_STATUS_DRAFT, CLAIM_STATUS_REJECTED, CLAIM_STATUS_PAID]
)
def test_a_dead_or_settled_claim_does_not_hold_access_open(status: str):
    _claim(status)
    with SessionLocal() as s:
        assert has_live_claim(s, s.get(Employee, "e1")) is False


def test_needs_info_is_the_case_this_exists_for():
    _claim(CLAIM_STATUS_NEEDS_INFO)
    with SessionLocal() as s:
        assert has_live_claim(s, s.get(Employee, "e1")) is True


# ── Resolving without an Employee row to hand (sign-in, /me) ──────────────────


def _prior_year_row(account_id: str, *, status: str) -> None:
    """A row in an ARCHIVED year — the shape a leaver has after rollover."""
    left = date(2028, 6, 30) if status == EMPLOYEE_STATUS_TERMINATED else None
    with SessionLocal() as s:
        if s.get(PolicyYear, "py-prior") is None:
            s.add(
                PolicyYear(
                    id="py-prior", client_id="c1", year=2028,
                    start_date=date(2028, 1, 1), end_date=date(2028, 12, 31),
                    status=PolicyYearStatus.archived,
                )
            )
            s.flush()
        s.add(
            Employee(
                id=f"prior-{account_id}", client_id="c1", policy_year_id="py-prior",
                staff_id=f"X-{account_id}", status=status,
                member_account_id=account_id,
                terminated_effective=left,
                attribute_values={},
            )
        )
        s.commit()


def _for_account(account_id: str, staff_id: str, today: date):
    with SessionLocal() as s:
        return access_for_account(
            s, member_account_id=account_id, client_id="c1",
            staff_id=staff_id, today=today,
        )


def test_an_account_with_no_row_anywhere_is_UNKNOWN_not_ended():
    """The usual cause is a new benefit year whose roster hasn't been uploaded
    yet. Absence is not evidence — the rule ADC's `missing` bucket is built on —
    so this member signs in exactly as they do today and the data endpoints
    answer with their own 404."""
    access = _for_account("acc-nothing", "NOBODY", date(2029, 7, 1))
    assert access.state == "unknown"
    assert access.capabilities == frozenset(Capability)


def test_a_row_in_the_current_year_wins_over_any_history():
    _prior_year_row("acc-rehired", status=EMPLOYEE_STATUS_TERMINATED)
    with SessionLocal() as s:
        s.add(
            Employee(
                id="cur-rehired", client_id="c1", policy_year_id="py-access",
                staff_id="X-rehired-now", status=EMPLOYEE_STATUS_ACTIVE,
                member_account_id="acc-rehired", attribute_values={},
            )
        )
        s.commit()
    # Left in 2028, back on the 2029 roster: they are an employee, full stop.
    assert _for_account("acc-rehired", "X-rehired-now", date(2029, 7, 1)).state == "active"


def test_a_leaver_whose_year_has_rolled_over_still_resolves_to_ended():
    """After rollover a leaver has no row in the current year, so the data
    endpoints 404 "No active coverage" — which is indistinguishable from a
    company that hasn't been configured. The lookback is what lets sign-in say
    the true thing instead."""
    _prior_year_row("acc-gone", status=EMPLOYEE_STATUS_TERMINATED)
    access = _for_account("acc-gone", "X-acc-gone", date(2029, 7, 1))
    assert access.state == "ended"
    assert access.last_day == date(2028, 6, 30)


def test_the_lookback_concludes_ended_only_from_a_TERMINATED_row():
    """An ACTIVE row in last year's roster proves nothing about this year — it
    means the new roster hasn't landed, which must never read as "you left"."""
    _prior_year_row("acc-waiting", status=EMPLOYEE_STATUS_ACTIVE)
    assert _for_account("acc-waiting", "X-acc-waiting", date(2029, 7, 1)).state == "unknown"


def test_the_lookback_never_matches_on_staff_id():
    """Reaching across years on staff id would bind an account to a row it was
    never stamped with, and "did this person leave?" is not worth a guess."""
    _prior_year_row("acc-stamped", status=EMPLOYEE_STATUS_TERMINATED)
    # Same staff id, a DIFFERENT account: no stamped row, so nothing is claimed.
    assert _for_account("acc-other", "X-acc-stamped", date(2029, 7, 1)).state == "unknown"


# ── The gate cannot be forgotten ──────────────────────────────────────────────


def test_every_portal_write_route_declares_a_capability():
    """A new mutating portal endpoint must SAY what it needs.

    `resolve_member_employee`'s `requires=` defaults to RECORD, which is the
    right default for a read and the wrong one for a write — so this is the
    thing that catches the omission. Modelled on `test_tenant_isolation.py`: the
    rule is enforced by enumerating the app's real routes, not by trusting a
    registry that has to be kept in step by hand.
    """
    import inspect

    from app.main import app

    missing: list[str] = []
    for route in app.routes:
        path = getattr(route, "path", "")
        methods = getattr(route, "methods", set()) or set()
        writes = methods - {"GET", "HEAD", "OPTIONS"}
        if not path.startswith("/api/v1/portal") or not writes:
            continue
        # `/portal/auth/*` is the anonymous sign-in surface: there is no member
        # and no Employee row yet, so there is nothing to gate. Its own refusal
        # lives in the sign-in path (step 4 of docs/LEAVER_ACCESS_PLAN.md).
        if path.startswith("/api/v1/portal/auth"):
            continue
        source = inspect.getsource(route.endpoint)
        if "resolve_member_employee" not in source:
            # Nothing member-scoped happens here at all — e.g. a static list.
            continue
        if "requires=" not in source:
            missing.append(f"{sorted(writes)} {path}")

    assert not missing, (
        "portal write routes that call resolve_member_employee without an "
        f"explicit `requires=`: {missing}"
    )


def test_the_gate_covers_every_capability_the_enum_defines():
    """A capability nobody requires is a capability that gates nothing — it
    would sit in the served list looking like protection."""
    import inspect
    import pkgutil

    import app.api.v1 as v1

    used = set()
    for mod in pkgutil.iter_modules(v1.__path__):
        if not mod.name.startswith("portal"):
            continue
        source = inspect.getsource(__import__(f"app.api.v1.{mod.name}", fromlist=["x"]))
        for cap in Capability:
            if f"Capability.{cap.name}" in source:
                used.add(cap)
    assert used == set(Capability), f"never required: {set(Capability) - used}"


# ── The batched resolver ──────────────────────────────────────────────────────


def _account(account_id: str, staff_id: str):
    from app.models import MemberAccount

    with SessionLocal() as s:
        if s.get(MemberAccount, account_id) is None:
            s.add(
                MemberAccount(
                    id=account_id, client_id="c1", staff_id=staff_id,
                    email=f"{account_id}@x.test", status="active",
                )
            )
            s.commit()

    class _Ref:
        id = account_id

    _Ref.staff_id = staff_id
    return _Ref


def test_the_batched_map_agrees_with_the_single_resolver():
    """The broker's account list resolves every account at once and the portal
    resolves one at a time. Two implementations of one rule is how a member gets
    told they are locked out on a page that still lets them in — so this pins
    that the batch and the single path give the same answer for every shape:
    a current row, a terminated one, a rolled-over leaver, and nothing at all."""
    from app.services.member_access import access_map

    refs = [
        _account("acc-map-current", "X-rehired-now"),   # row in the current year
        _account("acc-map-gone", "X-acc-gone"),         # terminated, rolled over
        _account("acc-map-waiting", "X-acc-waiting"),   # active row, older year
        _account("acc-map-nothing", "NOBODY-AT-ALL"),   # no row anywhere
    ]
    today = date(2029, 7, 1)
    with SessionLocal() as s:
        batched = access_map(s, "c1", refs, today=today)
        for ref in refs:
            single = access_for_account(
                s, member_account_id=ref.id, client_id="c1",
                staff_id=ref.staff_id, today=today,
            )
            assert batched[ref.id] == single, ref.id


def test_the_batched_map_sees_a_live_claim():
    """The live-claim check is batched separately and only for accounts whose
    run-off has expired — the branch most likely to be dropped, and the one that
    decides between `settling` (they can still answer us) and `ended`."""
    from app.models import MemberAccount
    from app.services.member_access import access_map

    ref = _account("acc-map-settling", "X-settling")
    with SessionLocal() as s:
        s.add(
            Employee(
                id="emp-settling", client_id="c1", policy_year_id="py-access",
                staff_id="X-settling", status=EMPLOYEE_STATUS_TERMINATED,
                terminated_effective=date(2029, 1, 31),
                member_account_id=ref.id, attribute_values={},
            )
        )
        s.commit()
    today = date(2029, 12, 1)  # long past the default run-off

    with SessionLocal() as s:
        assert access_map(s, "c1", [ref], today=today)[ref.id].state == "ended"

    with SessionLocal() as s:
        s.add(
            Claim(
                client_id="c1", policy_year_id="py-access",
                employee_id="emp-settling", claim_kind="insured",
                product_code="GHS", claim_type="outpatient",
                incurred_date=date(2029, 1, 5), amount_claimed=50.0,
                currency="SGD", status=CLAIM_STATUS_NEEDS_INFO,
            )
        )
        s.commit()
    with SessionLocal() as s:
        assert access_map(s, "c1", [ref], today=today)[ref.id].state == "settling"
    with SessionLocal() as s:
        s.query(Claim).filter(Claim.employee_id == "emp-settling").delete()
        s.query(Employee).filter(Employee.id == "emp-settling").delete()
        s.query(MemberAccount).filter(MemberAccount.id == ref.id).delete()
        s.commit()


def test_the_batched_map_is_a_fixed_number_of_queries():
    """It renders CDL's 491-account roster; a per-row resolve would be four
    queries each. Counted rather than asserted in prose, because the shape that
    regresses this (a `db.get` inside the loop) reads perfectly fine."""
    from sqlalchemy import event

    from app.db.session import engine
    from app.services.member_access import access_map

    refs = [
        _account(f"acc-count-{i}", f"X-count-{i}") for i in range(12)
    ]
    counted: list[str] = []

    def _count(conn, cursor, statement, params, context, many):
        counted.append(statement)

    with SessionLocal() as s:
        event.listen(engine, "before_cursor_execute", _count)
        try:
            access_map(s, "c1", refs, today=date(2029, 7, 1))
        finally:
            event.remove(engine, "before_cursor_execute", _count)
    assert len(counted) <= 4, f"{len(counted)} queries for {len(refs)} accounts"
