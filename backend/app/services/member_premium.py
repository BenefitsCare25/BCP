"""One member's annual premium for reimbursement products (flat or tiered rates).

``plan_hydration.member_financials`` reduces SUM-INSURED products (GTL/GCI/GPA)
to the member's own figures. GP/SP/dental/hospital cover has no sum insured, so
its financials were dropped whole and the broker's Premium column read "—" for
six of a CDL member's nine products — while the slip states each rate per head:

* flat  — GCGP "1 - Employees 378 / 1 - Dependents 396.9": the employee rate,
  plus the dependant rate for each dependant actually covered;
* tiered — GHS "EO 1,041 / ES 2,602.50 / EC 2,602.50 / EF 4,164": the one tier
  the member's covered family puts them in.
"""
from __future__ import annotations

from dataclasses import dataclass

from app.schemas.api import PlanFinancials

# Tier label spellings seen on slips, per family shape.
_TIER_KEYS: dict[str, tuple[str, ...]] = {
    "EO": ("EO", "E", "EMPLOYEE", "EMPLOYEE ONLY"),
    "ES": ("ES", "E+S", "SO", "EMPLOYEE & SPOUSE"),
    "EC": ("EC", "E+C", "CO", "EMPLOYEE & CHILD", "EMPLOYEE & CHILDREN"),
    "EF": ("EF", "E+F", "FAMILY", "EMPLOYEE & FAMILY"),
}


@dataclass(frozen=True)
class MemberPremium:
    amount: float
    note: str


def family_tier(spouses: int, children: int) -> str:
    if spouses and children:
        return "EF"
    if spouses:
        return "ES"
    if children:
        return "EC"
    return "EO"


def _tier_rate(tiers: dict[str, dict[str, float]], tier: str) -> float | None:
    by_key = {str(k).strip().upper(): v for k, v in tiers.items()}
    for key in _TIER_KEYS[tier]:
        cell = by_key.get(key)
        rate = cell.get("rate") if isinstance(cell, dict) else None
        if isinstance(rate, (int, float)) and rate > 0:
            return float(rate)
    return None


def _money(value: float) -> str:
    return f"S${value:,.2f}".replace(".00", "")


def member_premium(
    fin: PlanFinancials | None, *, spouses: int = 0, children: int = 0
) -> MemberPremium | None:
    """The member's premium from a flat or tiered rate table, else None."""
    if fin is None:
        return None
    covered = spouses + children
    if fin.rate_basis == "tiered" and fin.rate_tiers:
        tier = family_tier(spouses, children)
        rate = _tier_rate(fin.rate_tiers, tier)
        if rate is None:
            return None
        return MemberPremium(round(rate, 2), f"{tier} tier rate")
    if fin.rate_basis == "flat" and isinstance(fin.premium_rate, (int, float)):
        base = float(fin.premium_rate)
        if base <= 0:
            return None
        dep_rate = fin.dependant_rate if isinstance(fin.dependant_rate, (int, float)) else None
        if covered and not dep_rate:
            # Dependants are covered but the slip prices none: the employee
            # rate alone would understate the premium as if it were the total.
            return None
        if covered and dep_rate:
            total = base + dep_rate * covered
            label = "dependant" if covered == 1 else "dependants"
            return MemberPremium(
                round(total, 2),
                f"{_money(base)} + {_money(dep_rate)} per dependant ({covered} {label})",
            )
        return MemberPremium(round(base, 2), "Per-member rate")
    return None
