"""Premium tables and plan facts for the e-form — the figures the paper forms
printed beside each plan.

* ``product_contributions`` — per plan, the FULL annual premium per family
  composition (EO/ES/EC/EF, GST-inclusive where the product grosses GST) and
  the member's SHARE of it. Only for products the broker set a share for: the
  premium of a company-paid plan is not a price the member acts on (the same
  rule as ``enrollment_elections._member_safe_options``).
* ``plan_facts`` — every plan's key benefit (room & board for hospital cover)
  and sum insured, which a member may always see.

Figures come from the UNSCRUBBED options payload (``build_enrollment_options``),
already reduced to one member and grossed for GST there.
"""
from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Plan, Product
from app.schemas.api import PlanFinancials
from app.schemas.enrollment import CohortTierOut, ProductTierSetOut
from app.schemas.enrollment_forms import (
    ContributionTierOut,
    FormContribution,
    FormSettings,
    PlanFactOut,
    ProductContributionOut,
)
from app.services.member_premium import member_premium

_COMPOSITIONS: tuple[tuple[str, str | None, int, int], ...] = (
    ("EO", None, 0, 0),
    ("ES", "spouse", 1, 0),
    ("EC", "child", 0, 1),
    ("EF", "both", 1, 1),
)


def tier_dependant_participation(ts: ProductTierSetOut, tier: CohortTierOut | None) -> str | None:
    """How family cover is taken on this exact plan — the same value the portal
    deck reads (``dependantParticipationFor``), so server checks and the screen
    agree."""
    if tier is not None and tier.dependant_participation is not None:
        return tier.dependant_participation
    return ts.dependant_participation


def _share(amount: float | None, pct: float | None) -> float | None:
    if amount is None or pct is None:
        return None
    return round(amount * pct / 100.0, 2)


def own_premium(fin: PlanFinancials | None) -> float | None:
    """The member's own (employee-only) annual premium on one plan."""
    if fin is None:
        return None
    eo = member_premium(fin)
    if eo is not None:
        return eo.amount
    return float(fin.annual_premium) if isinstance(fin.annual_premium, (int, float)) else None


def _own_share(own: float, share: FormContribution, base_own: float | None) -> float | None:
    """The member's share of their own cover: a straight percentage, or — when
    the company pays the default plan — the upgrade share of the extra over it."""
    if share.employee_pct is not None:
        return _share(own, share.employee_pct)
    if share.upgrade_pct is not None and base_own is not None:
        return _share(max(0.0, own - base_own), share.upgrade_pct)
    return None


def contribution_tier(
    key: str,
    fin: PlanFinancials | None,
    share: FormContribution,
    base_own: float | None = None,
) -> ContributionTierOut | None:
    if fin is None:
        return None
    eo = member_premium(fin)
    if fin.rate_basis == "tiered" and eo is not None:
        premium: dict[str, float] = {}
        family: dict[str, float] = {}
        for label, role, spouses, children in _COMPOSITIONS:
            priced = member_premium(fin, spouses=spouses, children=children)
            if priced is None:
                continue
            premium[label] = priced.amount
            if role is not None:
                amount = _share(priced.amount - eo.amount, share.dependant_pct)
                if amount is not None:
                    family[role] = amount
        return ContributionTierOut(
            tier_key=key, mode="tiered", premium=premium,
            employee=_own_share(eo.amount, share, base_own), family=family,
        )
    own = eo.amount if eo is not None else fin.annual_premium
    if own is None:
        return None
    per_dep = fin.dependant_rate if isinstance(fin.dependant_rate, (int, float)) else None
    return ContributionTierOut(
        tier_key=key,
        mode="flat",
        premium={"EO": round(float(own), 2)},
        premium_per_dependant=round(float(per_dep), 2) if per_dep is not None else None,
        employee=_own_share(float(own), share, base_own),
        per_dependant=_share(per_dep, share.dependant_pct),
    )


def product_contributions(
    products: list[ProductTierSetOut], settings: FormSettings
) -> list[ProductContributionOut]:
    out: list[ProductContributionOut] = []
    for ts in products:
        share = settings.contributions.get(ts.product_code)
        if share is None or (
            share.employee_pct is None
            and share.dependant_pct is None
            and share.upgrade_pct is None
        ):
            continue
        base = next((t for t in ts.tiers if t.is_baseline), None)
        base_own = own_premium(base.financials) if base else None
        tiers = [
            tier
            for t in ts.tiers
            if (tier := contribution_tier(t.key, t.financials, share, base_own)) is not None
        ]
        if not tiers:
            continue
        out.append(
            ProductContributionOut(
                product_code=ts.product_code,
                employee_pct=share.employee_pct,
                dependant_pct=share.dependant_pct,
                upgrade_pct=share.upgrade_pct,
                gst_included=any(t.financials and t.financials.gst_included for t in ts.tiers),
                tiers=tiers,
            )
        )
    return out


def _highlight(schedule: dict[str, Any] | None) -> str | None:
    """The one benefit a paper form names beside the plan: hospital room &
    board ("Daily Room & Board: 1 Bed Restr."). None for other cover."""
    items = (schedule or {}).get("items") if isinstance(schedule, dict) else None
    if not isinstance(items, list):
        return None
    for item in items:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "")
        value = item.get("value")
        if "room" in name.lower() and value not in (None, ""):
            return f"{name.strip()}: {str(value).strip()}"
    return None


def plan_facts(
    db: Session, products: list[ProductTierSetOut], policy_year_id: str
) -> list[PlanFactOut]:
    plans = db.execute(
        select(Plan.product_id, Plan.code, Plan.benefit_schedule).where(
            Plan.policy_year_id == policy_year_id,
            Plan.product_id.in_([ts.product_id for ts in products]),
        )
    ).all() if products else []
    highlight = {(pid, code): _highlight(schedule) for pid, code, schedule in plans}
    insurers: dict[str, str] = {
        str(pid): str(name)
        for pid, name in db.execute(
            select(Product.id, Product.insurer).where(
                Product.id.in_([ts.product_id for ts in products]),
                Product.insurer.is_not(None),
            )
        ).all()
    } if products else {}
    out: list[PlanFactOut] = []
    for ts in products:
        for t in ts.tiers:
            si = t.financials.sum_insured if t.financials else None
            out.append(
                PlanFactOut(
                    product_code=ts.product_code,
                    tier_key=t.key,
                    label=t.label,
                    highlight=highlight.get((ts.product_id, t.plan_code or "")),
                    sum_insured=float(si) if isinstance(si, (int, float)) and si > 0 else None,
                    insurer=insurers.get(ts.product_id),
                )
            )
    return out
