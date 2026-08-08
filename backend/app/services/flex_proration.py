"""Pro-rating a flex allowance to the period a member was actually covered.

A flex wallet is an ANNUAL entitlement, but a member is rarely covered for a
whole year. Companies settle that differently, so the rule is per-scheme
configuration (`FlexScheme.scheme["eligibility"]["proration"]`, the shape
`ai_extractor` already writes):

    {"basis": "none | months_served | days_served",
     "applies_to": "leavers | joiners | both"}

``none`` is the default, so a scheme carrying no pro-ration is unaffected.

**A scheme whose AI extraction already captured `basis` WILL take effect on the
next assignment run** — the field has been written by `ai_extractor` since the
flex build and was simply never read. That cannot be neutralised by a data
migration (`provision_tenants.py` syncs tables and columns, never rows, so a
migration cannot reach the per-firm Postgres schemas), so it is handled by
making the unstated half conservative — see `applies_to` in
``proration_config`` — and by the value now being visible and editable on the
scheme form. Check the Flex form per company before the first assignment run
after deploy.

**There is no month-rounding option, deliberately.** "By months served" already
means a part month counts as a whole month — that is what choosing months over
days IS. A scheme wanting partial-month precision is choosing ``days_served``.

**Overspending cannot happen**, so nothing here computes a shortfall. A flex
wallet pays *up to* the limit: a member with S$500 left who presents a S$700 bill
utilises S$500 and pays the rest themselves. Pro-ration therefore binds FORWARD —
it limits what a member can still draw and never reaches back for money already
reimbursed.

The factor is computed at ASSIGNMENT and stored on the employee
(``Employee.flex_proration``) beside the pro-rated wallet, because six call sites
read ``flex_wallet_amount`` directly and every one of them should see the
effective figure. ``flex_pricing_resolver`` then reads the SAME stored factor to
scale the price tags — see ``factor_of``. One stored number, so the allowance and
the cover charged against it can never disagree about the period.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Any

from app.schemas.api import FlexProrationLine
from app.services.roster_attributes import (
    first_value,
    has_left,
    resolved_last_day,
    roster_date,
)

BASIS_NONE = "none"
BASIS_MONTHS = "months_served"
BASIS_DAYS = "days_served"
VALID_BASES = (BASIS_NONE, BASIS_MONTHS, BASIS_DAYS)

APPLIES_LEAVERS = "leavers"
APPLIES_JOINERS = "joiners"
APPLIES_BOTH = "both"
VALID_APPLIES_TO = (APPLIES_LEAVERS, APPLIES_JOINERS, APPLIES_BOTH)

# Where a member's cover STARTS, per the scheme's own statement. The roster's
# effective date is the default and is what `insurer_reports.benefit_window`
# already uses; the other two are fallbacks for schemes that tie entitlement to
# hiring or confirmation instead.
START_EFFECTIVE = "policy_year_start"
START_HIRE = "date_of_hire"
START_CONFIRMATION = "confirmation_date"
VALID_ENTITLEMENT_STARTS = (START_EFFECTIVE, START_HIRE, START_CONFIRMATION)

_START_KEYS: dict[str, tuple[str, ...]] = {
    START_EFFECTIVE: ("effective_date",),
    START_HIRE: ("date_of_hire", "hire_date"),
    START_CONFIRMATION: ("confirmation_date",),
}


@dataclass(frozen=True)
class ProrationConfig:
    """A scheme's pro-ration rule, already shape-guarded."""

    basis: str = BASIS_NONE
    applies_to: str = APPLIES_LEAVERS
    entitlement_start: str = START_EFFECTIVE

    @property
    def enabled(self) -> bool:
        return self.basis in (BASIS_MONTHS, BASIS_DAYS)

    @property
    def prorates_joiners(self) -> bool:
        return self.enabled and self.applies_to in (APPLIES_JOINERS, APPLIES_BOTH)

    @property
    def prorates_leavers(self) -> bool:
        return self.enabled and self.applies_to in (APPLIES_LEAVERS, APPLIES_BOTH)


@dataclass(frozen=True)
class ProrationResult:
    """One member's resolved pro-ration. ``factor`` is always in [0, 1]."""

    basis: str
    factor: float
    served: int
    total: int
    full_amount: float
    amount: float
    period_start: date
    period_end: date

    def as_dict(self) -> dict[str, Any]:
        return {
            "basis": self.basis,
            "factor": self.factor,
            "served": self.served,
            "total": self.total,
            "full_amount": self.full_amount,
            "period_start": self.period_start.isoformat(),
            "period_end": self.period_end.isoformat(),
        }


# ── Config ───────────────────────────────────────────────────────────────────
# READ side. `FlexScheme.scheme` is unvalidated JSON — a non-dict `eligibility`,
# a basis nobody recognises, an `applies_to` of 7. Every one of those has to
# degrade to "no pro-ration" rather than 500 the flex assignment for a whole
# company. The strict half lives in `proration_errors` below and runs at the
# write boundary, where a broker can actually see the message.


