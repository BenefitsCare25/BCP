"""Member portal access — a DERIVED state, never a stored flag.

A leaver used to keep the portal indefinitely: nothing on the member surface
checked employee status, so a terminated employee kept their statement, claim
filing for any date in the year, enrolment, dependant self-add, the clinic
locator and — the one with a counterparty — the panel e-card, which a clinic
accepts as proof of entitlement and bills the employer's panel against. The
nulled flex wallet looked like a gate; it never was one, and pro-ration replaced
it with a bound that is correct but is not an access control.

Derived on read for the same reason the settlement SLA counters are: the answer
changes every night and there is no event to recompute a stored copy on. Design
and build order: ``docs/LEAVER_ACCESS_PLAN.md``.

Five capabilities, so the surface degrades rather than switching off:

===============  ====================================================
``RECORD``       read own statement / usage / claims / dependants
``RESPOND``      act on something that ALREADY exists — attach a
                 document to an open claim, resubmit it, post a
                 message or an enquiry
``CLAIM``        start something new: draft a claim, run intake,
                 manage referral letters
``ELECT``        enrolment elections, leave, dependant self-add
``ENTITLEMENT``  panel cards + artwork, clinic locator
===============  ====================================================

**``RESPOND`` is split from ``CLAIM`` because otherwise ``settling`` does not
work.** Answering a ``needs_info`` means attaching the document that was asked
for and resubmitting — so folding those into ``CLAIM`` would leave a member
whose run-off has expired able to read the question and unable to answer it,
which is the exact outcome ``settling`` exists to prevent. Splitting also makes
the run-off bound meaningful in the other direction: ``settling`` cannot start a
new claim against cover that ended months ago.

The capability LIST is what gets served to the client (``PortalMe.access``).
The frontend must never re-derive the matrix from ``state`` — that is the same
drift class as mirroring ``PENDING_STATUSES`` into TypeScript.
"""
from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import date, timedelta
from enum import StrEnum
from typing import Any, Literal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.clock import today as business_today
from app.models import Claim, Employee, PolicyYear
from app.models.claim import PENDING_STATUSES
from app.services.roster_attributes import cover_end, has_left

# How long a leaver keeps access when the benefit year doesn't say. Long enough
# to cover a normal reimbursement round-trip on treatment received just before
# they left; `PolicyYear.leaver_access_days` overrides it per year.
DEFAULT_LEAVER_ACCESS_DAYS = 60


class Capability(StrEnum):
    RECORD = "record"
    RESPOND = "respond"
    CLAIM = "claim"
    ELECT = "elect"
    ENTITLEMENT = "entitlement"


_ALL = frozenset(Capability)
_RUN_OFF = frozenset({Capability.RECORD, Capability.RESPOND, Capability.CLAIM})
_SETTLING = frozenset({Capability.RECORD, Capability.RESPOND})
_NONE: frozenset[Capability] = frozenset()

AccessState = Literal["active", "run_off", "settling", "ended", "unknown"]


@dataclass(frozen=True)
class MemberAccess:
    """What one member may still do, and until when.

    ``last_day`` is the STATED last day of service (``None`` when the roster
    never gave one) — it is what a surface prints. ``access_ends_on`` is the
    derived bound and may exist without it, because a terminated row with no
    date still has to be bounded by something (the benefit year).
    """

    state: AccessState
    last_day: date | None
    access_ends_on: date | None
    capabilities: frozenset[Capability]

    def allows(self, capability: Capability) -> bool:
        return capability in self.capabilities


# No Employee row anywhere for this account. NOT the same as "ended": the usual
# cause is a new benefit year whose roster hasn't been uploaded yet, and absence
# is not evidence (the rule ADC's `missing` bucket is built on). Every data
# endpoint already 404s in this state via `resolve_member_employee`, so the
# capabilities stay open — there is no reason to withhold, only nothing to show.
UNKNOWN = MemberAccess(
    state="unknown", last_day=None, access_ends_on=None, capabilities=_ALL
)


# Codes the client branches on. `access_ended` is TERMINAL — the portal client
# ends the session on it, the way it does on a 401 — while `coverage_ended` is
# an ordinary refusal a signed-in member reads and works around. They must stay
# distinct: ending the session on every capability refusal would sign a member
# out for tapping the panel-card tab.
CODE_ACCESS_ENDED = "access_ended"
CODE_COVERAGE_ENDED = "coverage_ended"

