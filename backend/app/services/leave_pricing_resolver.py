"""Resolve a member's buy/sell-leave price — the flex-wallet impact of trading
leave days — from the policy-year ``LeavePolicy.leave_rates`` bag.

The rate is keyed by ONE employee attribute (a grade / designation value), NOT by
age or product: ``leave_rates = {"attribute": "<key>", "rates": {<value>: rate}}``.
A member's per-day rate is ``rates[ employee[attribute] ]``. Trading N days yields a
signed flex impact — buying spends (negative), selling credits (positive) — which is
snapshotted onto the ``LeaveElection`` and folded into the member's available flex
balance. Pure helpers (no commit); the caller owns persistence.
"""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Employee
from app.models.leave_election import LeaveAction, LeaveElection, LeaveElectionStatus
from app.models.leave_policy import LeavePolicy


def validate_leave_rates_shape(leave_rates: dict) -> list[str]:
    """Write-boundary shape check for a leave-rates bag (empty list == valid)."""
    if not leave_rates:
        return []
    errs: list[str] = []
    if not isinstance(leave_rates, dict):
        return ["leave_rates must be an object."]
    attribute = leave_rates.get("attribute")
    rates = leave_rates.get("rates", {})
    if attribute is not None and not isinstance(attribute, str):
        errs.append("leave_rates.attribute must be a string.")
    if not isinstance(rates, dict):
        errs.append("leave_rates.rates must be an object keyed by attribute value.")
        return errs
    if rates and not (isinstance(attribute, str) and attribute.strip()):
        errs.append("leave_rates.attribute is required when rates are set.")
    for value, rate in rates.items():
        if rate is not None and (not isinstance(rate, (int, float)) or rate < 0):
            errs.append(f"leave_rates: rate for '{value}' must be ≥ 0.")
    return errs


def _leave_attribute(policy: LeavePolicy | None) -> str | None:
    bag = (policy.leave_rates or {}) if policy else {}
    attr = bag.get("attribute") if isinstance(bag, dict) else None
    return attr if isinstance(attr, str) and attr.strip() else None


def employee_leave_value(employee: Employee, attribute: str) -> str | None:
    """The member's value for the leave-rate attribute (derived takes precedence)."""
    derived = employee.derived_attribute_values or {}
    raw = employee.attribute_values or {}
    v = derived.get(attribute)
    if v in (None, ""):
        v = raw.get(attribute)
    return str(v) if v not in (None, "") else None


def leave_rate_for(policy: LeavePolicy | None, employee: Employee) -> float | None:
    """The member's per-day leave rate, or None when no rate applies to them."""
    attribute = _leave_attribute(policy)
    if attribute is None:
        return None
    value = employee_leave_value(employee, attribute)
    if value is None:
        return None
    rate = ((policy.leave_rates or {}).get("rates") or {}).get(value)
    return float(rate) if isinstance(rate, (int, float)) else None


# Roster attribute keys that can sensibly key a leave rate (grade / designation).
# The config offers whichever of these actually appear in the roster.
_CANDIDATE_KEYS = (
    "job_grade", "job_grade_name", "grade", "grade_name", "job_category",
    "designation", "job_title", "title", "position", "category",
)


def build_leave_rate_options(employees: list[Employee]) -> dict:
    """Distinct grade/designation values per candidate attribute across the roster.

    Returns ``{"attributes": [key, ...], "values": {key: [{value, count}, ...]}}``
    so the leave-policy config can render one rate cell per category. Derived
    attribute values take precedence over raw (matching ``employee_leave_value``).
    """
    counts: dict[str, dict[str, int]] = {}
    for e in employees:
        merged = {**(e.attribute_values or {}), **(e.derived_attribute_values or {})}
        for k in _CANDIDATE_KEYS:
            v = merged.get(k)
            if v not in (None, ""):
                counts.setdefault(k, {})
                counts[k][str(v)] = counts[k].get(str(v), 0) + 1
    attributes = sorted(counts.keys())
    values = {
        k: [
            {"value": val, "count": c}
            for val, c in sorted(counts[k].items(), key=lambda kv: (-kv[1], kv[0]))
        ]
        for k in attributes
    }
    return {"attributes": attributes, "values": values}


def leave_flex_amount(action: str, days: float, rate: float | None) -> float | None:
    """Signed flex-wallet impact of a leave trade: buy spends (-), sell credits (+).

    None when there's no priced leave: a missing OR zero rate, ``none``/zero days.
    (A rate of 0 means "unpriced" — it must not snapshot a 0.0 trade that downstream
    readers would treat as an active priced election.)
    """
    if not rate or action == LeaveAction.none or not days:
        return None
    magnitude = abs(days) * rate
    if action == LeaveAction.buy:
        return -magnitude
    if action == LeaveAction.sell:
        return magnitude
    return None


def latest_confirmed_leave(db: Session, employee: Employee) -> LeaveElection | None:
    """The member's single effective leave election = newest CONFIRMED row for the
    policy year (across windows). Both the balance reader and the revert path use
    this so they agree on which row is 'the' leave trade; an older window's
    confirmed row is superseded, not summed."""
    return db.execute(
        select(LeaveElection)
        .where(
            LeaveElection.employee_id == employee.id,
            LeaveElection.policy_year_id == employee.policy_year_id,
            LeaveElection.status == LeaveElectionStatus.confirmed,
        )
        .order_by(LeaveElection.created_at.desc())
    ).scalars().first()
