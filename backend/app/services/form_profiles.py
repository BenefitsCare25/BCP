"""Product *form profiles* — per-product-family setup-form composition.

A product's guided setup form used to be one fixed, medical-shaped skeleton
(header → eligibility → plans → EO/ES/EC/EF tiers → Basis of Cover → Schedule
of Benefits → arrangements). That fits hospital/surgical products but is wrong
for life (sum-assured), accident (capital sum + scale), travel (per-trip
limits, no family tiers) and statutory (earnings-based) products.

A **form profile** declares which sections render and any profile-specific
fields. The profile is inferred from the product code and can be overridden per
product via ``product_metadata['form_profile']`` — so no schema migration is
needed and custom products stay configurable.

This module deliberately holds *structure only* (section ids + field labels),
never scheme values. It imports nothing from ``product_templates`` to keep the
dependency one-directional (templates import profiles, not vice-versa).
"""
from __future__ import annotations

from typing import Literal, cast

from app.services import product_registry

FormProfile = Literal[
    "tiered_medical",
    "outpatient",
    "dental",
    "sum_assured",
    "accident",
    "travel",
    "statutory",
]

DEFAULT_PROFILE: FormProfile = "tiered_medical"

# ── Basis-of-Cover + Rate models ────────────────────────────────────────────
# A product's *form profile* picks one of three Basis-of-Cover column shapes and
# one of three Rate shapes. These are the structural axes the slips actually use
# (see docs/DYNAMIC_PRODUCT_FORM_PLAN.md):
#   tiered      — EO/ES/EC/EF headcount columns (hospital/surgical).
#   per_member  — a single member count; rate per member (outpatient/dental).
#   sum_assured — Sum Insured + Basis columns; premium per S$1,000 SI (life).
BasisModel = Literal["tiered", "per_member", "sum_assured"]
# flat           — one annual policy premium covering everyone (GBT travel).
# earnings_based — premium = rate x estimated annual earnings (WICA/WICI statutory).
RateModel = Literal["tiered", "per_member", "per_1000_si", "flat", "earnings_based"]

DEFAULT_BASIS_MODEL: BasisModel = "tiered"
DEFAULT_RATE_MODEL: RateModel = "tiered"

PROFILE_BASIS_MODEL: dict[FormProfile, BasisModel] = {
    "tiered_medical": "tiered",
    "outpatient": "per_member",
    "dental": "per_member",
    "sum_assured": "sum_assured",
    "accident": "sum_assured",
    # Travel (GBT) and statutory (WICA) price at the policy / earnings level, not
    # per tier — their categories carry only headcount, like a per-member basis.
    "travel": "per_member",
    "statutory": "per_member",
}
PROFILE_RATE_MODEL: dict[FormProfile, RateModel] = {
    "tiered_medical": "tiered",
    "outpatient": "per_member",
    "dental": "per_member",
    "sum_assured": "per_1000_si",
    "accident": "per_1000_si",
    # GBT = one flat annual policy premium; WICA = rate x estimated annual earnings.
    "travel": "flat",
    "statutory": "earnings_based",
}

# ── Section ids the frontend knows how to render ────────────────────────────
# The old standalone Plans / Cover-details / Arrangements sections were folded
# into Basis of Cover + Schedule of Benefits, so their ids no longer exist here.
SECTION_HEADER = "header"
SECTION_ELIGIBILITY = "eligibility"
SECTION_RATE_TABLE = "rate_table"
SECTION_BASIS_OF_COVER = "basis_of_cover"
SECTION_SCHEDULE_OF_BENEFITS = "schedule_of_benefits"

# ── code → profile (catalog codes; compound slip codes resolve to these via
#    the product match, so the catalog code is what we see here). Derived from
#    the product registry — add new products there, not here. ────────────────
_CODE_PROFILE: dict[str, FormProfile] = cast(
    "dict[str, FormProfile]", product_registry.code_profile_map()
)

# ── Section composition (ordered) ───────────────────────────────────────────
# Every product family now shares the same five-section layout, mirroring the
# SME-scheme Excel sheets: Header → Eligibility → Basis of Cover → Rate →
# Schedule of Benefits. The old standalone Plans/Participation, Cover Details
# (profile fields) and Additional Arrangements sections were folded into these
# three: plans+participation live in Basis of Cover, cover details + benefit
# lines + arrangements live in Schedule of Benefits. The frontend renders the
# folded content from `template.profile_fields` / `plans` / `additional_arrangements`
# inside those sections, so it isn't a section id here.
_UNIFIED_SECTIONS: list[str] = [
    SECTION_HEADER,
    SECTION_ELIGIBILITY,
    SECTION_BASIS_OF_COVER,
    SECTION_RATE_TABLE,
    SECTION_SCHEDULE_OF_BENEFITS,
]
PROFILE_SECTIONS: dict[FormProfile, list[str]] = {
    "tiered_medical": list(_UNIFIED_SECTIONS),
    "outpatient": list(_UNIFIED_SECTIONS),
    "dental": list(_UNIFIED_SECTIONS),
    "sum_assured": list(_UNIFIED_SECTIONS),
    "accident": list(_UNIFIED_SECTIONS),
    "travel": list(_UNIFIED_SECTIONS),
    "statutory": list(_UNIFIED_SECTIONS),
}

# NOTE: there was once a `PROFILE_FIELD_SPECS` map here, rendered by the setup
# form as a "Cover details" block (Geographical Scope, Free Cover Limit, Scale
# of Compensation, …). It was removed because NOTHING ever read
# `answers["profile"]` — not confirm/materialize, not the placement-slip export,
# not the fact-find, not the benefit statement — so the values a broker typed
# went nowhere. Worse, every field duplicated somewhere that does work: the
# Schedule of Benefits rows, `Plan.annual_policy_limit`, or the product header's
# free-cover-limit. Don't reintroduce a parallel capture surface; add the field
# to the SOB or the product header instead.


def _coerce(value: str | None) -> FormProfile:
    return value if value in PROFILE_SECTIONS else DEFAULT_PROFILE  # type: ignore[return-value]


def infer_profile(code: str, override: str | None = None) -> FormProfile:
    """Resolve a product's form profile: a *valid* explicit override wins, else
    inferred from the code, else the default (tiered medical). An unrecognized
    override is ignored (falls through to inference) rather than silently
    forcing the medical default."""
    if override and override in PROFILE_SECTIONS:
        return override  # type: ignore[return-value]
    return _CODE_PROFILE.get((code or "").strip().upper(), DEFAULT_PROFILE)


def sections_for(profile: str) -> list[str]:
    return list(PROFILE_SECTIONS.get(_coerce(profile), PROFILE_SECTIONS[DEFAULT_PROFILE]))


def basis_model_for(profile: str) -> BasisModel:
    """Basis-of-Cover column shape for a profile (tiered / per_member / sum_assured)."""
    return PROFILE_BASIS_MODEL.get(_coerce(profile), DEFAULT_BASIS_MODEL)


def rate_model_for(profile: str) -> RateModel:
    """Rate-table shape for a profile (tiered / per_member / per_1000_si)."""
    return PROFILE_RATE_MODEL.get(_coerce(profile), DEFAULT_RATE_MODEL)