_REFUSALS: dict[Capability, str] = {
    Capability.ENTITLEMENT: (
        "Your cover has ended, so your panel card and clinic list are no "
        "longer available."
    ),
    Capability.ELECT: (
        "Your cover has ended, so you can no longer change your benefit "
        "selections."
    ),
    Capability.CLAIM: (
        "You can no longer start a new claim. If one of your existing claims "
        "is still open you can keep replying to it."
    ),
    Capability.RESPOND: "Your cover has ended.",
    Capability.RECORD: "Your cover has ended.",
}


def refusal(access: MemberAccess, capability: Capability) -> dict[str, object] | None:
    """The 403 body for a denied capability, or None when it is allowed.

    Returned rather than raised so this module stays a rule and the HTTP seam
    (`core/portal_auth.py`) stays the only thing that knows about responses.
    """
    if access.allows(capability):
        return None
    ended = access.state == "ended"
    on = access.access_ends_on
    if ended:
        message = (
            f"Your access to this portal ended on {on.isoformat()}."
            if on
            else "Your access to this portal has ended."
        )
        message += " Contact your HR team if you still need something from your record."
    else:
        message = _REFUSALS[capability]
    return {
        "code": CODE_ACCESS_ENDED if ended else CODE_COVERAGE_ENDED,
        "message": message,
        "state": access.state,
        "last_day": access.last_day.isoformat() if access.last_day else None,
        "access_ends_on": on.isoformat() if on else None,
    }


def access_payload(access: MemberAccess) -> dict[str, object]:
    """`MemberAccess` as the client receives it (`PortalAccessOut`).

    One serializer, so `/portal/me` and the broker preview cannot describe the
    same member differently — the preview's whole contract is that it shows what
    the member sees.
    """
    return {
        "state": access.state,
        "capabilities": sorted(c.value for c in access.capabilities),
        "last_day": access.last_day.isoformat() if access.last_day else None,
        "access_ends_on": (
            access.access_ends_on.isoformat() if access.access_ends_on else None
        ),
    }


def leaver_access_days(year: PolicyYear | None) -> int:
    """The configured run-off, or the default. Never negative."""
    value = getattr(year, "leaver_access_days", None) if year is not None else None
    if value is None:
        return DEFAULT_LEAVER_ACCESS_DAYS
    return max(0, int(value))


def access_for_employee(
    employee: Employee,
    year: PolicyYear | None,
    *,
    today: date,
    has_live_claim: Callable[[], bool] = lambda: False,
) -> MemberAccess:
    """The rule, as a pure function.

    ``has_live_claim`` is a CALLABLE so the query behind it runs only in the one
    branch that needs it — a member whose run-off has already expired. Passing a
    bool would put a claims query on every portal request for the whole roster.
    """
    # **Leaving is decided by the ROW's status, never by a date on file.**
    # `Last Day of Service` is a column of the member-listing template, so it
    # round-trips on every sync and an ACTIVE row can carry a stale past date —
    # which is why `adc.py` terminates only on a NEWLY stated one. Reading the
    # date alone would sign a live employee out of their own portal. Same
    # predicate the flex pro-ration bound and the claim window use, so the
    # wallet, the claim form and the door can't disagree about who has left.
    if not has_left(employee):
        return MemberAccess("active", None, None, _ALL)

    last_day = cover_end(employee)

    # Terminated with a FUTURE last day is someone on notice: still covered,
    # still entitled to their panel card, and told when that ends.
    if last_day is not None and today <= last_day:
        return MemberAccess(
            "active", last_day, last_day + timedelta(days=leaver_access_days(year)), _ALL
        )

    # They have left. The run-off is anchored to the last day when one was
    # stated, else to the END OF THE BENEFIT YEAR — cover cannot outlast the
    # year, and anchoring an unknown date on `today` would silently restart the
    # window every day, which is unlimited access wearing a bound's clothes.
    anchor = last_day if last_day is not None else getattr(year, "end_date", None)
    ends_on = anchor + timedelta(days=leaver_access_days(year)) if anchor else None

    if ends_on is None or today <= ends_on:
        return MemberAccess("run_off", last_day, ends_on, _RUN_OFF)

    # Past the window but holding a claim we haven't finished — read and reply
    # only. Without this a `needs_info` becomes unanswerable the moment the
    # clock expires, and the only outcomes are a silent rejection or broker
    # chase-work. It ends when the broker settles the last live claim.
    if has_live_claim():
        return MemberAccess("settling", last_day, ends_on, _SETTLING)

    return MemberAccess("ended", last_day, ends_on, _NONE)


