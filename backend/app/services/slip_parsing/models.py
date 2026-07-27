"""Dataclasses for data extracted from a placement-slip workbook."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class PolicyHeader:
    policyholder: str | None = None
    insurer: str | None = None
    period: str | None = None
    eligibility: str | None = None
    # Additional header / eligibility fields present on every slip — extracted so
    # the guided setup form pre-fills them instead of leaving them blank.
    insured: str | None = None
    business: str | None = None
    address: str | None = None
    policy_no: str | None = None
    admin_basis: str | None = None  # "Type of Administration"
    eligibility_date: str | None = None
    last_entry_age: str | None = None
    # Derived ages (normalised to a plain age-NEXT-birthday number, relative to
    # the renewal date). "next birthday"/"ANB" keep the stated age;
    # "last birthday"/"ALB" add one (age N ALB = age N+1 ANB).
    age_limit_no_underwriting: str | None = None  # from the Non-Evidence Limit row
    employee_age_limit: str | None = None  # "renewable up to age N" in Eligibility
    # Dollar amount from the Non-Evidence Limit row ("Sum insured exceeding
    # S$500,000 … requires underwriting" → 500000.0). Auto-fills the product's
    # free cover limit on upload; None when the sheet states no NEL.
    non_evidence_limit: float | None = None


@dataclass(frozen=True)
class ExtractedCategory:
    insured: str
    category: str
    participation: str
    plan_code: str
    source_row: int  # 1-indexed for spreadsheet UX
    # Financial data — populated from Basis-of-Cover and Rate sections.
    num_employees: int | None = None
    # Per-tier member split, when the slip's count column is divided by tier
    # ("* Number" spanning EO/ES/EC/EF). Canonical tier keys; ``num_employees``
    # is always the total across it, so readers that don't price per tier are
    # unaffected. None when the slip states a single undivided count.
    tier_counts: dict[str, int] | None = None
    basis: str | None = None
    sum_insured: float | None = None
    premium_rate: float | None = None
    annual_premium: float | None = None
    rate_basis: str | None = None  # per_1000_si|tiered|flat|annual_flat|earnings_based
    rate_tiers: dict[str, dict[str, float]] | None = None
    # Statutory (WICA): the estimated annual earnings the premium is rated on.
    # The premium = estimated_annual_earnings x premium_rate.
    estimated_annual_earnings: float | None = None
    # The per-member "with dependants" rate, when a flat per-member table lists a
    # separate Dependents row (e.g. GCGP "1 - Employees" / "1 - Dependents"). The
    # dependant flex increment is this minus ``premium_rate``.
    dependant_rate: float | None = None
    # Location qualifier from a scoped Participation cell ("Compulsory - SG
    # Office" → "SG Office"). Scoped categories stay distinct cohorts.
    location_scope: str | None = None
    # Who this category covers: "dependant" for dependant-scope rows (GPA
    # "Spouse (Option 1)", VDL's GHS - Dependants sheet); None/"employee"
    # otherwise. Dependant-scope categories feed dependant pricing, never the
    # employee tier fan-out.
    member_scope: str | None = None
    # Full text of an annotated premium cell whose amount was parsed out, e.g.
    # GBT's "$3,169.80 (Subject to Minimum Policy Premium of S$500)".
    premium_note: str | None = None


@dataclass(frozen=True)
class ExtractedLimit:
    """A qualifier row beneath a benefit value (e.g. "Maximum no. of days" ->
    "120 days"). Captured per plan column alongside the value it constrains."""

    label: str
    value: str | None = None


@dataclass(frozen=True)
class ExtractedSubItem:
    key: str
    name: str
    value: str | None = None
    # Footnote on this cell ("Include Implants", "Surgical schedule applies...").
    note: str | None = None
    limits: tuple[ExtractedLimit, ...] = ()


@dataclass(frozen=True)
class ExtractedBenefitItem:
    number: str
    name: str
    value: str | None = None
    # Footnote split off the value cell (e.g. the "* Bargainable employees:
    # 4 Bed Govt/Restr. Hospital" qualifier under Daily Room & Board).
    note: str | None = None
    limits: tuple[ExtractedLimit, ...] = ()
    sub_items: tuple[ExtractedSubItem, ...] = ()
    properties: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class ExtractedPlan:
    code: str
    display_name: str
    cover_description: str | None = None
    annual_policy_limit: str | None = None
    items: tuple[ExtractedBenefitItem, ...] = ()
    source_row: int = 0
    # The slip's VERBATIM Schedule-of-Benefits column header for this plan
    # ("PLAN 1/U01/U04/U06"). Set only for per-plan-column layouts — a
    # descriptive single-schedule sheet has no such header and leaves it None.
    #
    # It exists because a composite header names several plan codes at once and
    # `slip_reconcile` fans it out into one plan per code, rewriting
    # `display_name` to a synthetic "Plan U01". Without capturing the original
    # here, the broker-facing SOB column label can never show what the slip
    # actually said. `dataclasses.replace` carries it through the fan-out for
    # free, so every derived plan keeps pointing at the header it came from.
    source_label: str | None = None


@dataclass(frozen=True)
class ProductSlip:
    sheet: str
    product_code: str
    policy_header: PolicyHeader
    categories: tuple[ExtractedCategory, ...]
    plans: tuple[ExtractedPlan, ...] = ()
    # Stable signature of this product's SOB layout + the column->role mapping
    # actually used (detected, or a stored override). Drives template memory.
    sob_fingerprint: str | None = None
    sob_roles: dict[str, Any] | None = None
    # Age-banded voluntary rate table. Each band is {label, min, max, rate}
    # where rate is per S$1000 sum assured. () when the product prices
    # voluntary cover flat (GPA) or has no such table.
    voluntary_rates: tuple[dict[str, Any], ...] = ()
    # Canonical tier key → the label the slip actually used (e.g. {"SO":
    # "Spouse"} for a Hartree Spouse/Child rate header). Lets the UI show the
    # client's own vocabulary while persistence stays canonical.
    tier_labels: dict[str, str] | None = None
    # Registry classification at parse time: which layout family extracted the
    # sheet and whether the product code was recognized. Unknown codes are
    # surfaced downstream as needs_classification.
    layout_family: str | None = None
    registry_known: bool = True


@dataclass(frozen=True)
class PlacementSlip:
    client: str
    products: tuple[ProductSlip, ...]
    diagnostics: dict[str, Any] = field(default_factory=dict)
