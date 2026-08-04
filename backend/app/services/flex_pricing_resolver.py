"""Resolve a member's flex "price tag" — the wallet amount spent to offset the
insured coverage they hold/elect — from the per-policy-year ``FlexPricing`` matrix.

The matrix is keyed ``product_id → tier_key → age_band_label → amount`` with the
age bands defined per product (see ``app/models/flex_pricing.py``). A member's
price tag for a product is looked up by their elected tier and their age band
(actual age as of the policy year start). Summed across products and netted
against the flex wallet, this gives the surplus they keep or the shortfall they
top up.

Pure resolution helpers (no commit); the caller owns persistence. Mirrors the
read-only shape of ``coverage_resolver`` / ``flex_membership``.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Category, Dependant, Employee, FlexPricing, FlexScheme, PolicyYear
from app.models.enrollment_window import FlexDrawdownRule, FlexPriceSource
from app.services.cohort_tiers import first_category_per_product, tier_key
from app.services.coverage_resolver import employee_category_defaults, load_overrides
from app.services.plan_hydration import (
    basis_amount,
    member_financials,
    voluntary_rate_for_age,
)
from app.services.roster_attributes import age_from_attrs, band_for_age


class DependantMode:
    """How a product prices coverage for a member's dependants (set per product in
    the pricing bag ``products[pid].dependant.mode``)."""

    none = "none"  # no dependant pricing (default — preserves prior behavior)
    family_group = "family_group"  # composition tier (EO/ES/EC/EF or EO/SO/CO/SC)
    per_pax = "per_pax"  # a per-dependant rate times covered count (e.g. GCGP)
    # Slip dependant OPTION rows that stick to the employee's elected plan
    # (GPA "Spouse (Option N)" ↔ "Manager (Option N)"): each covered dependant
    # draws the matching option row's slip rate — age-banded where the slip
    # prices dependants by age band (GTL/GCI voluntary rates).
    slip_options = "slip_options"


# Canonical, scheme-agnostic family roles. Employee-Only is the absence of a role
# (no covered dependants) and always costs $0. Two label schemes map onto these,
# chosen per product; only the display labels differ.
FAMILY_SCHEMES: dict[str, dict[str, str]] = {
    "ec_es_ef": {"none": "EO", "spouse": "ES", "child": "EC", "both": "EF"},
    "so_co_sc": {"none": "EO", "spouse": "SO", "child": "CO", "both": "SC"},
}
DEFAULT_FAMILY_SCHEME = "ec_es_ef"
FAMILY_ROLES = ("spouse", "child", "both")
# Placement-slip ``rate_tiers`` labels → canonical role (EO is the baseline rate).
_SLIP_TIER_ROLE: dict[str, str] = {"ES": "spouse", "EC": "child", "EF": "both"}

# A product's flex price tag is derived FROM THE PLACEMENT SLIP by default (the
# parsed premium); a broker opts a product into the portal matrix per window. So a
# product absent from a window's ``flex_price_source`` map resolves to "slip".
DEFAULT_FLEX_SOURCE = FlexPriceSource.slip


def _uses_slip(source_map: dict[str, Any] | None) -> bool:
    """Whether the slip index is needed: true when the map is empty (every product
    defaults to the slip source) or explicitly marks any product "slip". Only an
    explicit all-manual map skips the slip query."""
    if not source_map:
        return True
    return any(v == FlexPriceSource.slip for v in source_map.values())


def validate_pricing_shape(pricing: dict[str, Any]) -> list[str]:
    """Write-boundary shape check for a pricing bag (empty list == valid)."""
    errs: list[str] = []
    products = pricing.get("products", {})
    if not isinstance(products, dict):
        return ["'products' must be an object keyed by product_id."]
    for pid, block in products.items():
        if not isinstance(block, dict):
            errs.append(f"product '{pid}': must be an object.")
            continue
        bands = block.get("age_bands", [])
        if not isinstance(bands, list):
            errs.append(f"product '{pid}': 'age_bands' must be a list.")
        else:
            labels: set[str] = set()
            for b in bands:
                if not isinstance(b, dict) or not str(b.get("label") or "").strip():
                    errs.append(f"product '{pid}': each age band needs a label.")
                    continue
                lo, hi = b.get("min"), b.get("max")
                if isinstance(lo, (int, float)) and isinstance(hi, (int, float)) and lo > hi:
                    errs.append(f"product '{pid}': age band '{b['label']}' min > max.")
                if b["label"] in labels:
                    errs.append(f"product '{pid}': duplicate age band '{b['label']}'.")
                labels.add(b["label"])
        tags = block.get("price_tags", {})
        if not isinstance(tags, dict):
            errs.append(f"product '{pid}': 'price_tags' must be an object.")
            continue
        for key, row in tags.items():
            if not isinstance(row, dict):
                errs.append(f"product '{pid}': price row '{key}' must be an object.")
                continue
            for label, amount in row.items():
                if amount is not None and (
                    not isinstance(amount, (int, float)) or amount < 0
                ):
                    errs.append(
                        f"product '{pid}': price for '{key}'/'{label}' must be ≥ 0."
                    )
        if "dependant" in block:
            errs.extend(_validate_dependant_block(pid, block["dependant"]))
    return errs


def _is_age(v: object) -> bool:
    """A valid age bound: a non-negative int that is NOT a bool (``bool`` is an
    ``int`` subclass, so a JSON ``true`` would otherwise slip through as 1)."""
    return isinstance(v, int) and not isinstance(v, bool) and v >= 0


def _validate_dependant_block(pid: str, dep: object) -> list[str]:
    """Shape check for a product's ``dependant`` config (empty list == valid)."""
    if not isinstance(dep, dict):
        return [f"product '{pid}': 'dependant' must be an object."]
    errs: list[str] = []
    mode = dep.get("mode")
    if mode is not None and mode not in (
        DependantMode.none, DependantMode.family_group, DependantMode.per_pax
    ):
        errs.append(f"product '{pid}': dependant mode '{mode}' is not valid.")
    scheme = dep.get("scheme")
    if scheme is not None and scheme not in FAMILY_SCHEMES:
        errs.append(f"product '{pid}': dependant scheme '{scheme}' is not valid.")
    # family_tags + per_pax are keyed by tier_key (per plan), so each value is a
    # per-tier row: {role: amount} for family, {flat: amount} for per_pax.
    tags = dep.get("family_tags")
    if tags is not None and not isinstance(tags, dict):
        errs.append(f"product '{pid}': 'family_tags' must be an object.")
    elif isinstance(tags, dict):
        for key, row in tags.items():
            if not isinstance(row, dict):
                errs.append(f"product '{pid}': family_tags '{key}' must be an object.")
                continue
            for role, amount in row.items():
                if role not in FAMILY_ROLES:
                    errs.append(f"product '{pid}': family_tags role '{role}' is not valid.")
                elif amount is not None and (
                    not isinstance(amount, (int, float)) or amount < 0
                ):
                    errs.append(f"product '{pid}': family_tags '{key}/{role}' must be ≥ 0.")
    pp = dep.get("per_pax")
    if pp is not None and not isinstance(pp, dict):
        errs.append(f"product '{pid}': 'per_pax' must be an object.")
    elif isinstance(pp, dict):
        for key, row in pp.items():
            if not isinstance(row, dict):
                errs.append(f"product '{pid}': per_pax '{key}' must be an object.")
                continue
            flat = row.get("flat")
            if flat is not None and (not isinstance(flat, (int, float)) or flat < 0):
                errs.append(f"product '{pid}': per_pax '{key}' flat must be ≥ 0.")
    limits = dep.get("age_limits")
    if limits is not None and not isinstance(limits, dict):
        errs.append(f"product '{pid}': 'age_limits' must be an object.")
    elif isinstance(limits, dict):
        for role, win in limits.items():
            if role not in ("spouse", "child"):
                errs.append(f"product '{pid}': age_limits role '{role}' is not valid.")
                continue
            if not isinstance(win, dict):
                errs.append(f"product '{pid}': age_limits '{role}' must be an object.")
                continue
            lo, hi = win.get("min"), win.get("max")
            for bound, name in ((lo, "min"), (hi, "max")):
                if bound is not None and not _is_age(bound):
                    errs.append(f"product '{pid}': age_limits '{role}.{name}' must be ≥ 0.")
            if _is_age(lo) and _is_age(hi) and lo > hi:
                errs.append(f"product '{pid}': age_limits '{role}' min > max.")
    return errs


