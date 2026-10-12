"""The employee's own plan price tag: the broker matrix override, the
placement-slip premium source, and the full / on-change drawdown rule.
"""
from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from app.models.enrollment_window import FlexDrawdownRule, FlexPriceSource
from app.schemas.api import PlanFinancials
from app.services.cohort_tiers import tier_key
from app.services.flex_pricing.bag import (
    DEFAULT_FLEX_SOURCE,
    _gross,
    _product_block,
    _tier_row,
    age_band_label,
    gst_multiplier_for,
)
from app.services.plan_hydration import voluntary_rate_for_age


def price_tag_for(
    pricing: dict[str, Any] | None, product_id: str, key: str, age: int | None
) -> float | None:
    """The configured price tag for a (product, tier, age) — None when unset."""
    block = _product_block(pricing, product_id)
    if block is None:
        return None
    tags = block.get("price_tags")
    if not isinstance(tags, dict):
        return None
    # Overrides are deliberately category-specific. A missing exact row must
    # never borrow a sibling cohort's amount; the editor's explicit Apply action
    # is the only supported fan-out path.
    row = tags.get(key)
    if not isinstance(row, dict):
        return None
    label = age_band_label(block.get("age_bands") or [], age)
    if label is None:
        return None
    amount = row.get(label)
    return float(amount) if isinstance(amount, (int, float)) else None


# ── Flex price-tag source + drawdown rule (per-window config) ────────────────
#
# A window can fund each product's flex price tag from the placement slip's
# premium ("slip") or the portal matrix ("manual"), and draw it down in full or
# only by the upgrade/downgrade difference vs the member's default plan
# ("on_change"). These helpers resolve a single member's per-tier charge so every
# surface — live options, the election/override snapshot, bulk apply, revert and
# the benefit statement — agrees on the number.


def _per_member_slip_premium(fin: PlanFinancials | None) -> float | None:
    """The PER-MEMBER annual premium to use as a slip-derived flex price tag.

    A slip states a GROUP premium, but the flex tag is deducted from one member's
    wallet, so reduce it to a per-member figure:

    - ``flat`` rate → ``premium_rate`` IS the per-employee premium (the parsed
      ``annual_premium`` is the group total).
    - ``per_1000_si`` → ``_member_financials`` already expressed ``annual_premium``
      per member (basis / 1000 * rate).
    - ``tiered`` with an EO/ES/EC/EF rate table → the EMPLOYEE-ONLY (``EO``) rate
      IS the per-member employee premium (GHS/GMM Plan 1 EO = $1,200); dependant
      tiers price cover add-ons separately, so EO is the right base tag. Prefer it
      over averaging the group total.
    - any group total carrying a headcount → average = ``annual_premium /
      num_employees`` (covers a salary-relative basis where no per-member rate
      exists but the slip gives a group premium + count).
    - a bare group total with no per-member rate and no headcount → not reducible
      to one figure → None, so the UI shows "no slip premium" and the broker prices
      it via the matrix instead of a misleading group total."""
    if fin is None:
        return None
    if fin.rate_basis == "flat":
        rate = fin.premium_rate
        return round(float(rate), 2) if isinstance(rate, (int, float)) else None
    if fin.rate_basis == "tiered" and fin.rate_tiers:
        # EO = employee-only cohort; its rate is a genuine per-member figure (not a
        # group total). Absent an EO column (e.g. a dependant-only SO/CO/FO/SC
        # table) this falls through and the tier stays unpriced from the slip.
        eo = fin.rate_tiers.get("EO")
        rate = eo.get("rate") if isinstance(eo, dict) else None
        if isinstance(rate, (int, float)) and rate > 0:
            return round(float(rate), 2)
    ann = fin.annual_premium
    if not isinstance(ann, (int, float)):
        return None
    n = fin.num_employees
    if isinstance(n, int) and n > 1:
        return round(ann / n, 2)  # group total → average per member
    if fin.rate_basis == "per_1000_si":
        return round(float(ann), 2)  # already per member
    return None


def slip_premium_index(db: Session, policy_year_id: str) -> dict[str, dict[str, Any]]:
    """``{product_id: {tier_key: per_member_premium}}`` — the per-member slip premium
    for every electable tier in the year, the "slip" price-tag source.

    Mirrors the matrix shape (keyed by the same ``tier_key``) so a tier resolves the
    same way whichever source funds it. Built from ``list_product_tiers`` so config
    and election keys can't drift. Premiums are reduced to a per-member figure (see
    ``_per_member_slip_premium``); tiers with no derivable per-member premium (e.g.
    a tiered hospital plan) are simply absent — the caller falls back."""
    from app.services.cohort_tiers import list_product_tiers

    return slip_premium_index_from(list_product_tiers(db, policy_year_id))