def has_live_claim(db: Session, employee: Employee) -> bool:
    """Whether this member holds a claim that is neither settled nor dead.

    `PENDING_STATUSES` is defined on the model by subtraction from the settled
    set, so this asks the same question `utilization` does — spelling the
    statuses out here would drift the day one is added.
    """
    return db.execute(
        select(Claim.id)
        .where(
            Claim.employee_id == employee.id,
            Claim.policy_year_id == employee.policy_year_id,
            Claim.status.in_(PENDING_STATUSES),
        )
        .limit(1)
    ).first() is not None


def access_of(db: Session, employee: Employee, year: PolicyYear | None, *,
              today: date | None = None) -> MemberAccess:
    """`access_for_employee` with the DB-backed live-claim check wired in."""
    return access_for_employee(
        employee,
        year,
        today=today or business_today(),
        has_live_claim=lambda: has_live_claim(db, employee),
    )


def access_for_account(
    db: Session,
    *,
    member_account_id: str,
    client_id: str,
    staff_id: str,
    today: date | None = None,
) -> MemberAccess:
    """The account's access WITHOUT a resolved Employee row to hand.

    Used by sign-in and by ``GET /portal/me``, which must answer for a member
    who has no row in the current year — the case the data endpoints answer with
    a 404. Looks in the current year first, then falls back to this account's
    most recent row in ANY year, because a leaver's row stops being current at
    rollover and "you left in June" is a better answer than "no active
    coverage".

    **The lookback concludes `ended` only from a row that is actually
    terminated.** A member whose new-year roster simply hasn't landed resolves
    to `UNKNOWN`, and signs in exactly as they do today.
    """
    from app.core.portal_auth import active_policy_year  # circular at module load

    year = active_policy_year(db, client_id)
    employee = _locate(db, member_account_id, staff_id, year)
    if employee is not None:
        return access_of(db, employee, year, today=today)

    previous, previous_year = _most_recent_row(db, member_account_id, client_id)
    if previous is None or not has_left(previous):
        return UNKNOWN
    return access_of(db, previous, previous_year, today=today)