def proration_config(scheme: dict[str, Any] | None) -> ProrationConfig:
    """The scheme's pro-ration rule. Never raises; unknown shapes → disabled."""
    if not isinstance(scheme, dict):
        return ProrationConfig()
    eligibility = scheme.get("eligibility")
    if not isinstance(eligibility, dict):
        return ProrationConfig()

    start = eligibility.get("entitlement_start")
    entitlement_start = (
        start if start in VALID_ENTITLEMENT_STARTS else START_EFFECTIVE
    )

    raw = eligibility.get("proration")
    if not isinstance(raw, dict):
        return ProrationConfig(entitlement_start=entitlement_start)

    basis = raw.get("basis")
    applies_to = raw.get("applies_to")
    return ProrationConfig(
        basis=basis if basis in VALID_BASES else BASIS_NONE,
        # LEAVERS, not both, when unstated. `applies_to` did not exist before
        # this feature, so a stored `basis` with no `applies_to` is by definition
        # a value AI extracted from a document nobody could review — there was no
        # UI for it. Defaulting that to the widest setting would invent a
        # decision and cut every joiner's wallet on the next assignment run. It
        # is also the only rule any document we hold actually states ("pro-rated
        # for employees LEAVING service"), and the safe direction: over-
        # allocating a joiner is absorbed by the company, under-allocating one is
        # a member complaint. The scheme form always writes both fields, so an
        # absent `applies_to` never means "the broker chose nothing".
        applies_to=applies_to if applies_to in VALID_APPLIES_TO else APPLIES_LEAVERS,
        entitlement_start=entitlement_start,
    )


def proration_errors(scheme: dict[str, Any] | None) -> list[str]:
    """Human-readable validation errors for the pro-ration block (empty == ok).

    WRITE side, called from ``flex_schemes.validate_scheme``. Deliberately
    stricter than ``proration_config``: a broker saving a typo should be told,
    while a legacy row carrying one must still resolve.
    """
    errors: list[str] = []
    if not isinstance(scheme, dict):
        return errors
    eligibility = scheme.get("eligibility")
    if eligibility is None:
        return errors
    if not isinstance(eligibility, dict):
        return ["Eligibility settings must be an object."]

    start = eligibility.get("entitlement_start")
    if start is not None and start not in VALID_ENTITLEMENT_STARTS:
        errors.append(
            f"Entitlement start '{start}' must be one of "
            f"{', '.join(VALID_ENTITLEMENT_STARTS)}."
        )

    raw = eligibility.get("proration")
    if raw is None:
        return errors
    if not isinstance(raw, dict):
        return [*errors, "Pro-ration settings must be an object."]

    basis = raw.get("basis")
    if basis is not None and basis not in VALID_BASES:
        errors.append(
            f"Pro-ration basis '{basis}' must be one of {', '.join(VALID_BASES)}."
        )
    applies_to = raw.get("applies_to")
    if applies_to is not None and applies_to not in VALID_APPLIES_TO:
        errors.append(
            f"Pro-ration 'applies to' must be one of "
            f"{', '.join(VALID_APPLIES_TO)}."
        )
    return errors


# ── Periods ──────────────────────────────────────────────────────────────────


def _month_index(d: date) -> int:
    return d.year * 12 + d.month


def _months_touched(start: date, end: date) -> int:
    """Distinct calendar months the inclusive range touches (a part month counts
    whole — see the module docstring)."""
    return _month_index(end) - _month_index(start) + 1


def _days_touched(start: date, end: date) -> int:
    return (end - start).days + 1


def entitlement_period(
    period_start: date | None, period_end: date | None
) -> tuple[date, date] | None:
    """The window a full allowance buys — the DENOMINATOR.

    Callers pass the flex effective window already intersected with the policy
    year (``flex_membership.flex_effective_window`` bounds do exactly that).
    Returns None when either bound is missing or the window is inverted, which
    disables pro-ration rather than dividing by a period we cannot describe.
    """
    if period_start is None or period_end is None:
        return None
    if period_end < period_start:
        return None
    return period_start, period_end


def _entitlement_start_for(
    attrs: dict[str, Any], config: ProrationConfig
) -> date | None:
    keys = _START_KEYS.get(config.entitlement_start, _START_KEYS[START_EFFECTIVE])
    value = roster_date(first_value(attrs or {}, keys))
    return value if isinstance(value, date) else None