def slip_premium_index_from(tier_sets: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """``slip_premium_index`` from already-loaded ``ProductTierSet``s — so a caller
    that already holds ``list_product_tiers`` (or builds both indices) avoids a
    second load.

    A tier value is normally a per-member premium (float). A voluntary LIFE tier
    instead stores an age-banded spec ``{"basis", "voluntary_rates"}`` — its slip
    price tag depends on the member's age and is computed in ``slip_premium_for``.
    """
    out: dict[str, dict[str, Any]] = {}
    for ts in tier_sets.values():
        row: dict[str, Any] = {}
        for t in ts.tiers:
            fin = t.financials
            key = tier_key(t.tier_category_id, t.plan_code)
            if (
                fin is not None
                and fin.voluntary_rates
                and isinstance(fin.sum_insured, (int, float))
            ):
                row[key] = {
                    "basis": float(fin.sum_insured),
                    "voluntary_rates": [b.model_dump() for b in fin.voluntary_rates],
                }
                continue
            prem = _per_member_slip_premium(fin)
            if prem is not None:
                row[key] = prem
        if row:
            out[ts.product_id] = row
    return out


def slip_premium_for(
    slip_idx: dict[str, Any] | None,
    product_id: str,
    key: str,
    age: int | None = None,
    voluntary_rates: list[Any] | None = None,
) -> float | None:
    """The slip premium for a (product, tier) — None when unset.

    Slip-derived data may use an unambiguous plan-code fallback when its category
    half differs; broker matrix overrides remain exact-tier only.

    A voluntary life tier stores an age-banded spec, so its price tag is computed
    from the member's ``age``: ``basis / 1000 x rate[age band]``. When ``age`` is
    unknown (no DOB) this intentionally returns None — the same as every age-banded
    matrix tier — so the coverage shows as UNPRICED (surfaced via the summary's
    ``age_known=False``) rather than guessing a wrong premium. Do NOT default the
    age to make it price."""
    val = _tier_row((slip_idx or {}).get(product_id), key)
    if isinstance(val, bool):
        return None
    if isinstance(val, (int, float)):
        return float(val)
    if isinstance(val, dict):
        basis = val.get("basis")
        rate = voluntary_rate_for_age(
            voluntary_rates if voluntary_rates is not None else val.get("voluntary_rates"),
            age,
        )
        if isinstance(basis, (int, float)) and rate is not None:
            return round(basis / 1000.0 * rate, 2)
    return None


def configured_voluntary_rates(
    pricing: dict[str, Any] | None,
    product_id: str,
    key: str | None = None,
) -> list[Any] | None:
    """A tier-specific or shared saved rate override, else slip recommendations."""
    block = _product_block(pricing, product_id) or {}
    rates_by_tier = block.get("voluntary_rates_by_tier")
    if key is not None and isinstance(rates_by_tier, dict):
        tier_rates = rates_by_tier.get(key)
        if isinstance(tier_rates, list):
            return tier_rates
    rates = block.get("voluntary_rates")
    return rates if isinstance(rates, list) else None


def _tier_charge(
    *,
    source: str,
    pricing: dict[str, Any] | None,
    slip_idx: dict[str, Any] | None,
    product_id: str,
    tier_category_id: str | None,
    plan_code: str | None,
    age: int | None,
) -> float | None:
    """One tier's flex price tag. A broker-set matrix value is a sparse OVERRIDE that
    always wins (so a wrong slip extraction can be corrected per tier); otherwise the
    "slip" source falls back to the placement-slip premium (a "from slip" product
    prices off the slip by default), while "manual" leaves an unset tier unpriced.

    This mirrors the sparse-override pattern used elsewhere (an explicit value over a
    derived default), so "from slip" and "manual" share one editable matrix — the
    source only decides whether the slip provides the baseline.

    Configured/extracted amounts are GST-exclusive; the product's GST gross-up
    (``__gst__`` stamp) applies here — the single output boundary for employee
    tags — so matrix and slip sources gross identically and exactly once."""
    key = tier_key(tier_category_id, plan_code)
    m = gst_multiplier_for(pricing, product_id)
    override = price_tag_for(pricing, product_id, key, age)
    if override is not None:
        return _gross(override, m)
    if source == FlexPriceSource.slip:
        return _gross(
            slip_premium_for(
                slip_idx,
                product_id,
                key,
                age,
                configured_voluntary_rates(pricing, product_id, key),
            ),
            m,
        )
    return None


def member_price_tag(
    *,
    source_map: dict[str, Any] | None,
    rule: str,
    pricing: dict[str, Any] | None,
    slip_idx: dict[str, Any] | None,
    product_id: str,
    age: int | None,
    declined: bool,
    tier_category_id: str | None,
    plan_code: str | None,
    default_tier_category_id: str | None,
    default_plan: str | None,
) -> float | None:
    """The flex amount drawn down for one product's elected tier.

    Picks the per-product source (slip vs manual matrix), then applies the
    company-wide drawdown rule: "full" returns the whole price tag; "on_change"
    returns only the difference vs the member's default plan (a downgrade yields a
    negative credit). Returns None when declined (declined coverage costs no flex)
    or when neither source prices the tier.

    Contract for "on_change": a tier with no configured price counts as $0 (an
    unpriced plan is free coverage), so an upgrade off an unpriced default draws
    the full elected tag. The window form surfaces unpriced products explicitly, so
    a genuine config gap is visible rather than silently mispriced. The result is
    rounded to cents on both rules so the snapshot is stable across surfaces."""
    if declined:
        return None
    source = (source_map or {}).get(product_id, DEFAULT_FLEX_SOURCE)
    elected = _tier_charge(
        source=source, pricing=pricing, slip_idx=slip_idx, product_id=product_id,
        tier_category_id=tier_category_id, plan_code=plan_code, age=age,
    )
    if rule != FlexDrawdownRule.on_change:
        return round(elected, 2) if elected is not None else None
    default = _tier_charge(
        source=source, pricing=pricing, slip_idx=slip_idx, product_id=product_id,
        tier_category_id=default_tier_category_id, plan_code=default_plan, age=age,
    )
    if elected is None and default is None:
        return None
    return round((elected or 0.0) - (default or 0.0), 2)
