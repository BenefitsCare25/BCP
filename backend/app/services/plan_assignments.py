"""Build ``Category.plan_assignments`` from a parsed slip category.

``plan_assignments`` is the per-category financial envelope every read path
(plan_hydration → benefit statements, coverage summaries, cohort tiers, flex
pricing) consumes. This module owns the slip-side constructor; the guided-form
confirm path builds the same shape from form answers in
``api/v1/product_setups._category_plan_assignments`` (different input, same
output contract — keep the key set in sync).
"""
from __future__ import annotations

from typing import Any

from app.services.plan_hydration import GROUP_RATE_FIELDS


def build_plan_assignments(
    cat: Any,
    voluntary_rates: tuple[dict[str, Any], ...] = (),
    is_voluntary: bool = False,
    tier_labels: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Build the plan_assignments JSON from an ExtractedCategory.

    For a VOLUNTARY tier of a product that publishes an age-banded voluntary
    rate table, carry that table on the assignment so the premium for this tier
    prices off the member's age band (basis / 1000 x rate[band]) rather than the
    flat compulsory rate. Its presence is the signal that this tier is age-banded.
    """
    pa: dict[str, Any] = {
        "plan_code": cat.plan_code,
        "insured": cat.insured,
    }
    if cat.basis:
        pa["basis"] = cat.basis
    if cat.num_employees is not None:
        pa["num_employees"] = cat.num_employees
    if cat.sum_insured is not None:
        pa["sum_insured"] = cat.sum_insured
    if cat.premium_rate is not None:
        pa["premium_rate"] = cat.premium_rate
    if cat.annual_premium is not None:
        pa["annual_premium"] = cat.annual_premium
    if cat.rate_basis:
        pa["rate_basis"] = cat.rate_basis
    if cat.rate_tiers:
        pa["rate_tiers"] = cat.rate_tiers
    if cat.dependant_rate is not None:
        pa["dependant_rate"] = cat.dependant_rate
    if getattr(cat, "estimated_annual_earnings", None) is not None:
        pa["estimated_annual_earnings"] = cat.estimated_annual_earnings
    # Additive context captured by the generalized parser: the participation
    # scope ("SG Office"), whether this category covers dependants standalone,
    # and an annotated premium's full text.
    if getattr(cat, "location_scope", None):
        pa["location_scope"] = cat.location_scope
    if getattr(cat, "member_scope", None):
        pa["member_scope"] = cat.member_scope
    if getattr(cat, "premium_note", None):
        pa["premium_note"] = cat.premium_note
    # The slip's own tier vocabulary (e.g. {"SO": "Spouse"}) — kept alongside
    # the canonical rate_tiers keys so the UI can label tiers the client's way.
    if tier_labels and pa.get("rate_tiers"):
        relevant = {k: v for k, v in tier_labels.items() if k in pa["rate_tiers"]}
        if relevant:
            pa["tier_labels"] = relevant
    # A voluntary tier priced off the member's age band (basis / 1000 x
    # rate[band]) has no fixed headcount/total: the group sum_insured, headcount
    # and flat compulsory premium_rate the parser copies onto elective rows are
    # meaningless and would show a wrong "total premium". Drop those group
    # fields, flag the assignment age_banded, and carry the age-band rate table
    # (whose presence is the signal this tier is banded).
    if is_voluntary and voluntary_rates:
        for field in GROUP_RATE_FIELDS:
            pa.pop(field, None)
        pa["rate_basis"] = "age_banded"
        pa["voluntary_rates"] = [dict(b) for b in voluntary_rates]
    return pa