# Reserved top-level key stamped onto the pricing bag by ``get_pricing`` (never
# persisted): {"default": <flex-scheme multiplier>, "products": {pid: multiplier}}.
# ``products`` holds ONLY products with an explicit ProductTerm GST opinion — an
# explicit "off" is present as 1.0 so it can override ``default`` (the flex-scheme
# fallback). Raw configured/extracted amounts are GST-EXCLUSIVE; the multiplier
# grosses up the resolved tags at the output boundaries (_tier_charge / dependant
# tags) for flex, and the premium via ``product_premium_multiplier``.
_GST_KEY = "__gst__"
# Non-persisted stamp carrying the flex-scheme-level dependant age-limit default
# (`meta.dependant_age_limits`) so every resolver reads it without new plumbing —
# the same pattern as `_GST_KEY`. Applied by `get_pricing`.
_DEP_AGE_KEY = "__dep_age__"


def gst_multiplier_for(pricing: dict[str, Any] | None, product_id: str) -> float:
    """The gross-up factor for one product's FLEX price tag — the product's own
    explicit GST opinion (ProductTerm, incl. an explicit "off" = 1.0) when set,
    else the flex-scheme default, else 1.0. Use this for flex wallet tags only;
    the insurance PREMIUM uses ``product_premium_multiplier`` (no scheme fallback)."""
    g = (pricing or {}).get(_GST_KEY)
    if not isinstance(g, dict):
        return 1.0
    products = g.get("products")
    m = (products or {}).get(product_id) if isinstance(products, dict) else None
    if m is None:  # no product-level opinion → inherit the flex-scheme default
        m = g.get("default")
    return float(m) if isinstance(m, (int, float)) and m > 0 else 1.0


def product_premium_multiplier(pricing: dict[str, Any] | None, product_id: str) -> float:
    """The gross-up factor for a product's own PREMIUM display (benefit statement,
    enrollment option financials) — the product's explicit GST opinion ONLY, never
    the flex-scheme default (that default governs flex wallet tags, not the
    insurance premium). 1.0 when the product has no explicit opinion."""
    g = (pricing or {}).get(_GST_KEY)
    if not isinstance(g, dict):
        return 1.0
    products = g.get("products")
    m = (products or {}).get(product_id) if isinstance(products, dict) else None
    return float(m) if isinstance(m, (int, float)) and m > 0 else 1.0


def _gross(amount: float | None, multiplier: float) -> float | None:
    """Apply a GST gross-up to a resolved amount (None passes through)."""
    if amount is None or multiplier == 1.0:
        return amount
    return round(amount * multiplier, 2)


def _flex_scheme_meta(db: Session, policy_year_id: str) -> dict[str, Any]:
    """The flex scheme's ``meta`` block ({} when no scheme/meta). Loaded once and
    shared by both pricing stamps so a single FlexScheme query serves the bag."""
    scheme = db.execute(
        select(FlexScheme).where(FlexScheme.policy_year_id == policy_year_id)
    ).scalar_one_or_none()
    meta = (scheme.scheme or {}).get("meta") if scheme is not None else None
    return meta if isinstance(meta, dict) else {}


def _gst_stamp(db: Session, policy_year_id: str, meta: dict[str, Any]) -> dict | None:
    """The GST block for ``_GST_KEY`` — None when nothing is configured. ``meta`` is
    the pre-loaded flex scheme meta (see ``_flex_scheme_meta``)."""
    from app.services.product_terms import gst_multiplier, product_gst_multipliers

    products = product_gst_multipliers(db, policy_year_id)
    default = gst_multiplier(bool(meta.get("gst_included")), meta.get("gst_rate"))
    if not products and default == 1.0:
        return None
    return {"default": default, "products": products}


def _dep_age_stamp(meta: dict[str, Any]) -> dict | None:
    """The scheme-level dependant age-limit default (``meta.dependant_age_limits``)
    for the ``_DEP_AGE_KEY`` stamp — None when nothing valid is configured.
    Sanitized to ``{role: {min?, max?}}`` (valid ages only) so a malformed bag can
    never reach the resolver. Pure function of the pre-loaded scheme meta."""
    cfg = meta.get("dependant_age_limits")
    if not isinstance(cfg, dict):
        return None
    out: dict[str, dict[str, int]] = {}
    for role in DEFAULT_DEPENDANT_AGE_LIMITS:
        win = cfg.get(role)
        if isinstance(win, dict):
            w = {b: win[b] for b in ("min", "max") if _is_age(win.get(b))}
            if w:
                out[role] = w
    return out or None


def stamp_pricing(db: Session, policy_year_id: str, pricing: dict[str, Any] | None) -> dict | None:
    """Attach the non-persisted ``__gst__`` and ``__dep_age__`` stamps to a raw
    pricing bag so every resolution helper can gross up its output and resolve the
    scheme-level dependant age default without new plumbing. Loads the flex scheme
    once for both stamps. Never mutates ``pricing`` in place — a copy is made only
    when a stamp actually applies. Returns None when no pricing and no stamps."""
    meta = _flex_scheme_meta(db, policy_year_id)
    gst = _gst_stamp(db, policy_year_id, meta)
    if gst is not None:
        pricing = dict(pricing or {})
        pricing[_GST_KEY] = gst
    dep_age = _dep_age_stamp(meta)
    if dep_age is not None:
        pricing = dict(pricing or {})
        pricing[_DEP_AGE_KEY] = dep_age
    return pricing


def get_pricing(db: Session, policy_year_id: str) -> dict[str, Any] | None:
    """The policy year's flex-pricing bag (``{"products": {...}}``) or None, carrying
    the ``__gst__`` / ``__dep_age__`` stamps (see ``stamp_pricing``) — it may be a
    stamp-only bag when no pricing row exists but GST / dependant limits are set."""
    row = db.execute(
        select(FlexPricing).where(FlexPricing.policy_year_id == policy_year_id)
    ).scalar_one_or_none()
    pricing = dict(row.pricing or {}) if row else None
    return stamp_pricing(db, policy_year_id, pricing)


def _product_block(pricing: dict[str, Any] | None, product_id: str) -> dict | None:
    products = (pricing or {}).get("products")
    if not isinstance(products, dict):
        return None
    block = products.get(product_id)
    return block if isinstance(block, dict) else None


def age_band_label(age_bands: list[Any], age: int | None) -> str | None:
    """Label of the band containing ``age`` (first match wins), or None. Band
    selection is shared with the life voluntary-rate bands via ``band_for_age`` so
    the price-tag band and the premium band can't diverge."""
    band = band_for_age(age_bands, age)
    if band is None:
        return None
    label = band.get("label")
    return str(label) if label is not None else None


def _plan_of_key(key: str) -> str:
    """The plan_code half of a ``cat::plan`` tier key (empty when none)."""
    return key.split("::", 1)[1] if "::" in key else ""


def _unambiguous_by_plan(by_key: dict[str, Any], key: str):
    """The single value in ``by_key`` whose key shares ``key``'s plan_code, or None
    when zero or more than one match.

    The category half of a tier key can legitimately differ across baseline-
    selection paths (config grid picks the cohort's compulsory category; a member
    may be matched to a sibling), so an unambiguous plan_code is still safe to
    price. Ambiguous plan_codes (two tiers, same code, different value) return None
    rather than guess. Shared by the matrix (``_plan_code_fallback``) and the slip
    index (``slip_premium_for``) so both resolve a drifted category identically.
    """
    plan = _plan_of_key(key)
    if not plan:
        return None
    matches = [v for k, v in by_key.items() if _plan_of_key(k) == plan]
    return matches[0] if len(matches) == 1 else None


def _tier_row(by_key: dict[str, Any] | None, key: str):
    """A tier's value from a ``{tier_key: value}`` map: the exact key, else the
    unambiguous plan-code row (mirrors ``price_tag_for``'s fallback so a tier whose
    category half drifted still resolves). The single lookup shared by every
    slip/sub-config resolver (premium, family, per_pax, manual sub-dicts)."""
    if not isinstance(by_key, dict):
        return None
    if key in by_key:
        return by_key[key]
    return _unambiguous_by_plan(by_key, key)


def _plan_code_fallback(tags: dict[str, Any], key: str) -> dict | None:
    """Matrix plan-code fallback: the unambiguous price row for ``key``'s plan."""
    row = _unambiguous_by_plan(tags, key)
    return row if isinstance(row, dict) else None


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
    row = tags.get(key)
    if not isinstance(row, dict):
        row = _plan_code_fallback(tags, key)
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


