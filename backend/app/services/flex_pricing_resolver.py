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

This module is the stable public entry point; the implementation lives in the
``app.services.flex_pricing`` package, split by responsibility:

- ``bag`` — dependant modes, family schemes, GST / dependant-age stamps, the
  pricing-bag loader and the age-band / tier-key lookups.
- ``validation`` — write-boundary shape check for a pricing bag.
- ``price_tags`` — the employee plan tag (matrix override, slip premium,
  drawdown rule).
- ``dependant_profiles`` — dependant roles, age eligibility, covered profiles and
  participation.
- ``slip_family`` — slip-derived dependant pricing and dependant option rows.
- ``dependant_tags`` — the dependant tag per mode and its display breakdown.
- ``coverage_tags`` — the combined employee + dependant coverage-line tag.
- ``context`` — window / year flex config, slip index builders, age reference.
- ``summary`` — a member's coverage + leave resolved into a wallet balance.
"""
from __future__ import annotations

from app.services.flex_pricing.bag import (
    DEFAULT_DEPENDANT_AGE_LIMITS,
    DEFAULT_FAMILY_SCHEME,
    DEFAULT_FLEX_SOURCE,
    FAMILY_ROLES,
    FAMILY_SCHEMES,
    DependantMode,
    DependantParticipation,
    _is_age,
    age_band_label,
    get_pricing,
    gst_multiplier_for,
    product_premium_multiplier,
    stamp_pricing,
)
from app.services.flex_pricing.context import (
    FlexYearContext,
    employee_age,
    flex_year_context,
    governing_flex_config,
    maybe_family_slip_index,
    maybe_slip_index,
    maybe_slip_indices,
    reference_date,
    window_flex_config,
)
from app.services.flex_pricing.coverage_tags import _combine_tags, member_coverage_tag
from app.services.flex_pricing.dependant_profiles import (
    _dependant_eligible,
    _dependant_role_age,
    active_dependant_profiles,
    compulsory_dependant_category_ids,
    covered_dependant_profiles,
    dependant_age_limits,
    dependant_profiles_by_id,
    dependant_profiles_of,
    effective_dependant_participation,
    family_role,
    profile_counts,
    role_age_eligible,
    scheme_dependant_age_limits,
)
from app.services.flex_pricing.dependant_tags import (
    _effective_dependant_mode,
    dependant_pricing_breakdown,
    dependant_tag,
    option_amount,
)
from app.services.flex_pricing.price_tags import (
    _per_member_slip_premium,
    configured_voluntary_rates,
    member_price_tag,
    price_tag_for,
    slip_premium_for,
    slip_premium_index,
    slip_premium_index_from,
)
from app.services.flex_pricing.slip_family import (
    _composition_amounts,
    _dependant_option_spec,
    _merge_family_overlay,
    _slip_dependant_for_tier,
    _slip_dependant_shape,
    dependant_option_choices,
    dependant_option_overlay,
    dependant_option_role,
    family_slip_incr,
    family_slip_index,
    family_slip_index_from,
    per_pax_slip_rate,
    slip_family_increments,
)
from app.services.flex_pricing.summary import (
    FlexPriceLine,
    FlexPriceSummary,
    summarize_employee,
)
from app.services.flex_pricing.validation import validate_pricing_shape

__all__ = [
    "DEFAULT_DEPENDANT_AGE_LIMITS",
    "DEFAULT_FAMILY_SCHEME",
    "DEFAULT_FLEX_SOURCE",
    "FAMILY_ROLES",
    "FAMILY_SCHEMES",
    "DependantMode",
    "DependantParticipation",
    "FlexPriceLine",
    "FlexPriceSummary",
    "FlexYearContext",
    "_combine_tags",
    "_composition_amounts",
    "_dependant_eligible",
    "_dependant_option_spec",
    "_dependant_role_age",
    "_effective_dependant_mode",
    "_is_age",
    "_merge_family_overlay",
    "_per_member_slip_premium",
    "_slip_dependant_for_tier",
    "_slip_dependant_shape",
    "active_dependant_profiles",
    "age_band_label",
    "compulsory_dependant_category_ids",
    "configured_voluntary_rates",
    "covered_dependant_profiles",
    "dependant_age_limits",
    "dependant_option_choices",
    "dependant_option_overlay",
    "dependant_option_role",
    "dependant_pricing_breakdown",
    "dependant_profiles_by_id",
    "dependant_profiles_of",
    "dependant_tag",
    "effective_dependant_participation",
    "employee_age",
    "family_role",
    "family_slip_incr",
    "family_slip_index",
    "family_slip_index_from",
    "flex_year_context",
    "get_pricing",
    "governing_flex_config",
    "gst_multiplier_for",
    "maybe_family_slip_index",
    "maybe_slip_index",
    "maybe_slip_indices",
    "member_coverage_tag",
    "member_price_tag",
    "option_amount",
    "per_pax_slip_rate",
    "price_tag_for",
    "product_premium_multiplier",
    "profile_counts",
    "reference_date",
    "role_age_eligible",
    "scheme_dependant_age_limits",
    "slip_family_increments",
    "slip_premium_for",
    "slip_premium_index",
    "slip_premium_index_from",
    "stamp_pricing",
    "summarize_employee",
    "validate_pricing_shape",
    "window_flex_config",
]
