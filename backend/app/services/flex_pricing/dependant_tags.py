"""The dependant flex tag: effective dependant mode per tier, the per-mode
amount (family group, per pax, slip options), and the display breakdown.
"""
from __future__ import annotations

from typing import Any

from app.models.enrollment_window import FlexPriceSource
from app.services.cohort_tiers import tier_key
from app.services.flex_pricing.bag import (
    DEFAULT_FAMILY_SCHEME,
    DependantMode,
    _dependant_block,
    _gross,
    _tier_row,
    gst_multiplier_for,
)
from app.services.flex_pricing.dependant_profiles import family_role
from app.services.flex_pricing.price_tags import configured_voluntary_rates
from app.services.flex_pricing.slip_family import (
    _slip_dependant_shape,
    dependant_option_choices,
    family_slip_incr,
    per_pax_slip_rate,
)
from app.services.plan_hydration import voluntary_rate_for_age

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
    dependant = _dependant_block(pricing, product_id)
    tier_modes = dependant.get("modes")
    raw = tier_modes.get(key) if isinstance(tier_modes, dict) else None
    # ``mode`` is the legacy product-wide setting. Keep it as a fallback so
    # existing drafts resolve unchanged while new edits can follow the slip's
    # actual per-tier shape.
    if raw is None:
        raw = dependant.get("mode")
    if isinstance(raw, str) and raw in (
        DependantMode.none,
        DependantMode.family_group,
        DependantMode.per_pax,
    ):
        return raw
    if source == FlexPriceSource.slip:
        shape = _slip_dependant_shape(family_slip_idx, product_id, key)
        if shape is not None:
            return shape
    return DependantMode.none


def _tier_subdict(
    by_key: dict[str, Any] | None, tier_category_id: str | None, plan_code: str | None
) -> dict[str, Any] | None:
    """A per-tier sub-config row. ``family_tags`` and ``per_pax`` are keyed by
    ``tier_key`` (the dependant amount differs per plan, like the price tag), so
    resolve the exact key then an unambiguous plan_code — mirroring the matrix +
    slip fallback so a drifted category half still resolves."""
    # Broker overrides are category-specific. Slip recommendations retain the
    # plan-code fallback in their own lookup helpers; manual changes cross
    # categories only through the editor's explicit copy action.
    row = (
        by_key.get(tier_key(tier_category_id, plan_code))
        if isinstance(by_key, dict)
        else None
    )
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


def _chosen_option_spec(
    choices: dict[str, list[dict[str, Any]]],
    role: str,
    dep_option_ids: dict[str, Any] | None,
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


def option_amount(
    spec: object,
    age: int | None,
    voluntary_rates: list[Any] | None = None,
) -> float | None:
    """Price one covered dependant from an option spec: a flat amount, or an
    age-banded ``{basis, voluntary_rates}`` resolved on the DEPENDANT's age
    (unknown age → None, unpriced — never guess a band). A saved product-level
    rate table replaces the extracted table for every voluntary employee and
    dependant option under that product."""
    if isinstance(spec, bool):
        return None
    if isinstance(spec, (int, float)):
        return float(spec)
    if isinstance(spec, dict):
        basis = spec.get("basis")
        rate = voluntary_rate_for_age(
            voluntary_rates if voluntary_rates is not None else spec.get("voluntary_rates"),
            age,
        )
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
        rate_override = configured_voluntary_rates(pricing, product_id, key)
        total = 0.0
        for role, dep_age in profiles:
            spec = (opts or {}).get(role)
            if spec is None:
                spec = _chosen_option_spec(choices, role, dep_option_ids)
            amt = option_amount(spec, dep_age, rate_override)
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
    family_status_role = family_role(spouse_count, child_count)
    if family_status_role is None:
        return 0.0  # Employee-Only — covered, no dependant cost
    # A manual family_tags value is a sparse OVERRIDE that wins; otherwise the "slip"
    # source falls back to the slip's EO/ES/EC/EF increment for the role.
    row = _tier_subdict(
        _dependant_block(pricing, product_id).get("family_tags"),
        tier_category_id, plan_code,
    )
    amt = row.get(family_status_role) if isinstance(row, dict) else None
    if isinstance(amt, (int, float)):
        return round(float(amt), 2)
    if source == FlexPriceSource.slip:
        incr = family_slip_incr(
            family_slip_idx,
            product_id,
            tier_key(tier_category_id, plan_code),
            family_status_role,
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