def _per_member_slip_premium(fin) -> float | None:
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
    slip_idx: dict[str, Any] | None, product_id: str, key: str, age: int | None = None
) -> float | None:
    """The slip premium for a (product, tier) — None when unset. Mirrors
    ``price_tag_for``'s plan-code fallback so a tier whose category half differs
    from the configured key still resolves when its plan_code is unambiguous.

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
        rate = voluntary_rate_for_age(val.get("voluntary_rates"), age)
        if isinstance(basis, (int, float)) and rate is not None:
            return round(basis / 1000.0 * rate, 2)
    return None


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
        return _gross(slip_premium_for(slip_idx, product_id, key, age), m)
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


# ── Dependant coverage pricing (additive over Employee-Only) ─────────────────
#
# Covering dependants draws additional flex on top of the employee's own plan
# tier. Each product is configured (``products[pid].dependant``) as one of two
# modes: ``family_group`` (the member's composition maps to a tier increment,
# priced from the slip's EO/ES/EC/EF rate table or the portal matrix) or
# ``per_pax`` (a flat amount per covered dependant). The dependant charge is
# ALWAYS the incremental cost of the dependants added (Employee-Only = $0),
# independent of the full/on_change rule — that rule only nets the employee's own
# plan tier against their default plan.


def family_role(spouse_count: int, child_count: int) -> str | None:
    """Canonical family role from covered counts; Employee-Only (none) → None."""
    has_s, has_c = spouse_count > 0, child_count > 0
    if not has_s and not has_c:
        return None
    if has_s and not has_c:
        return "spouse"
    if has_c and not has_s:
        return "child"
    return "both"


# Default dependant eligibility age window per role, applied when a product sets
# none. A dependant outside their role's window is NOT covered (no premium, no flex).
# Bounds are in age-NEXT-birthday terms relative to the renewal date (the
# canonical convention for every extracted/configured age limit).
DEFAULT_DEPENDANT_AGE_LIMITS: dict[str, dict[str, int]] = {
    "spouse": {"min": 18, "max": 70},
    "child": {"min": 0, "max": 25},
}


def _overlay_age_window(out: dict[str, dict[str, int]], cfg: object) -> None:
    """Overlay a ``{role: {min?, max?}}`` config onto ``out`` in place, keeping only
    valid age bounds (an absent/invalid bound leaves the base untouched)."""
    if not isinstance(cfg, dict):
        return
    for role in out:
        win = cfg.get(role)
        if isinstance(win, dict):
            if _is_age(win.get("min")):
                out[role]["min"] = win["min"]
            if _is_age(win.get("max")):
                out[role]["max"] = win["max"]


def dependant_age_limits(
    pricing: dict[str, Any] | None, product_id: str
) -> dict[str, dict[str, int]]:
    """Per-product dependant eligibility windows (spouse/child min-max age),
    resolved most-specific-wins: hardcoded defaults, then the flex-scheme-level
    default (``meta.dependant_age_limits``, stamped onto the bag by ``get_pricing``),
    then the product's own ``dependant.age_limits`` override. Used to drop
    out-of-limit dependants from coverage + pricing."""
    out = {role: dict(win) for role, win in DEFAULT_DEPENDANT_AGE_LIMITS.items()}
    _overlay_age_window(out, (pricing or {}).get(_DEP_AGE_KEY))
    _overlay_age_window(out, _dependant_block(pricing, product_id).get("age_limits"))
    return out


def scheme_dependant_age_limits(meta: dict[str, Any] | None) -> dict[str, dict[str, int]]:
    """The scheme-wide dependant eligibility window (spouse/child min-max age): the
    hardcoded defaults overlaid with ``meta.dependant_age_limits``. Product-agnostic —
    this is the window that sizes family status / flex wallets, so membership matches
    the coverage/pricing eligibility; a product's own pricing entry can tighten it
    further via ``dependant_age_limits(pricing, product_id)``."""
    out = {role: dict(win) for role, win in DEFAULT_DEPENDANT_AGE_LIMITS.items()}
    _overlay_age_window(out, (meta or {}).get("dependant_age_limits"))
    return out


def role_age_eligible(
    role: str | None, age: int | None, limits: dict[str, dict[str, int]] | None
) -> bool:
    """Is a (role, age) within the role's eligibility window? Unknown role, unknown
    age, or no window → kept (can't prove ineligibility) — eligibility only excludes
    a dependant we can positively place outside the window.

    ``age`` is the actual age (last birthday) as of the renewal date, but the
    window bounds are age-NEXT-birthday (the canonical limit convention), so the
    comparison is done in ANB terms (``age + 1``). Rate-band lookups keep the
    actual age — slip rate tables are quoted "Based on Age Last Birthday"."""
    win = (limits or {}).get(role or "")
    if not win or age is None:
        return True
    anb = age + 1
    lo, hi = win.get("min"), win.get("max")
    return (lo is None or anb >= lo) and (hi is None or anb <= hi)


def _dependant_role_age(
    dep: Dependant, ref: date | None
) -> tuple[str, int | None] | None:
    """``(role, age)`` for one dependant — None when the relationship can't
    classify. The single classification rule shared by every profile loader so
    coverage, pricing, and the options display agree on who counts."""
    from app.services.flex_membership import classify_relationship

    av = dep.attribute_values or {}
    role = classify_relationship(av.get("relationship") or av.get("relation"))
    if role is None:
        return None
    return role, (age_from_attrs(av, ref) if ref is not None else None)


def _dependant_eligible(dep: Dependant, limits: dict[str, dict[str, int]], ref: date) -> bool:
    """Is ``dep`` within their role's age window? (See ``role_age_eligible``.)"""
    prof = _dependant_role_age(dep, ref)
    if prof is None:
        return True
    return role_age_eligible(prof[0], prof[1], limits)


def covered_dependant_counts(
    db: Session,
    covered_dependant_ids: list[str] | None,
    *,
    age_limits: dict[str, dict[str, int]] | None = None,
    ref: date | None = None,
) -> tuple[int, int]:
    """``(spouse_count, child_count)`` for a set of covered dependant ids.

    Reuses ``flex_membership.count_dependants`` so the spouse/child classification
    matches the Flex-membership + benefit-statement views. Empty/None → (0, 0)
    with no query.

    When ``age_limits`` + ``ref`` are given, dependants outside their role's age
    window are dropped first — they are not eligible, so they draw no flex and add
    no premium."""
    from app.services.flex_membership import count_dependants

    ids = [i for i in (covered_dependant_ids or []) if i]
    if not ids:
        return (0, 0)
    deps = list(db.execute(select(Dependant).where(Dependant.id.in_(ids))).scalars().all())
    if age_limits is not None and ref is not None:
        deps = [d for d in deps if _dependant_eligible(d, age_limits, ref)]
    return count_dependants(deps)


def covered_dependant_profiles(
    db: Session,
    covered_dependant_ids: list[str] | None,
    *,
    age_limits: dict[str, dict[str, int]] | None = None,
    ref: date | None = None,
) -> list[tuple[str, int | None]]:
    """``[(role, age)]`` per covered dependant — the per-dependant view that
    ``slip_options`` pricing needs (each dependant draws its own option rate,
    age-banded rates resolve on the DEPENDANT's age).

    Same classification and age-window filtering as ``covered_dependant_counts``
    (unclassifiable dependants are dropped in both). Derive counts from the
    profiles via ``profile_counts`` so a caller loads the dependants once."""
    ids = [i for i in (covered_dependant_ids or []) if i]
    if not ids:
        return []
    deps = list(db.execute(select(Dependant).where(Dependant.id.in_(ids))).scalars().all())
    return dependant_profiles_of(deps, age_limits=age_limits, ref=ref)


def dependant_profiles_of(
    dependants: list[Dependant],
    *,
    age_limits: dict[str, dict[str, int]] | None = None,
    ref: date | None = None,
) -> list[tuple[str, int | None]]:
    """``covered_dependant_profiles`` over ALREADY-LOADED rows.

    Bulk paths load every selected member's dependants in one query and then
    price member by member; without this they would re-SELECT the same rows once
    per employee. Same filtering, one implementation — the DB-hitting variant
    above delegates here so the two can't drift.
    """
    deps = dependants
    if age_limits is not None and ref is not None:
        deps = [d for d in deps if _dependant_eligible(d, age_limits, ref)]
    return [p for p in (_dependant_role_age(d, ref) for d in deps) if p is not None]


def dependant_profiles_by_id(
    db: Session, employee_id: str, ref: date | None
) -> dict[str, tuple[str, int | None]]:
    """``{dependant_id: (role, age)}`` for one employee's ACTIVE dependants
    (unclassifiable relationships dropped). Feeds the options API's per-dependant
    resolved amounts for age-banded dependant option levels; per-product age
    limits are applied by the caller (``role_age_eligible``) since the map is
    built once across products."""
    out: dict[str, tuple[str, int | None]] = {}
    deps = db.execute(
        select(Dependant).where(
            Dependant.employee_id == employee_id,
            Dependant.status == "active",
        )
    ).scalars()
    for d in deps:
        prof = _dependant_role_age(d, ref)
        if prof is not None:
            out[d.id] = prof
    return out


def profile_counts(profiles: list[tuple[str, int | None]]) -> tuple[int, int]:
    """``(spouse_count, child_count)`` from dependant profiles."""
    spouse = sum(1 for role, _ in profiles if role == "spouse")
    child = sum(1 for role, _ in profiles if role == "child")
    return spouse, child


def _dependant_block(pricing: dict[str, Any] | None, product_id: str) -> dict:
    block = _product_block(pricing, product_id) or {}
    dep = block.get("dependant")
    return dep if isinstance(dep, dict) else {}


def dependant_mode(pricing: dict[str, Any] | None, product_id: str) -> str:
    """The EXPLICITLY-configured dependant pricing mode for a product (``none`` when
    unset). For the resolution mode that also applies the slip default, see
    ``_effective_dependant_mode``."""
    mode = _dependant_block(pricing, product_id).get("mode")
    return (
        mode
        if mode in (DependantMode.family_group, DependantMode.per_pax)
        else DependantMode.none
    )


def _effective_dependant_mode(
    pricing: dict[str, Any] | None,
    product_id: str,
    source: str,
    family_slip_idx: dict[str, Any] | None,
    key: str,
) -> str:
    """The dependant pricing mode actually applied to ONE tier.

    Explicit config wins (including an explicit ``none`` = dependant pricing turned
    off). When UNSET, a tier whose slip carries a dependant rate and is funded from
    the slip defaults to a slip-priced mode based on THIS tier's slip shape (see
    ``_slip_dependant_shape``) — ``per_pax`` for a per-dependant rate (a flat
    per-member Dependents rate, e.g. GCGP), else ``family_group`` (EO/ES/EC/EF). The
    mode is tier-scoped, so a product whose tiers mix the two formats prices each
    tier correctly instead of forcing the whole product to one mode. Otherwise
    ``none``."""
    raw = _dependant_block(pricing, product_id).get("mode")
    if raw in (DependantMode.none, DependantMode.family_group, DependantMode.per_pax):
        return raw
    if source == FlexPriceSource.slip:
        shape = _slip_dependant_shape(family_slip_idx, product_id, key)
        if shape is not None:
            return shape
    return DependantMode.none


def _tier_rate(cell: object) -> float | None:
    """The per-member ``rate`` from a slip ``rate_tiers`` cell (``{rate, premium}``)."""
    if isinstance(cell, dict):
        r = cell.get("rate")
        return float(r) if isinstance(r, (int, float)) else None
    return None


def slip_family_increments(rate_tiers: object) -> dict[str, float]:
    """The per-role dependant increments over Employee-Only from one tier's slip
    ``rate_tiers`` (``{EO/ES/EC/EF: {rate, premium}}``).

    The increment for a role is its rate minus the EO rate. A role whose rate is
    0.0 is an UNPRICED column (slips often list only EO/EF) — not a free tier — so
    it's omitted and the caller falls back to the manual matrix. Returns {} when
    there's no EO baseline."""
    if not isinstance(rate_tiers, dict):
        return {}
    eo = _tier_rate(rate_tiers.get("EO"))
    if eo is None:
        return {}
    incr: dict[str, float] = {}
    for label, role in _SLIP_TIER_ROLE.items():
        rate = _tier_rate(rate_tiers.get(label))
        if rate is not None and rate != 0.0:
            incr[role] = round(rate - eo, 2)
    return incr


def _slip_dependant_for_tier(fin) -> dict[str, float]:
    """The slip-derived dependant pricing for ONE tier, as either:

    - family increments ``{spouse, child, both}`` from an EO/ES/EC/EF rate table, or
    - a per-dependant rate ``{"per_pax": rate}`` from a flat per-member table that
      lists a separate Dependents rate (e.g. GCGP "1 - Dependents $396.90", or the
      combined "Employees / Dependents" rate). The per-dependant rate is the
      dependant's FULL premium (drawn per covered dependant, additive over the
      employee tag), not an increment.

    ``{}`` when the slip carries no dependant pricing for the tier."""
    incr = slip_family_increments(fin.rate_tiers if fin else None)
    if incr:
        return incr
    dr = getattr(fin, "dependant_rate", None) if fin is not None else None
    if isinstance(dr, (int, float)):
        return {"per_pax": round(float(dr), 2)}
    return {}


def family_slip_index(
    db: Session, policy_year_id: str
) -> dict[str, dict[str, dict[str, float]]]:
    """``{product_id: {tier_key: <slip dependant pricing>}}`` — the slip-derived
    dependant pricing for every electable tier in the year (see
    ``_slip_dependant_for_tier``). Each tier value is EITHER family increments
    ``{spouse, child, both}`` (EO/ES/EC/EF slip) OR ``{"per_pax": rate}`` (a flat
    per-member table with a Dependents rate). Mirrors ``slip_premium_index``'s key
    shape so the employee tag and the dependant tag resolve a tier identically."""
    from app.services.cohort_tiers import list_product_tiers

    return _merge_family_overlay(
        family_slip_index_from(list_product_tiers(db, policy_year_id)),
        dependant_option_overlay(db, policy_year_id),
    )


def family_slip_index_from(tier_sets: dict[str, Any]) -> dict[str, dict[str, dict[str, float]]]:
    """``family_slip_index`` from already-loaded ``ProductTierSet``s (see
    ``slip_premium_index_from``)."""
    out: dict[str, dict[str, dict[str, float]]] = {}
    for ts in tier_sets.values():
        prod_row: dict[str, dict[str, float]] = {}
        for t in ts.tiers:
            dep = _slip_dependant_for_tier(t.financials)
            if dep:
                prod_row[tier_key(t.tier_category_id, t.plan_code)] = dep
        if prod_row:
            out[ts.product_id] = prod_row
    return out


# ── Dependant OPTION rows (slip categories that stick to the employee plan) ──
#
# The parser extracts dependant-scope categories (`plan_assignments.member_scope
# == "dependant"`): GPA "Spouse (Option N)" / "Child (Option N)" rows, VDL's
# "GHS - Dependants" sheet, GTL/GCI Spouse/Child plan rows. A dependant never
# elects its OWN option level — the covered dependant rides the EMPLOYEE's
# elected plan, so each dependant row must be keyed to the employee tier(s) it
# serves. Three linkage rules, unambiguous only:
#
# 1. Marker match — "Spouse (Option 2)" serves employee tiers whose category
#    carries the same "(Option 2)" marker (GPA).
# 2. Composition rows (rate_tiers keyed SO/CO/FO/SC — a dependants sheet) serve
#    the employee tier with the SAME plan code (VDL mirrors codes by design).
# 3. A SOLE spouse row (and/or sole child row) with no marker serves every
#    employee tier.
#
# 4. MULTIPLE unmarked rows per role (e.g. CDL GTL's three Spouse levels whose
#    "Plan 1-6" numbers collide with employee plans by renumbering) are
#    freestanding OPTION LEVELS the slip leaves to the member: they attach to
#    every employee tier as electable ``choices`` — the member (broker) picks a
#    cover level per role at election time (``dependant_option_ids``) and each
#    covered dependant prices from the chosen row. Covered dependants with NO
#    chosen level stay unpriced — surfaced by the unpriced-election guard —
#    rather than mislinked.

_OPTION_MARKER = re.compile(r"\(option\b[^)]*\)", re.IGNORECASE)
_DEP_ROLE_RE = re.compile(r"^\s*(spouse|child(?:ren)?)\b", re.IGNORECASE)
# Dependant-sheet composition tier keys → canonical role.
_COMPOSITION_ROLE: dict[str, str] = {"SO": "spouse", "CO": "child", "SC": "both", "FO": "both"}


def _option_marker(text: str | None) -> str | None:
    m = _OPTION_MARKER.search(text or "")
    return re.sub(r"\s+", " ", m.group(0).lower()) if m else None


def dependant_option_role(text: str | None) -> str | None:
    """'spouse' | 'child' from a dependant option row's description, None when
    the description names neither role."""
    m = _DEP_ROLE_RE.match(text or "")
    if not m:
        return None
    return "spouse" if m.group(1).lower().startswith("spouse") else "child"


def _dependant_option_spec(pa: dict[str, Any]) -> float | dict | None:
    """A dependant option row's per-dependant price spec: a flat amount, or an
    age-banded ``{basis, voluntary_rates}`` spec (same dual shape as
    ``slip_premium_index``) priced by the DEPENDANT's age at draw time.

    A dependant-scope row carries no headcount, so its ``sum_insured`` IS the
    per-dependant cover amount (a GPA option's SI) — unlike employee rows where
    group SI must reduce via ``basis``."""
    if not isinstance(pa, dict):
        return None
    amount = basis_amount(pa)
    si = pa.get("sum_insured")
    if amount is None and isinstance(si, (int, float)):
        amount = float(si)
    vol = pa.get("voluntary_rates")
    if isinstance(vol, list) and vol and amount is not None:
        return {"basis": amount, "voluntary_rates": [dict(b) for b in vol]}
    rate = pa.get("premium_rate")
    if (
        pa.get("rate_basis") == "per_1000_si"
        and isinstance(rate, (int, float))
        and amount is not None
    ):
        return round(amount / 1000.0 * float(rate), 2)
    fin = member_financials(pa, None)
    prem = fin.annual_premium if fin else None
    return round(float(prem), 2) if isinstance(prem, (int, float)) else None


def _composition_amounts(pa: dict[str, Any]) -> dict[str, float]:
    """Standalone family amounts from a dependants-sheet row's SO/CO/FO/SC
    ``rate_tiers`` (the rate IS the dependant premium, not an increment)."""
    tiers = pa.get("rate_tiers") if isinstance(pa, dict) else None
    out: dict[str, float] = {}
    if not isinstance(tiers, dict):
        return out
    for label, cell in tiers.items():
        role = _COMPOSITION_ROLE.get(str(label).upper())
        rate = _tier_rate(cell)
        if role and rate is not None and rate > 0:
            out.setdefault(role, rate)
    return out


def dependant_option_overlay(
    db: Session, policy_year_id: str
) -> dict[str, dict[str, dict[str, Any]]]:
    """``{product_id: {employee_tier_key: row}}`` derived from dependant-scope
    categories, where a row is ``{"options": {role: spec}}`` (per-dependant
    option pricing linked to the tier), ``{"choices": {role: [choice]}}``
    (freestanding option LEVELS the member elects — see rule 4), or standalone
    family amounts ``{spouse/child/both: amt}`` (dependants-sheet composition
    rows). Merged UNDER the employee-tier slip data by ``_merge_family_overlay``
    (an employee tier's own EO/ES/EC/EF table wins)."""
    cats = list(
        db.execute(
            select(Category).where(
                Category.policy_year_id == policy_year_id,
                Category.product_id.is_not(None),
            )
        ).scalars()
    )
    dep_cats = [
        c for c in cats
        if isinstance(c.plan_assignments, dict)
        and c.plan_assignments.get("member_scope") == "dependant"
    ]
    if not dep_cats:
        return {}
    dep_ids = {c.id for c in dep_cats}
    emp_cats = [c for c in cats if c.id not in dep_ids]

    out: dict[str, dict[str, dict[str, Any]]] = {}

    def _add(pid: str, key: str, kind: str, payload: dict[str, Any]) -> None:
        row = out.setdefault(pid, {}).setdefault(key, {})
        if kind == "options":
            row.setdefault("options", {})
            for role, spec in payload.items():
                row["options"].setdefault(role, spec)
        elif kind == "choices":
            row.setdefault("choices", {})
            for role, choice_list in payload.items():
                row["choices"].setdefault(role, choice_list)
        else:  # standalone family amounts
            for role, amt in payload.items():
                row.setdefault(role, amt)

    for pid in {c.product_id for c in dep_cats}:
        p_deps = [c for c in dep_cats if c.product_id == pid]
        p_emps = [c for c in emp_cats if c.product_id == pid]
        emp_keys_all = [
            tier_key(c.id, (c.plan_assignments or {}).get("plan_code")) for c in p_emps
        ]
        # Role rows (Spouse/Child) grouped for the sole-row rule.
        role_rows: dict[str, list[Category]] = {}
        for dc in p_deps:
            pa = dc.plan_assignments or {}
            comp = _composition_amounts(pa)
            if comp:
                # Rule 2: composition row → same-plan employee tiers.
                dep_plan = str(pa.get("plan_code") or "")
                for ec in p_emps:
                    if str((ec.plan_assignments or {}).get("plan_code") or "") == dep_plan:
                        _add(pid, tier_key(ec.id, dep_plan), "family", comp)
                continue
            role = dependant_option_role(dc.raw_description)
            if role is None:
                continue
            spec = _dependant_option_spec(pa)
            if spec is None:
                continue
            marker = _option_marker(dc.raw_description)
            if marker:
                # Rule 1: marker match against employee tier categories.
                for ec in p_emps:
                    if _option_marker(ec.raw_description) == marker:
                        _add(
                            pid,
                            tier_key(ec.id, (ec.plan_assignments or {}).get("plan_code")),
                            "options",
                            {role: spec},
                        )
            else:
                role_rows.setdefault(role, []).append(dc)
        # Rule 3: a SOLE unmarked row per role applies to every employee tier.
        # Rule 4: MULTIPLE unmarked rows per role are freestanding option LEVELS
        # — attached to every employee tier as electable choices; the elected
        # level (``dependant_option_ids``) selects which row prices each
        # covered dependant.
        for role, rows in role_rows.items():
            if len(rows) == 1:
                spec = _dependant_option_spec(rows[0].plan_assignments or {})
                if spec is None:
                    continue
                for key in emp_keys_all:
                    _add(pid, key, "options", {role: spec})
                continue
            choices = []
            for dc in rows:
                pa = dc.plan_assignments or {}
                spec = _dependant_option_spec(pa)
                if spec is None:
                    continue
                cover = basis_amount(pa)
                si = pa.get("sum_insured")
                if cover is None and isinstance(si, (int, float)):
                    cover = float(si)
                choices.append({
                    "category_id": dc.id,
                    "label": (dc.raw_description or "").strip() or role.title(),
                    "sum_insured": cover,
                    "spec": spec,
                })
            if not choices:
                continue
            # Stable, meaningful order: ascending cover amount.
            choices.sort(key=lambda c: (c["sum_insured"] is None, c["sum_insured"] or 0.0))
            for key in emp_keys_all:
                _add(pid, key, "choices", {role: choices})
    return out


def _merge_family_overlay(
    base: dict[str, dict[str, dict[str, Any]]], overlay: dict[str, dict[str, dict]]
) -> dict[str, dict[str, dict[str, Any]]]:
    """Merge dependant-option rows UNDER the tier-derived index: an employee
    tier that already carries slip dependant pricing (its own EO/ES/EC/EF
    table or Dependents rate) keeps it; option rows fill the gaps."""
    for pid, rows in overlay.items():
        target = base.setdefault(pid, {})
        for key, row in rows.items():
            target.setdefault(key, row)
    return base


def _slip_dependant_shape(
    family_slip_idx: dict[str, Any] | None, product_id: str, key: str
) -> str | None:
    """The slip's dependant SHAPE for ONE tier — ``per_pax`` (a per-dependant rate),
    ``family_group`` (an EO/ES/EC/EF table), or None when the slip carries no
    dependant pricing for the tier. Tier-scoped (with the same plan-code fallback as
    the resolvers) so a product whose tiers mix the two formats classifies each tier
    by its own slip data rather than one mode for the whole product."""
    row = _tier_row((family_slip_idx or {}).get(product_id), key)
    if not isinstance(row, dict) or not row:
        return None
    if "options" in row or "choices" in row:
        return DependantMode.slip_options
    return DependantMode.per_pax if "per_pax" in row else DependantMode.family_group


def per_pax_slip_rate(
    family_slip_idx: dict[str, Any] | None, product_id: str, key: str
) -> float | None:
    """The slip-derived per-dependant rate for a (product, tier) — None when unset.
    Mirrors ``family_slip_incr``'s plan-code fallback."""
    row = _tier_row((family_slip_idx or {}).get(product_id), key)
    rate = row.get("per_pax") if isinstance(row, dict) else None
    return float(rate) if isinstance(rate, (int, float)) else None


def family_slip_incr(
    family_slip_idx: dict[str, Any] | None, product_id: str, key: str, role: str
) -> float | None:
    """The slip-derived incremental dependant amount for a (product, tier, role).

    Mirrors ``slip_premium_for``'s plan-code fallback so a tier whose category half
    differs from the configured key still resolves on an unambiguous plan_code."""
    incr = _tier_row((family_slip_idx or {}).get(product_id), key)
    amt = incr.get(role) if isinstance(incr, dict) else None
    return float(amt) if isinstance(amt, (int, float)) else None


def _tier_subdict(
    by_key: dict[str, Any] | None, tier_category_id: str | None, plan_code: str | None
) -> dict[str, Any] | None:
    """A per-tier sub-config row. ``family_tags`` and ``per_pax`` are keyed by
    ``tier_key`` (the dependant amount differs per plan, like the price tag), so
    resolve the exact key then an unambiguous plan_code — mirroring the matrix +
    slip fallback so a drifted category half still resolves."""
    row = _tier_row(by_key, tier_key(tier_category_id, plan_code))
    return row if isinstance(row, dict) else None


def _per_pax_flat(
    pricing: dict[str, Any] | None,
    product_id: str,
    tier_category_id: str | None,
    plan_code: str | None,
) -> float | None:
    """The manual flat per-dependant rate for a (product, tier) — None when unset."""
    row = _tier_subdict(
        _dependant_block(pricing, product_id).get("per_pax"),
        tier_category_id, plan_code,
    )
    flat = row.get("flat") if isinstance(row, dict) else None
    return float(flat) if isinstance(flat, (int, float)) else None


def _per_pax_rate(
    source: str,
    pricing: dict[str, Any] | None,
    family_slip_idx: dict[str, Any] | None,
    product_id: str,
    tier_category_id: str | None,
    plan_code: str | None,
) -> float | None:
    """The effective per-dependant rate for a (product, tier): a manual ``per_pax``
    matrix value is a sparse OVERRIDE that wins (correcting a wrong slip rate);
    otherwise the "slip" source falls back to the slip's Dependents rate."""
    manual = _per_pax_flat(pricing, product_id, tier_category_id, plan_code)
    if manual is not None:
        return manual
    if source == FlexPriceSource.slip:
        return per_pax_slip_rate(
            family_slip_idx, product_id, tier_key(tier_category_id, plan_code)
        )
    return None


def dependant_option_choices(
    family_slip_idx: dict[str, Any] | None, product_id: str, key: str
) -> dict[str, list[dict[str, Any]]]:
    """The freestanding dependant option LEVELS for a (product, tier):
    ``{role: [{category_id, label, sum_insured, spec}]}`` (rule 4 of
    ``dependant_option_overlay``). ``{}`` when the tier has none — its dependant
    pricing is either linked (``options``) or absent.

    Returns per-choice COPIES: the overlay aliases one list across every tier
    key, so handing callers the internal dicts would let a single mutation
    corrupt the shared index for the product's whole tier set."""
    row = _tier_row((family_slip_idx or {}).get(product_id), key)
    choices = row.get("choices") if isinstance(row, dict) else None
    if not isinstance(choices, dict):
        return {}
    return {role: [dict(c) for c in rows] for role, rows in choices.items()}


def _chosen_option_spec(
    choices: dict[str, list[dict[str, Any]]], role: str, dep_option_ids: dict | None
) -> object | None:
    """The elected level's price spec for one role — None when no level is
    chosen or the chosen id no longer matches a choice (re-parse drift)."""
    chosen = (dep_option_ids or {}).get(role)
    if not chosen:
        return None
    for c in choices.get(role, []):
        if c.get("category_id") == chosen:
            return c.get("spec")
    return None


def option_amount(spec: object, age: int | None) -> float | None:
    """Price one covered dependant from an option spec: a flat amount, or an
    age-banded ``{basis, voluntary_rates}`` resolved on the DEPENDANT's age
    (unknown age → None, unpriced — never guess a band)."""
    if isinstance(spec, bool):
        return None
    if isinstance(spec, (int, float)):
        return float(spec)
    if isinstance(spec, dict):
        basis = spec.get("basis")
        rate = voluntary_rate_for_age(spec.get("voluntary_rates"), age)
        if isinstance(basis, (int, float)) and rate is not None:
            return round(basis / 1000.0 * rate, 2)
    return None


def dependant_tag(
    *,
    source: str,
    pricing: dict[str, Any] | None,
    family_slip_idx: dict[str, Any] | None,
    product_id: str,
    tier_category_id: str | None,
    plan_code: str | None,
    spouse_count: int,
    child_count: int,
    dep_profiles: list[tuple[str, int | None]] | None = None,
    dep_option_ids: dict[str, Any] | None = None,
) -> float | None:
    """The flex amount drawn down for a member's covered dependants on one product.

    ``family_group`` maps the member's composition to a tier increment (the slip
    rate table when the source is "slip", else the manual ``family_tags``);
    ``per_pax`` multiplies the per-dependant rate (the slip's Dependents rate, else
    the manual matrix) by the covered head count; ``slip_options`` prices EACH
    covered dependant from the option row that sticks to the elected employee
    plan (``dep_profiles`` supplies the per-dependant role+age — age-banded rows
    resolve on the dependant's age; without profiles, ages are unknown). When the
    slip's option levels are freestanding (rule-4 ``choices``), the elected level
    per role — ``dep_option_ids`` ``{role: category_id}`` — selects the row; a
    covered dependant with no elected level is unpriced. Employee-Only costs $0;
    a product with no dependant pricing (effective mode ``none``) returns None.
    The mode is resolved with the slip default applied (see
    ``_effective_dependant_mode``)."""
    key = tier_key(tier_category_id, plan_code)
    mode = _effective_dependant_mode(
        pricing, product_id, source, family_slip_idx, key,
    )
    return _dependant_tag_for_mode(
        mode, source=source, pricing=pricing, family_slip_idx=family_slip_idx,
        product_id=product_id, tier_category_id=tier_category_id,
        plan_code=plan_code, spouse_count=spouse_count, child_count=child_count,
        dep_profiles=dep_profiles, dep_option_ids=dep_option_ids,
    )


def _dependant_tag_for_mode(
    mode: str,
    *,
    source: str,
    pricing: dict[str, Any] | None,
    family_slip_idx: dict[str, Any] | None,
    product_id: str,
    tier_category_id: str | None,
    plan_code: str | None,
    spouse_count: int,
    child_count: int,
    dep_profiles: list[tuple[str, int | None]] | None = None,
    dep_option_ids: dict[str, Any] | None = None,
) -> float | None:
    """``dependant_tag`` with the effective mode already resolved — for callers
    (``member_coverage_tag``, ``_member_flex_line``) that also need the mode for
    the combine rule, so it's computed once per line, not twice.

    Like ``_tier_charge``, this is the single output boundary where the product's
    GST gross-up applies to dependant amounts (all modes, all sources)."""
    amt = _dependant_tag_raw(
        mode, source=source, pricing=pricing, family_slip_idx=family_slip_idx,
        product_id=product_id, tier_category_id=tier_category_id,
        plan_code=plan_code, spouse_count=spouse_count, child_count=child_count,
        dep_profiles=dep_profiles, dep_option_ids=dep_option_ids,
    )
    return _gross(amt, gst_multiplier_for(pricing, product_id))


def _dependant_tag_raw(
    mode: str,
    *,
    source: str,
    pricing: dict[str, Any] | None,
    family_slip_idx: dict[str, Any] | None,
    product_id: str,
    tier_category_id: str | None,
    plan_code: str | None,
    spouse_count: int,
    child_count: int,
    dep_profiles: list[tuple[str, int | None]] | None = None,
    dep_option_ids: dict[str, Any] | None = None,
) -> float | None:
    """The GST-exclusive dependant tag (see ``_dependant_tag_for_mode``)."""
    key = tier_key(tier_category_id, plan_code)
    if mode == DependantMode.none:
        return None
    if mode == DependantMode.slip_options:
        row = _tier_row((family_slip_idx or {}).get(product_id), key)
        opts = row.get("options") if isinstance(row, dict) else None
        choices = dependant_option_choices(family_slip_idx, product_id, key)
        profiles = (
            dep_profiles
            if dep_profiles is not None
            else [("spouse", None)] * spouse_count + [("child", None)] * child_count
        )
        if not profiles:
            return 0.0  # Employee-Only — covered, no dependant cost
        total = 0.0
        for role, dep_age in profiles:
            spec = (opts or {}).get(role)
            if spec is None:
                spec = _chosen_option_spec(choices, role, dep_option_ids)
            amt = option_amount(spec, dep_age)
            if amt is None:
                # ANY unpriced dependant → the whole tag is unpriced (surfaced
                # by the unpriced-election guard), never a silent partial sum.
                return None
            total += amt
        return round(total, 2)
    if mode == DependantMode.per_pax:
        rate = _per_pax_rate(
            source, pricing, family_slip_idx, product_id, tier_category_id, plan_code
        )
        if rate is None:
            return None
        return round(rate * (spouse_count + child_count), 2)
    role = family_role(spouse_count, child_count)
    if role is None:
        return 0.0  # Employee-Only — covered, no dependant cost
    # A manual family_tags value is a sparse OVERRIDE that wins; otherwise the "slip"
    # source falls back to the slip's EO/ES/EC/EF increment for the role.
    row = _tier_subdict(
        _dependant_block(pricing, product_id).get("family_tags"),
        tier_category_id, plan_code,
    )
    amt = row.get(role) if isinstance(row, dict) else None
    if isinstance(amt, (int, float)):
        return round(float(amt), 2)
    if source == FlexPriceSource.slip:
        incr = family_slip_incr(
            family_slip_idx, product_id, tier_key(tier_category_id, plan_code), role
        )
        if incr is not None:
            return round(incr, 2)
    return None


def dependant_pricing_breakdown(
    *,
    pricing: dict[str, Any] | None,
    family_slip_idx: dict[str, Any] | None,
    source: str,
    product_id: str,
    tier_category_id: str | None,
    plan_code: str | None,
) -> dict[str, Any]:
    """A display-ready summary of a product's dependant pricing for ONE tier
    (the dependant amount differs per plan):
    ``{mode, scheme, family: [{role, amount}], per_pax_rate, choices}``. Reflects
    the slip default — an unconfigured product with slip family rates reads as
    family_group."""
    key = tier_key(tier_category_id, plan_code)
    mode = _effective_dependant_mode(
        pricing, product_id, source, family_slip_idx, key,
    )
    out: dict[str, Any] = {
        "mode": mode, "scheme": None, "family": [], "per_pax_rate": None,
        "choices": {},
    }
    gst_m = gst_multiplier_for(pricing, product_id)
    if mode == DependantMode.slip_options:
        # Per-dependant option pricing tied to the elected employee plan: emit
        # a per-role amount (None = age-banded, resolved per dependant at draw).
        row = _tier_row((family_slip_idx or {}).get(product_id), key)
        opts = row.get("options") if isinstance(row, dict) else None
        for role in ("spouse", "child"):
            spec = (opts or {}).get(role)
            if spec is None:
                continue
            out["family"].append(
                {"role": role, "amount": _gross(option_amount(spec, None), gst_m)}
            )
        # Freestanding option LEVELS (rule 4) — the member elects one per role.
        out["choices"] = dependant_option_choices(family_slip_idx, product_id, key)
    elif mode == DependantMode.per_pax:
        out["per_pax_rate"] = _gross(
            _per_pax_rate(
                source, pricing, family_slip_idx, product_id, tier_category_id, plan_code
            ),
            gst_m,
        )
    elif mode == DependantMode.family_group:
        out["scheme"] = (
            _dependant_block(pricing, product_id).get("scheme") or DEFAULT_FAMILY_SCHEME
        )
        for role, sc, cc in (("spouse", 1, 0), ("child", 0, 1), ("both", 1, 1)):
            out["family"].append({
                "role": role,
                "amount": dependant_tag(
                    source=source, pricing=pricing, family_slip_idx=family_slip_idx,
                    product_id=product_id, tier_category_id=tier_category_id,
                    plan_code=plan_code, spouse_count=sc, child_count=cc,
                ),
            })
    return out


def member_coverage_tag(
    *,
    source_map: dict[str, Any] | None,
    rule: str,
    pricing: dict[str, Any] | None,
    slip_idx: dict[str, Any] | None,
    family_slip_idx: dict[str, Any] | None,
    product_id: str,
    age: int | None,
    declined: bool,
    tier_category_id: str | None,
    plan_code: str | None,
    default_tier_category_id: str | None,
    default_plan: str | None,
    spouse_count: int,
    child_count: int,
    dep_profiles: list[tuple[str, int | None]] | None = None,
    dep_option_ids: dict[str, Any] | None = None,
    dependants_compulsory: bool = False,
) -> float | None:
    """Total flex drawn down for one product = employee plan tag (see
    ``member_price_tag``) + dependant tag (see ``dependant_tag``), combined by
    the single ``_combine_tags`` rule every snapshot/recompute surface shares.

    ``dep_profiles`` (per-dependant role+age) feeds ``slip_options`` pricing —
    pass it wherever the covered dependants are known so age-banded dependant
    option rows price on each dependant's own age. ``dep_option_ids``
    (``{role: category_id}``) is the elected freestanding option level per role
    (rule-4 choices). ``dependants_compulsory`` marks employer-funded dependant
    cover (participation compulsory): the dependants are covered but draw NO
    member flex, and their unpriceability never blocks the tag.

    Declined coverage (employee + dependants) costs no flex → None."""
    emp = member_price_tag(
        source_map=source_map, rule=rule, pricing=pricing, slip_idx=slip_idx,
        product_id=product_id, age=age, declined=declined,
        tier_category_id=tier_category_id, plan_code=plan_code,
        default_tier_category_id=default_tier_category_id, default_plan=default_plan,
    )
    if declined:
        return emp  # None — no coverage, no flex
    if dependants_compulsory:
        return _combine_tags(emp, None, 0, dep_applies=False)
    source = (source_map or {}).get(product_id, DEFAULT_FLEX_SOURCE)
    mode = _effective_dependant_mode(
        pricing, product_id, source, family_slip_idx,
        tier_key(tier_category_id, plan_code),
    )
    dep = _dependant_tag_for_mode(
        mode, source=source, pricing=pricing, family_slip_idx=family_slip_idx,
        product_id=product_id, tier_category_id=tier_category_id, plan_code=plan_code,
        spouse_count=spouse_count, child_count=child_count,
        dep_profiles=dep_profiles, dep_option_ids=dep_option_ids,
    )
    return _combine_tags(
        emp, dep, spouse_count + child_count, dep_applies=mode != DependantMode.none
    )


def _combine_tags(
    emp: float | None, dep: float | None, covered_count: int, *, dep_applies: bool
) -> float | None:
    """THE combine rule for a coverage line's flex tag — shared by the election
    snapshot (``member_coverage_tag``) and the benefit-statement recompute
    (``_member_flex_line``) so the two can never diverge.

    Covered dependants whose tag can't price — while dependant pricing applies —
    unprice the WHOLE tag (surfaced by the unpriced-election guard) rather than
    letting a priced employee component silently absorb $0 dependants. When
    neither component prices → None. Otherwise the components sum (an
    inapplicable one counts as $0), rounded to cents."""
    if dep is None and covered_count and dep_applies:
        return None
    if emp is None and dep is None:
        return None
    return round((emp or 0.0) + (dep or 0.0), 2)


def maybe_family_slip_index(
    db: Session, policy_year_id: str, source_map: dict[str, Any] | None
) -> dict[str, Any] | None:
    """Build the family slip index when any product resolves to the slip source
    (``_uses_slip``) — an unconfigured slip product defaults to family_group priced
    from the slip, so the index is needed to both decide the mode and price it. An
    explicit all-manual window skips it."""
    return family_slip_index(db, policy_year_id) if _uses_slip(source_map) else None


def window_flex_config(window) -> tuple[dict[str, str], str]:
    """``(source_map, drawdown_rule)`` for one window. A product absent from the map
    defaults to the slip source (``DEFAULT_FLEX_SOURCE``); the drawdown rule
    defaults to full."""
    src = window.flex_price_source if isinstance(window.flex_price_source, dict) else {}
    return src, (window.flex_drawdown_rule or FlexDrawdownRule.full)


def governing_flex_config(
    db: Session, policy_year_id: str
) -> tuple[dict[str, str], str]:
    """The flex config for window-agnostic surfaces (the benefit statement).

    The drawdown rule is a company-wide setting, so the most recent non-draft
    window for the year governs; with no window yet, returns ({}, full) — an empty
    source map means every product defaults to the slip source."""
    from app.models.enrollment_window import EnrollmentWindow, WindowStatus

    w = (
        db.execute(
            select(EnrollmentWindow)
            .where(
                EnrollmentWindow.policy_year_id == policy_year_id,
                EnrollmentWindow.status != WindowStatus.draft,
            )
            .order_by(EnrollmentWindow.opens_at.desc())
        )
        .scalars()
        .first()
    )
    return window_flex_config(w) if w is not None else ({}, FlexDrawdownRule.full)


def maybe_slip_index(
    db: Session, policy_year_id: str, source_map: dict[str, Any] | None
) -> dict[str, Any] | None:
    """Build the slip-premium index when any product resolves to the slip source
    (``_uses_slip`` — the default). Only an explicit all-manual window skips it."""
    return slip_premium_index(db, policy_year_id) if _uses_slip(source_map) else None


def maybe_slip_indices(
    db: Session, policy_year_id: str, source_map: dict[str, Any] | None
) -> tuple[dict[str, Any] | None, dict | None]:
    """``(slip_premium_idx, family_slip_idx)`` from a SINGLE ``list_product_tiers``
    load when any product uses the slip source, else ``(None, None)``. Use this on
    paths that need both (the benefit statement) so the expensive tier load runs
    once instead of once per index."""
    if not _uses_slip(source_map):
        return None, None
    from app.services.cohort_tiers import list_product_tiers

    tier_sets = list_product_tiers(db, policy_year_id)
    return (
        slip_premium_index_from(tier_sets),
        _merge_family_overlay(
            family_slip_index_from(tier_sets),
            dependant_option_overlay(db, policy_year_id),
        ),
    )


def employee_age(employee: Employee, ref: date) -> int | None:
    """Member's actual age (last birthday) as of ``ref``, from their DOB."""
    return age_from_attrs(employee.attribute_values, ref)


def reference_date(db: Session, policy_year_id: str) -> date:
    """The date used to compute ages — the policy year's start (today as fallback)."""
    py = db.get(PolicyYear, policy_year_id)
    return py.start_date if py and py.start_date else date.today()


@dataclass(frozen=True)
class FlexPriceLine:
    product_id: str
    product_code: str
    tier_key: str
    plan_code: str | None
    price_tag: float | None  # total = employee plan tag + dependant tag
    # The dependant portion of ``price_tag`` (covered spouse/children), so the
    # statement can show "incl. $X dependants". None when the product has no
    # dependant pricing; 0.0 when configured but the member is Employee-Only.
    dependant_tag: float | None = None


@dataclass(frozen=True)
class FlexPriceSummary:
    wallet_amount: float | None
    currency: str | None
    age: int | None
    age_known: bool
    total_price_tag: float
    balance: float | None  # wallet - coverage spend + leave impact (None when no wallet)
    lines: list[FlexPriceLine]
    # Effective buy/sell-leave trade folded into the balance (signed: buy spends,
    # sell credits). leave_flex_amount is None when no priced leave applies.
    leave_action: str | None = None
    leave_days: float | None = None
    leave_flex_amount: float | None = None


def _effective_leave(db: Session, employee: Employee):
    """The member's effective, priced buy/sell-leave trade for the year.

    Uses the shared ``latest_confirmed_leave`` selector (newest confirmed row) so
    the materialized balance and the revert path agree on which row is effective;
    an in-progress draft is shown live by the enrollment panel, not here. Returns
    the row only when it carries a snapshotted ``flex_amount``.
    """
    from app.models.leave_election import LeaveAction
    from app.services.leave_pricing_resolver import latest_confirmed_leave

    row = latest_confirmed_leave(db, employee)
    if row is None or row.action == LeaveAction.none or row.flex_amount is None:
        return None
    return row


def summarize_employee(db: Session, employee: Employee) -> FlexPriceSummary | None:
    """Resolve the member's effective coverage + leave → wallet balance.

    Effective tier per product = their override if present, else the matched
    cohort default; available flex = wallet - coverage spend + leave impact (buy
    spends, sell credits). Price tags come from the placement slip by default (per
    the governing window's config), so a member is priced even with no manual matrix
    configured. Returns None only when the member has no flex wallet AND no priced
    leave AND no pricing matrix — i.e. flex doesn't apply to them.
    """
    pricing = get_pricing(db, employee.policy_year_id)
    leave = _effective_leave(db, employee)
    has_wallet = isinstance(employee.flex_wallet_amount, (int, float))
    if pricing is None and leave is None and not has_wallet:
        return None
    ref = reference_date(db, employee.policy_year_id)
    age = employee_age(employee, ref)

    # Company-wide flex config (source per product + drawdown rule) governs how each
    # line is priced. The slip indices are built whenever any product resolves to
    # the slip source (the default), so coverage prices from the slip with no matrix.
    source_map, rule = governing_flex_config(db, employee.policy_year_id)
    slip_idx, family_slip_idx = maybe_slip_indices(
        db, employee.policy_year_id, source_map
    )

    lines: list[FlexPriceLine] = []
    total = 0.0
    if pricing is not None or slip_idx or family_slip_idx:
        defaults = employee_category_defaults(db, employee)  # {product_id: (code, plan)}
        baseline_cat = _baseline_category_ids(db, employee)
        # Categories whose dependant cover is compulsory (auto-included, employer-
        # funded) — their dependants draw no member flex.
        compulsory_dep_cats = compulsory_dependant_category_ids(
            db, set(baseline_cat.values())
        )
        overrides = load_overrides(db, employee.policy_year_id, [employee.id])
        for product_id, (product_code, default_plan) in defaults.items():
            ov = overrides.get((employee.id, product_id))
            if ov is not None and ov.declined:
                continue  # declined coverage costs no flex
            base_cat = baseline_cat.get(product_id)
            line = _member_flex_line(
                db, pricing=pricing, source_map=source_map, rule=rule,
                slip_idx=slip_idx, family_slip_idx=family_slip_idx,
                product_id=product_id, product_code=product_code,
                default_plan=default_plan, base_cat=base_cat, ov=ov,
                age=age, ref=ref,
                dependants_compulsory=base_cat in compulsory_dep_cats,
            )
            if line.price_tag is not None:
                total += line.price_tag
            lines.append(line)

    leave_amount = leave.flex_amount if leave else None
    wallet = employee.flex_wallet_amount
    balance = (
        (wallet - total + (leave_amount or 0.0))
        if isinstance(wallet, (int, float))
        else None
    )
    return FlexPriceSummary(
        wallet_amount=wallet,
        currency=employee.flex_currency,
        age=age,
        age_known=age is not None,
        total_price_tag=total,
        balance=balance,
        lines=lines,
        leave_action=leave.action if leave else None,
        leave_days=leave.days if leave else None,
        leave_flex_amount=leave_amount,
    )


def _member_flex_line(
    db: Session,
    *,
    pricing: dict[str, Any] | None,
    source_map: dict[str, Any],
    rule: str,
    slip_idx: dict[str, Any] | None,
    family_slip_idx: dict[str, Any] | None,
    product_id: str,
    product_code: str,
    default_plan: str | None,
    base_cat: str | None,
    ov,
    age: int | None,
    ref: date | None,
    dependants_compulsory: bool,
) -> FlexPriceLine:
    """One product's effective-coverage flex line for the benefit statement:
    the member's override (or cohort default) priced through the SAME pieces as
    the election snapshot — ``member_price_tag`` + ``_dependant_tag_for_mode``
    + ``_combine_tags`` — so the statement recompute can't diverge from what
    ``member_coverage_tag`` stamped at election time."""
    if ov is not None:
        cat_id = ov.tier_category_id or base_cat
        plan_code = ov.plan_code or default_plan
        covered_ids = ov.covered_dependant_ids
        dep_option_ids = ov.dependant_option_ids
    else:
        cat_id = base_cat
        plan_code = default_plan
        covered_ids = None  # no override → Employee-Only default
        dep_option_ids = None
    key = tier_key(cat_id, plan_code)
    dep_profiles = covered_dependant_profiles(
        db, covered_ids,
        age_limits=dependant_age_limits(pricing, product_id), ref=ref,
    )
    spouse_count, child_count = profile_counts(dep_profiles)
    emp_tag = member_price_tag(
        source_map=source_map, rule=rule, pricing=pricing, slip_idx=slip_idx,
        product_id=product_id, age=age, declined=False,
        tier_category_id=cat_id, plan_code=plan_code,
        default_tier_category_id=base_cat, default_plan=default_plan,
    )
    src = (source_map or {}).get(product_id, DEFAULT_FLEX_SOURCE)
    # Compulsory dependant cover is part of the base premium → no flex draw,
    # even though the dependants are covered.
    if dependants_compulsory:
        dep_tag, dep_applies = None, False
    else:
        mode = _effective_dependant_mode(
            pricing, product_id, src, family_slip_idx, key
        )
        dep_tag = _dependant_tag_for_mode(
            mode, source=src, pricing=pricing, family_slip_idx=family_slip_idx,
            product_id=product_id, tier_category_id=cat_id, plan_code=plan_code,
            spouse_count=spouse_count, child_count=child_count,
            dep_profiles=dep_profiles, dep_option_ids=dep_option_ids,
        )
        dep_applies = mode != DependantMode.none
    return FlexPriceLine(
        product_id=product_id,
        product_code=product_code,
        tier_key=key,
        plan_code=plan_code,
        price_tag=_combine_tags(
            emp_tag, dep_tag, spouse_count + child_count, dep_applies=dep_applies
        ),
        dependant_tag=dep_tag,
    )


def compulsory_dependant_category_ids(db: Session, cat_ids: set[str]) -> set[str]:
    """Of the given categories, those whose dependant participation is compulsory.

    A compulsory dependant is auto-covered and employer-funded — it must NOT draw
    member flex (only a voluntary opt-in does). Mirrors the enrollment UI gate.
    """
    from app.models.category import Category

    if not cat_ids:
        return set()
    out: set[str] = set()
    for cid, detail in db.execute(
        select(Category.id, Category.participation_detail).where(Category.id.in_(cat_ids))
    ).all():
        if isinstance(detail, dict) and detail.get("dependant") == "compulsory":
            out.add(cid)
    return out


def _baseline_category_ids(db: Session, employee: Employee) -> dict[str, str]:
    """``{product_id: matched_category_id}`` — the member's baseline tier per product.

    Uses the shared ``first_category_per_product`` selection so this agrees with the
    bulk-update snapshot path (and any future caller); a member matched to more than
    one category for a product must resolve to the SAME one everywhere.
    """
    from app.models.category import Category

    cat_ids = [
        m["category_id"]
        for m in (employee.matched_categories or [])
        if m.get("category_id")
    ]
    if not cat_ids:
        return {}
    product_of = dict(
        db.execute(
            select(Category.id, Category.product_id).where(
                Category.id.in_(cat_ids), Category.product_id.is_not(None)
            )
        ).all()
    )
    return first_category_per_product(employee.matched_categories or [], product_of)
