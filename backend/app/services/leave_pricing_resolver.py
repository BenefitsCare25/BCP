"""Resolve a member's buy/sell-leave terms — the per-day price AND the day caps —
from the policy-year ``LeavePolicy``.

Both are keyed by ONE employee attribute (a grade / designation value), NOT by age
or product, and by the SAME attribute — a leave "tier" is one value of it::

    leave_rates = {
        "attribute": "<key>",
        "rates":  {<value>: per_day_rate},
        "limits": {<value>: {"max_buy_days": n, "max_sell_days": n}},
    }

``rates`` prices a day; ``limits`` caps how many days that tier may trade. A tier
absent from ``limits`` (or carrying a null field) INHERITS the policy-level
``max_buy_days`` / ``max_sell_days``, so the global fields stay the default and
per-tier entries are a sparse override — the same shape as every other override
layer in the app. ``increment_days`` and the minimums stay global: they are a
granularity convention, not an entitlement.

Trading N days yields a signed flex impact — buying spends (negative), selling
credits (positive) — which is snapshotted onto the ``LeaveElection`` and folded
into the member's available flex balance. Pure helpers (no commit); the caller
owns persistence.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Employee
from app.models.leave_election import LeaveAction, LeaveElection, LeaveElectionStatus
from app.models.leave_policy import LeavePolicy

# Per-tier limit fields. Only the MAXIMA are per-tier — see the module docstring.
_LIMIT_FIELDS = ("max_buy_days", "max_sell_days")


def validate_leave_rates_shape(
    leave_rates: dict[str, Any],
    *,
    min_buy_days: float = 0.0,
    min_sell_days: float = 0.0,
) -> list[str]:
    """Write-boundary shape check for a leave-rates bag (empty list == valid).

    The minimums are passed in because they stay COMPANY-WIDE while the maxima are
    per tier: a tier max below the policy minimum makes the range unsatisfiable
    (``lo=2, hi=1`` rejects every possible value) and must be caught at the write
    boundary, not discovered by a member who can never save a valid number.
    """
    if not leave_rates:
        return []
    errs: list[str] = []
    if not isinstance(leave_rates, dict):
        return ["leave_rates must be an object."]
    attribute = leave_rates.get("attribute")
    rates = leave_rates.get("rates", {})
    limits = leave_rates.get("limits", {})
    if attribute is not None and not isinstance(attribute, str):
        errs.append("leave_rates.attribute must be a string.")
    if not isinstance(rates, dict):
        errs.append("leave_rates.rates must be an object keyed by attribute value.")
        return errs
    if (rates or limits) and not (isinstance(attribute, str) and attribute.strip()):
        errs.append("leave_rates.attribute is required when rates or limits are set.")
    for value, rate in rates.items():
        if rate is not None and (not isinstance(rate, (int, float)) or rate < 0):
            errs.append(f"leave_rates: rate for '{value}' must be ≥ 0.")
    if not isinstance(limits, dict):
        errs.append("leave_rates.limits must be an object keyed by attribute value.")
        return errs
    floors = {"max_buy_days": min_buy_days, "max_sell_days": min_sell_days}
    for value, entry in limits.items():
        if not isinstance(entry, dict):
            errs.append(f"leave_rates.limits: '{value}' must be an object.")
            continue
        for field in _LIMIT_FIELDS:
            days = entry.get(field)
            if days is None:
                continue
            if (
                not isinstance(days, (int, float))
                or isinstance(days, bool)
                or days < 0
            ):
                errs.append(f"leave_rates.limits: {field} for '{value}' must be ≥ 0.")
                continue
            floor = floors[field]
            if days < floor:
                errs.append(
                    f"leave_rates.limits: {field} for '{value}' ({days}) is below the "
                    f"company minimum ({floor}) — no number of days would be valid."
                )
    return errs


def leave_attribute(policy: LeavePolicy | None) -> str | None:
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
    attribute = leave_attribute(policy)
    if attribute is None:
        return None
    value = employee_leave_value(employee, attribute)
    if value is None:
        return None
    rate = ((policy.leave_rates or {}).get("rates") or {}).get(value)
    return float(rate) if isinstance(rate, (int, float)) else None


@dataclass(frozen=True)
class LeaveLimits:
    """The day caps that actually apply to ONE member."""

    max_buy_days: float
    max_sell_days: float
    # True when a per-tier entry supplied either cap, so the UI can say the limit
    # is the member's grade rather than the company default.
    from_tier: bool
    # The tier the caps were looked up by (None when the member has no value for
    # the leave attribute, or no attribute is configured).
    tier_value: str | None


def _limit_field(entry: object, field: str) -> float | None:
    if not isinstance(entry, dict):
        return None
    days = entry.get(field)
    # bool is an int subclass — a stray `true` must not read as 1 day.
    if isinstance(days, bool) or not isinstance(days, (int, float)):
        return None
    return float(days)


def leave_limits_for(
    policy: LeavePolicy | None, employee: Employee | None
) -> LeaveLimits:
    """The member's buy/sell day caps: their tier's override, else the policy default.

    Sparse by design — a tier that sets only ``max_buy_days`` still inherits the
    company ``max_sell_days``. A member with no value for the leave attribute (or a
    tier with no entry) gets the defaults, never zero: an unconfigured tier must not
    silently revoke leave trading the company has switched on.
    """
    default_buy = float(policy.max_buy_days or 0.0) if policy else 0.0
    default_sell = float(policy.max_sell_days or 0.0) if policy else 0.0
    attribute = leave_attribute(policy)
    value = (
        employee_leave_value(employee, attribute)
        if employee is not None and attribute
        else None
    )
    if policy is None or value is None:
        return LeaveLimits(default_buy, default_sell, False, value)
    entry = ((policy.leave_rates or {}).get("limits") or {}).get(value)
    buy = _limit_field(entry, "max_buy_days")
    sell = _limit_field(entry, "max_sell_days")
    return LeaveLimits(
        max_buy_days=default_buy if buy is None else buy,
        max_sell_days=default_sell if sell is None else sell,
        from_tier=buy is not None or sell is not None,
        tier_value=value,
    )


# Roster attribute keys that can sensibly key a leave rate (grade / designation).
# The config offers whichever of these actually appear in the roster.
_CANDIDATE_KEYS = (
    "job_grade", "job_grade_name", "grade", "grade_name", "job_category",
    "designation", "job_title", "title", "position", "category",
)


def build_leave_rate_options(employees: list[Employee]) -> dict[str, Any]:
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


_FALSE_FLAGS = {"false", "no", "n", "0", "0.0"}


def leave_sell_eligible(employee: Employee) -> bool:
    """Whether the member may SELL leave, from the roster flag
    ``leave_sell_eligible`` ("Eligible to Sell Leave" upload column).

    Absent = eligible (the LeavePolicy bounds still apply); only an explicit
    false-ish value blocks the sell election. Shared by the enrollment
    validation and the insurer reports so the two can't disagree.
    """
    raw = (employee.attribute_values or {}).get("leave_sell_eligible")
    if raw is None or raw == "":
        return True
    if isinstance(raw, bool):
        return raw
    return str(raw).strip().lower() not in _FALSE_FLAGS


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