def service_period(
    member: Any, period: tuple[date, date], config: ProrationConfig
) -> tuple[date, date] | None:
    """The member's own covered window inside the entitlement period.

    Unknown dates resolve to the period's own bounds — a member is NEVER reduced
    because their roster row was incomplete. ``applies_to`` is checked per SIDE:
    a ``leavers`` scheme forces the start back to the period's start regardless
    of when the member joined, and a ``joiners`` scheme does the mirror.
    Returns None when the member's cover does not intersect the period at all.
    """
    period_start, period_end = period
    start, end = period_start, period_end

    if config.prorates_joiners:
        joined = _entitlement_start_for(member.attribute_values or {}, config)
        if joined is not None and joined > start:
            start = joined
    if config.prorates_leavers and has_left(member):
        left = resolved_last_day(member)
        if left is not None and left < end:
            end = left

    if end < start:
        return None
    return start, end


def prorate(
    full_amount: float | None,
    member: Any,
    period: tuple[date, date] | None,
    config: ProrationConfig,
) -> ProrationResult | None:
    """Resolve one member's pro-rated allowance.

    Returns None when pro-ration does not apply (disabled, no period, no
    amount) — the caller then keeps the full annual figure and stores no
    derivation. Returns a result with ``amount = 0.0`` when the member's cover
    does not intersect the entitlement period at all.
    """
    if not config.enabled or period is None:
        return None
    if not isinstance(full_amount, (int, float)) or isinstance(full_amount, bool):
        return None

    period_start, period_end = period
    count = _months_touched if config.basis == BASIS_MONTHS else _days_touched
    total = count(period_start, period_end)
    if total <= 0:
        return None

    window = service_period(member, period, config)
    served = 0 if window is None else count(*window)
    factor = min(1.0, max(0.0, served / total))
    if served >= total:
        # Covered for the whole entitlement period — there is nothing to
        # pro-rate and nothing to explain. Returning a result here would store a
        # derivation on every full-year member, and every consumer honours
        # "present == it was pro-rated": the reports would print an Annual
        # Allocation identical to the figure beside it and "12/12 months" on
        # every row, and both the broker panel and the member's wallet page
        # would show a pro-ration note to people who were there all year.
        return None

    return ProrationResult(
        basis=config.basis,
        factor=factor,
        served=served,
        total=total,
        full_amount=float(full_amount),
        amount=round(float(full_amount) * factor, 2),
        period_start=period_start,
        period_end=period_end,
    )


# ── Read-back ────────────────────────────────────────────────────────────────


def factor_of(member: Any) -> float:
    """The stored pro-ration factor for a member, or 1.0.

    The ONE read-back used by `flex_pricing_resolver` to scale price tags. It
    deliberately reads the stored bag rather than recomputing: the tags are then
    guaranteed to be scaled by the very factor that sized the wallet they are
    drawn from, and it costs no query.
    """
    raw = getattr(member, "flex_proration", None)
    if not isinstance(raw, dict):
        return 1.0
    value = raw.get("factor")
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        return 1.0
    return min(1.0, max(0.0, float(value)))


def describe(raw: object) -> str:
    """"6/12 months" / "182/365 days" — the fraction printed beside a figure.

    Empty string when there is no pro-ration to describe, so a caller can print
    it unconditionally.
    """
    if not isinstance(raw, dict):
        return ""
    basis = raw.get("basis")
    served, total = raw.get("served"), raw.get("total")
    if basis not in (BASIS_MONTHS, BASIS_DAYS):
        return ""
    if not isinstance(served, int) or not isinstance(total, int) or total <= 0:
        return ""
    unit = "months" if basis == BASIS_MONTHS else "days"
    return f"{served}/{total} {unit}"


def proration_line(employee: Any) -> FlexProrationLine | None:
    """The stored derivation behind a pro-rated wallet, as API output.

    None when nothing was pro-rated, so a surface can render the explanation
    unconditionally and print nothing in the ordinary case. Shape-guarded: the
    column is JSON and a legacy/hand-edited row must not break the caller.

    It lives HERE, beside ``describe``, because three surfaces answer "why is
    this allowance not the full year's" — the benefit statement, the utilization
    payload and the enrollment options — and a member moving between them must
    not meet three spellings of one derivation.
    """
    raw = getattr(employee, "flex_proration", None)
    if not isinstance(raw, dict):
        return None
    try:
        return FlexProrationLine(
            basis=str(raw["basis"]),
            factor=float(raw["factor"]),
            served=int(raw["served"]),
            total=int(raw["total"]),
            full_amount=float(raw["full_amount"]),
            note=describe(raw),
            period_start=raw.get("period_start"),
            period_end=raw.get("period_end"),
        )
    except (KeyError, TypeError, ValueError):
        return None


__all__ = [
    "APPLIES_BOTH",
    "APPLIES_JOINERS",
    "APPLIES_LEAVERS",
    "BASIS_DAYS",
    "BASIS_MONTHS",
    "BASIS_NONE",
    "VALID_APPLIES_TO",
    "VALID_BASES",
    "VALID_ENTITLEMENT_STARTS",
    "ProrationConfig",
    "ProrationResult",
    "describe",
    "entitlement_period",
    "factor_of",
    "prorate",
    "proration_config",
    "proration_errors",
    "proration_line",
    "service_period",
]