def access_map(
    db: Session,
    client_id: str,
    accounts: Sequence[Any],
    *,
    today: date | None = None,
) -> dict[str, MemberAccess]:
    """`access_for_account` for a whole roster, in a FIXED number of queries.

    The broker's member-account list is the reason this exists: it renders every
    account on the company at once (491 on CDL's), and resolving them one at a
    time would be four queries each. Four total instead: the current year, its
    employees, the lookback for accounts with no row in it, and — only for the
    ones whose run-off has already expired — their live claims.

    The two-pass shape at the end is deliberate. `access_for_employee` takes the
    live-claim check as a callable so the query fires only where it can change
    the answer; here that means resolving once with "no live claim", batching
    the check for exactly the accounts that came out `ended`, and re-resolving
    those. Anything else either asks the claims table about the whole roster or
    re-implements the state machine.
    """
    from app.core.portal_auth import active_policy_year  # circular at module load

    if not accounts:
        return {}
    when = today or business_today()
    year = active_policy_year(db, client_id)

    by_account: dict[str, Employee] = {}
    by_staff: dict[str, Employee | None] = {}
    if year is not None:
        for emp in db.execute(
            select(Employee).where(Employee.policy_year_id == year.id)
        ).scalars():
            if emp.member_account_id:
                by_account[emp.member_account_id] = emp
            elif emp.staff_id:
                # An unclaimed row matched by staff id — the same fallback
                # `resolve_member_employee` uses when a new year's roster
                # arrives before anyone has signed in against it. A REPEATED
                # staff id resolves to nothing, exactly as `_locate` does:
                # ambiguity is `resolve_member_employee`'s 409 to raise, and a
                # first-one-wins here would report a broker an access state
                # derived from a row the member's own gate refuses to pick.
                by_staff[emp.staff_id] = (
                    None if emp.staff_id in by_staff else emp
                )

    def _current(account: Any) -> Employee | None:
        found = by_account.get(account.id)
        if found is None and getattr(account, "staff_id", None):
            found = by_staff.get(account.staff_id)
        return found

    # The lookback, for accounts with no row in the current year. Ordered ASC so
    # the LAST write per account wins, which is the newest year they appear in.
    history: dict[str, tuple[Employee, PolicyYear]] = {}
    orphans = [a.id for a in accounts if _current(a) is None]
    if orphans:
        for emp, py in db.execute(
            select(Employee, PolicyYear)
            .join(PolicyYear, Employee.policy_year_id == PolicyYear.id)
            .where(
                PolicyYear.client_id == client_id,
                Employee.member_account_id.in_(orphans),
            )
            .order_by(PolicyYear.start_date.asc())
        ).all():
            history[emp.member_account_id] = (emp, py)

    # (employee, the year to resolve it against) per account, for the pass below.
    context: dict[str, tuple[Employee, PolicyYear | None]] = {}
    resolved: dict[str, MemberAccess] = {}
    for account in accounts:
        emp, emp_year = _current(account), year
        if emp is None:
            previous = history.get(account.id)
            # An ACTIVE row in an older year proves nothing about this one — it
            # means the new roster has not landed. Absence is not evidence.
            if previous is None or not has_left(previous[0]):
                resolved[account.id] = UNKNOWN
                continue
            emp, emp_year = previous
        context[account.id] = (emp, emp_year)
        resolved[account.id] = access_for_employee(emp, emp_year, today=when)

    expired = {
        aid: context[aid] for aid, a in resolved.items() if a.state == "ended"
    }
    if expired:
        live = set(
            db.execute(
                select(Claim.employee_id)
                .where(
                    Claim.employee_id.in_([e.id for e, _ in expired.values()]),
                    Claim.status.in_(PENDING_STATUSES),
                )
                .distinct()
            ).scalars()
        )
        for aid, (emp, emp_year) in expired.items():
            if emp.id in live:
                resolved[aid] = access_for_employee(
                    emp, emp_year, today=when, has_live_claim=lambda: True
                )
    return resolved


def _locate(
    db: Session, member_account_id: str, staff_id: str, year: PolicyYear | None
) -> Employee | None:
    """The member's row in `year`, without stamping anything.

    Mirrors `resolve_member_employee`'s binding rule (stamped account id, else
    an unclaimed staff-id match) but never writes: this runs on the sign-in path,
    where committing a binding as a side effect of a refusal would be wrong.
    `resolve_member_employee` stays the authority for the data path, and the
    data path always has its own row, so the two can't disagree about which
    person a request is about.
    """
    if year is None:
        return None
    employee = db.execute(
        select(Employee).where(
            Employee.policy_year_id == year.id,
            Employee.member_account_id == member_account_id,
        )
    ).scalars().one_or_none()
    if employee is not None:
        return employee
    rows = db.execute(
        select(Employee).where(
            Employee.policy_year_id == year.id,
            Employee.staff_id == staff_id,
            Employee.member_account_id.is_(None),
        )
    ).scalars().all()
    # Ambiguity is `resolve_member_employee`'s 409 to raise, not ours — an
    # access check must not be the thing that decides a member has two rows.
    return rows[0] if len(rows) == 1 else None


def _most_recent_row(
    db: Session, member_account_id: str, client_id: str
) -> tuple[Employee | None, PolicyYear | None]:
    """This account's newest Employee row across every benefit year.

    Matched on the STAMPED binding only. A staff-id fallback here would reach
    across years into rows the member was never bound to, and the question being
    asked ("did this person leave?") is not worth a guess.
    """
    row = db.execute(
        select(Employee, PolicyYear)
        .join(PolicyYear, Employee.policy_year_id == PolicyYear.id)
        .where(
            PolicyYear.client_id == client_id,
            Employee.member_account_id == member_account_id,
        )
        .order_by(PolicyYear.start_date.desc())
        .limit(1)
    ).first()
    return (row[0], row[1]) if row else (None, None)
