"""Pydantic request/response models for the API."""

from __future__ import annotations

import json
from datetime import date, datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, computed_field, model_validator

ParticipationModelStr = Literal["standard", "extended", "eo_only"]
InsuranceLineStr = Literal["medical", "general", "life", "flex"]
LayoutFamilyStr = Literal["si_based", "plan_tier", "travel", "named_person", "earnings"]
MAX_CLAIM_WINDOW_DAYS = 3650


class _Base(BaseModel):
    model_config = ConfigDict(from_attributes=True)


# ── Schemas ──────────────────────────────────────────────────────────────────


class AttributeSchemaOut(_Base):
    id: str
    client_id: str | None
    attribute_id: str
    display_name: str
    data_type: str
    enum_values: list[str] | None = None
    is_required: bool
    is_pii: bool
    # Defaults keep internal/programmatic schema construction compatible while
    # taking the privacy-safe position for external AI value sharing.
    allow_matching: bool = True
    allow_ai_values: bool = False
    description: str | None = None
    derived_from: str | None = None
    derivation_rule: dict[str, Any] | None = None


class AttributeSchemaCreate(BaseModel):
    attribute_id: str
    display_name: str
    data_type: str
    enum_values: list[str] | None = None
    is_required: bool = False
    is_pii: bool = False
    allow_matching: bool = True
    allow_ai_values: bool = False
    description: str | None = None


class AttributeSchemaPatch(BaseModel):
    display_name: str | None = None
    data_type: str | None = None
    enum_values: list[str] | None = None
    is_required: bool | None = None
    is_pii: bool | None = None
    allow_matching: bool | None = None
    allow_ai_values: bool | None = None
    description: str | None = None
    derived_from: str | None = None
    derivation_rule: dict[str, Any] | None = None


# ── Slip-driven config recommendations (attributes + products) ───────────────


class DerivationSample(BaseModel):
    input: str
    output: Any


class AttributeRecommendation(BaseModel):
    """A recommended employee attribute inferred from the slip's categories.

    When a roster was available, derivation fields are populated by sampling the
    roster columns and validating the proposed rule against real values.
    """

    attribute_id: str
    display_name: str
    data_type: str
    enum_values: list[str] | None = None
    is_pii: bool = False
    description: str | None = None
    reasoning: str = ""
    already_exists: bool = False
    # Derivation (only present when a roster was sampled and a column mapped).
    derived_from: str | None = None
    derivation_rule: dict[str, Any] | None = None
    valid: bool = False
    match_count: int = 0
    sample_size: int = 0
    samples: list[DerivationSample] = Field(default_factory=list)
    warning: str | None = None


class ProductRecommendation(BaseModel):
    code: str
    display_name: str
    # No `insurer`: the apply can't store one (the catalog has no such field any
    # more) and the slip's own answer already reaches the year's setup through
    # slip_to_setup, so reporting it here only advertised a discarded value.
    participation_model: ParticipationModelStr = "standard"
    has_dependants: bool = False
    is_outpatient: bool = False
    reasoning: str = ""
    already_exists: bool = False
    category_count: int = 0


class ConfigRecommendationOut(BaseModel):
    policy_year_id: str
    roster_present: bool
    employee_count: int
    category_count: int
    attributes: list[AttributeRecommendation]
    products: list[ProductRecommendation]
    model: str | None = None
    cache_hit: bool = False


class ApplyAttributeItem(BaseModel):
    attribute_id: str
    display_name: str
    data_type: str
    enum_values: list[str] | None = None
    is_pii: bool = False
    description: str | None = None
    derived_from: str | None = None
    derivation_rule: dict[str, Any] | None = None


class ApplyProductItem(BaseModel):
    code: str
    display_name: str
    # No `insurer` — a suggestion may report the insurer the slip named, but the
    # catalog never stores it (services/product_insurer.py).
    participation_model: ParticipationModelStr = "standard"
    has_dependants: bool = False
    is_outpatient: bool = False


class ApplyConfigRequest(BaseModel):
    attributes: list[ApplyAttributeItem] = Field(default_factory=list)
    products: list[ApplyProductItem] = Field(default_factory=list)
    rerun_matching: bool = True


class ApplyConfigResult(BaseModel):
    attributes_created: list[str]
    attributes_updated: list[str]
    products_created: list[str]
    categories_relinked: int = 0
    rematched: bool = False
    employees_matched: int | None = None
    rules_validated: int = 0
    rules_proposed: int = 0
    rules_need_review: int = 0
    rules_unmapped: int = 0
    rules_not_applicable: int = 0
    rules_reused: int = 0


class ProductOut(_Base):
    id: str
    client_id: str | None
    code: str
    display_name: str
    # LEGACY, read-only: the insurer is a per-benefit-year placement fact and is
    # entered on Company & Benefits → Header & Policy (services/
    # product_insurer.py). This still reports what pre-existing catalog rows
    # carry — the fallback for a year whose setup has no answer — but nothing
    # writes it and the catalog UI no longer offers it.
    insurer: str | None = None
    participation_model: str
    has_dependants: bool
    is_outpatient: bool
    # Broker-facing Medical / General / Life / Flex grouping (computed from code +
    # product_metadata override; not a stored column).
    line: InsuranceLineStr = "medical"
    # Structural classification (computed from code + product_metadata
    # override) — drives the setup-form shape and slip extraction.
    form_profile: str | None = None
    layout_family: str | None = None
    # Display code used on insurer report columns when it differs from the
    # internal code (e.g. GCGP → "GOGP"). Rides product_metadata.
    report_code: str | None = None
    # Legal entities this product is written on — the matching gate for ALL its
    # categories. Empty = no restriction. Rides product_metadata; set from the
    # setup header's roster-anchored Entities picker.
    entities: list[str] = Field(default_factory=list)


class ProductCreate(BaseModel):
    code: str
    display_name: str
    # No `insurer`: it belongs to the placement, not the catalog — set it per
    # benefit year on Company & Benefits → Header & Policy.
    participation_model: ParticipationModelStr = "standard"
    has_dependants: bool = False
    is_outpatient: bool = False
    # Optional overrides persisted into product_metadata for custom products.
    line: InsuranceLineStr | None = None
    form_profile: str | None = None
    layout_family: LayoutFamilyStr | None = None
    report_code: str | None = None
    # Legal entities this product is written on — the matching gate for
    # ALL its categories. `[]` clears the restriction; omitted leaves it.
    entities: list[str] | None = None


class ProductPatch(BaseModel):
    code: str | None = None
    display_name: str | None = None
    # No `insurer` — see ProductCreate.
    participation_model: ParticipationModelStr | None = None
    has_dependants: bool | None = None
    is_outpatient: bool | None = None
    # Classification overrides persisted into product_metadata (the broker's
    # answer to a needs_classification upload diagnostic).
    line: InsuranceLineStr | None = None
    form_profile: str | None = None
    layout_family: LayoutFamilyStr | None = None
    report_code: str | None = None
    # Legal entities this product is written on — the matching gate for
    # ALL its categories. `[]` clears the restriction; omitted leaves it.
    entities: list[str] | None = None


class PolicyYearOut(_Base):
    id: str
    client_id: str
    year: int
    start_date: date
    end_date: date
    # Company-level coverage window derived from per-product periods: earliest
    # product start → latest product end. Falls back to start_date/end_date when
    # no products carry an override. Display-only; identity remains start_date.
    coverage_start: date
    coverage_end: date
    status: str
    # Days after the coverage period ends during which claims may still be
    # submitted. None = no submission deadline (system default).
    claim_grace_period_days: int | None = None
    # Days after a member's LAST DAY OF SERVICE that they keep portal access.
    # A different bound from the grace period above: that one is a property of
    # the YEAR, this one of the member. None = the system default
    # (`member_access.DEFAULT_LEAVER_ACCESS_DAYS`); 0 = access ends on the last
    # day. There is deliberately no "unlimited".
    leaver_access_days: int | None = None
    activated_at: datetime | None = None


class PolicyYearCreate(BaseModel):
    start_date: date
    end_date: date
    claim_grace_period_days: int | None = Field(
        default=None, ge=0, le=MAX_CLAIM_WINDOW_DAYS
    )
    leaver_access_days: int | None = Field(
        default=None, ge=0, le=MAX_CLAIM_WINDOW_DAYS
    )

    @model_validator(mode="after")
    def _check_range(self) -> PolicyYearCreate:
        if self.end_date < self.start_date:
            raise ValueError("end_date must be on or after start_date")
        return self


class PolicyYearUpdate(BaseModel):
    """Partial update of a benefit year (dates, grace period, leaver run-off).

    Only fields present in the request body are written (``model_fields_set``),
    so a grace-period-only update can't wipe the dates and vice versa.
    """

    start_date: date | None = None
    end_date: date | None = None
    claim_grace_period_days: int | None = Field(
        default=None, ge=0, le=MAX_CLAIM_WINDOW_DAYS
    )
    leaver_access_days: int | None = Field(
        default=None, ge=0, le=MAX_CLAIM_WINDOW_DAYS
    )

    @model_validator(mode="after")
    def _check(self) -> PolicyYearUpdate:
        if (
            self.start_date is not None
            and self.end_date is not None
            and self.end_date < self.start_date
        ):
            raise ValueError("end_date must be on or after start_date")
        return self


class PolicyYearCopyIn(BaseModel):
    """Create a new benefit year and clone a source year's configuration into it.

    Both deadline settings are OPTIONAL overrides: omitted, the source year's
    value carries over. `leaver_access_days` is accepted for the same reason
    `claim_grace_period_days` is — they are the same kind of per-year setting,
    and honouring one while silently dropping the other made a caller that set
    the run-off on copy compile clean and lose the value.
    """

    start_date: date
    end_date: date
    claim_grace_period_days: int | None = Field(
        default=None, ge=0, le=MAX_CLAIM_WINDOW_DAYS
    )
    leaver_access_days: int | None = Field(
        default=None, ge=0, le=MAX_CLAIM_WINDOW_DAYS
    )

    @model_validator(mode="after")
    def _check_range(self) -> PolicyYearCopyIn:
        if self.end_date < self.start_date:
            raise ValueError("end_date must be on or after start_date")
        return self


class PolicyYearCopyResult(BaseModel):
    policy_year: PolicyYearOut
    copied: dict[str, int]


class PolicyYearDeletionImpact(BaseModel):
    deletable: bool
    reason: str | None = None
    counts: dict[str, int]
    operational_records: int


class PolicyYearReadinessOut(BaseModel):
    ready: bool
    metrics: dict[str, int]
    blockers: list[str]
    warnings: list[str]


class ProductTermOut(BaseModel):
    """A product's effective terms (coverage period + GST) within a policy year."""

    product_id: str
    code: str
    display_name: str
    coverage_start: date
    coverage_end: date
    # True when inheriting the policy year's span (no explicit dates stored —
    # the row may still exist for GST alone).
    is_default: bool
    # Medical / General / Life / Flex line, so coverage rows scope to their tab.
    line: InsuranceLineStr = "medical"
    # Tri-state GST opinion: None = inherit (flex-scheme default), True = gross,
    # False = explicit "no GST". Slip amounts are always GST-exclusive.
    gst_included: bool | None = None
    gst_rate: float | None = None
    # Free cover limit (underwriting): SI auto-accepted without medicals.
    free_cover_limit: float | None = None
    # NEL age (ANB): members at/above it require underwriting regardless of SI.
    nel_age_limit: int | None = None
    # Explicit setup for Medical and General products; defaults to No.
    underwriting_required: bool = False
    # Insurer-issued policy number for this product's placement.
    policy_number: str | None = None
    # Whether this product's claims draw on an inpatient benefit. SERVED from
    # `claim_intake.is_inpatient_product` (which reads the product registry —
    # the ONE place product-type knowledge lives) so the terms form can hide the
    # pre/post window on lines it cannot apply to without reimplementing the
    # taxonomy in TypeScript.
    is_inpatient: bool = False
    # Pre-/post-hospitalisation claim window, in days either side of the stay.
    # NULL = no rule (see the model) — never rendered as 0.
    pre_hosp_days: int | None = None
    post_hosp_days: int | None = None


class ProductTermUpdate(BaseModel):
    # Partial update: only the fields actually present in the request are applied
    # (the handler reads ``model_fields_set``), so a GST-only body doesn't touch
    # the coverage dates and a dates-only body doesn't touch GST. Dates still move
    # as a pair.
    coverage_start: date | None = None
    coverage_end: date | None = None
    gst_included: bool | None = None
    gst_rate: float | None = Field(default=None, ge=0, le=100)
    free_cover_limit: float | None = Field(default=None, ge=0)
    nel_age_limit: int | None = Field(default=None, ge=1, le=120)
    underwriting_required: bool = False
    policy_number: str | None = Field(default=None, max_length=64)
    # Bounded at a year: these are transcribed from policy wording, where the
    # figures are 30 to 180 days. An unbounded field invites a typo that silently
    # turns the window off (999 days passes everything) rather than erroring.
    pre_hosp_days: int | None = Field(default=None, ge=0, le=365)
    post_hosp_days: int | None = Field(default=None, ge=0, le=365)

    @model_validator(mode="after")
    def _check_range(self) -> ProductTermUpdate:
        if (self.coverage_start is None) != (self.coverage_end is None):
            raise ValueError("coverage_start and coverage_end must be set together")
        if (
            self.coverage_start is not None
            and self.coverage_end is not None
            and self.coverage_end < self.coverage_start
        ):
            raise ValueError("coverage_end must be on or after coverage_start")
        return self


# ── Categories ───────────────────────────────────────────────────────────────

ParticipationStr = Literal["compulsory", "voluntary"]


class CategoryOut(_Base):
    id: str
    policy_year_id: str
    product_id: str | None
    priority: int
    display_name: str
    raw_description: str
    matching_rule: dict[str, Any] | None
    rule_human_readable: str | None
    mapping_profile_id: str | None = None
    rule_status: str | None = None
    rule_validation: dict[str, Any] | None = None
    participation_model: ParticipationStr | None
    # {employee, dependant, direction} — the employee/dependant participation
    # split parsed from the slip. Edited separately in the employee + dependant
    # config cards; participation_model mirrors the employee clause.
    participation_detail: dict[str, Any] | None
    plan_assignments: dict[str, Any] | None
    source: str
    source_ref: str | None
    confidence: float | None
    status: str
    human_modified: bool
    modified_by: str | None
    created_at: datetime
    updated_at: datetime


class CategoryPatch(BaseModel):
    display_name: str | None = None
    matching_rule: dict[str, Any] | None = None
    rule_human_readable: str | None = None
    participation_model: ParticipationStr | None = None
    # Merge-patched as a whole dict by the card (employee or dependant scope).
    participation_detail: dict[str, Any] | None = None
    plan_assignments: dict[str, Any] | None = None
    status: str | None = None
    confidence: float | None = Field(default=None, ge=0.0, le=1.0)


class CategoryCreate(BaseModel):
    """Create a new eligibility category (the cards' '+ Add category').

    The matching rule is derived from the display name; the broker refines it
    afterwards via the rule editor. ``product_id`` scopes it to a product tab.
    """

    policy_year_id: str
    product_id: str | None = None
    display_name: str = Field(min_length=1, max_length=512)
    participation_model: ParticipationStr | None = None
    plan_assignments: dict[str, Any] | None = None


class CategoryGrouped(BaseModel):
    product_code: str
    product_display_name: str
    product_id: str | None
    # Medical / General / Life / Flex line this product group belongs to (for tab routing).
    line: InsuranceLineStr = "medical"
    categories: list[CategoryOut]


class EligibilityMappingItemOut(BaseModel):
    category_id: str
    product_code: str | None = None
    display_name: str
    plan_code: str | None = None
    category_status: str
    rule_status: str
    source: str
    matching_rule: dict[str, Any] | None = None
    rule_human_readable: str | None = None
    confidence: float | None = None
    matched_count: int | None = None
    expected_count: int | None = None
    unresolved_clauses: list[str] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    reused: bool = False


class MissingCategoryPlanOut(BaseModel):
    plan_id: str
    product_id: str
    product_code: str
    product_display_name: str
    plan_code: str
    plan_display_name: str
    source_hint: str | None = None


class AICategoryCreate(BaseModel):
    """Create a missing plan category from broker-supplied eligibility wording.

    The wording is required because a benefit plan name or schedule does not
    reliably state who is eligible; AI compiles evidence, it does not decide
    coverage policy.
    """

    plan_id: str = Field(min_length=1, max_length=36)
    eligibility_description: str = Field(min_length=3, max_length=2048)
    display_name: str | None = Field(default=None, max_length=512)
    participation_model: ParticipationStr | None = None


class EligibilityMappingSummaryOut(BaseModel):
    policy_year_id: str
    employee_count: int
    total: int
    validated: int
    proposed: int
    needs_review: int
    unmapped: int
    not_applicable: int = 0
    reused: int
    categories: list[EligibilityMappingItemOut] = Field(default_factory=list)
    missing_categories: int = 0
    missing_category_plans: list[MissingCategoryPlanOut] = Field(default_factory=list)


class ProductDiagnostic(BaseModel):
    """Per-product summary of how a sheet parsed + was reconciled (for the
    post-upload confidence/issues banner and the review queue)."""

    sheet: str
    product_code: str
    layout: str  # "per_plan" | "descriptive" | "none"
    rate_model: str | None = None
    n_categories: int
    n_plans: int
    n_benefit_items: int = 0  # total Schedule-of-Benefits lines parsed
    confidence: float
    reconciliation: str  # consistent | fan_out | assign_default | unmappable | no_plans
    issues: list[str] = Field(default_factory=list)
    low_confidence: bool = False
    needs_attention: bool = False
    empty_sob: bool = False  # plans parsed but SOB empty
    used_ai: bool = False
    # Template-memory: fingerprint + the column->role mapping used, so the UI can
    # show and correct it.
    fingerprint: str | None = None
    column_roles: dict[str, Any] | None = None
    # Registry classification of the sheet's product code. When the code is
    # unknown (no registry entry, no stored broker classification), the UI
    # offers a "classify this product" action instead of trusting the generic
    # default profile.
    layout_family: str | None = None
    registry_known: bool = True
    needs_classification: bool = False


class ParseResult(BaseModel):
    placement_slip_id: str
    policy_year_id: str
    total_categories: int
    high_confidence: int
    needs_review: int
    # Prior unreviewed auto-generated categories cleared before this parse
    # (re-upload supersedes its own previous run instead of stacking).
    replaced_categories: int = 0
    # Same, for auto-generated plans (Schedule of Benefits) — prevents stale
    # plans being orphaned when a re-parse yields different plan codes.
    replaced_plans: int = 0
    skipped_sheets: list[dict[str, Any]]
    # Product codes whose guided setup form was pre-filled from this slip.
    prefilled_setups: list[str] = []
    # Per-product parse/reconciliation diagnostics (confidence + issues).
    products: list[ProductDiagnostic] = Field(default_factory=list)
    # Employees were auto re-matched against the fresh categories (a re-parse
    # replaces category rows, which orphans prior matches otherwise).
    rematched: bool = False
    employees_matched: int | None = None
    # Company-aware matching-rule compiler outcome. Kept separate from the
    # parser's legacy regex confidence so partial keyword parses are never
    # presented as ready for employee matching.
    rules_validated: int = 0
    rules_proposed: int = 0
    rules_need_review: int = 0
    rules_unmapped: int = 0
    rules_not_applicable: int = 0
    rules_reused: int = 0


class SlipTemplateProfileSave(BaseModel):
    """A broker's correction of a template's SOB column->role mapping. Stored
    keyed by ``fingerprint`` and reused on later uploads of the same template."""

    fingerprint: str = Field(min_length=1, max_length=64)
    product_code: str = Field(min_length=1, max_length=64)
    insurer: str | None = Field(default=None, max_length=128)
    sheet_label: str | None = Field(default=None, max_length=255)
    # {name_col, key_col, value_col, allow_letter_keys, name_first} — extra keys
    # are dropped server-side.
    roles: dict[str, Any]


class SlipTemplateProfileOut(BaseModel):
    id: str
    fingerprint: str
    product_code: str
    insurer: str | None = None
    sheet_label: str | None = None
    roles: dict[str, Any]


# ── Employees / Dependants ───────────────────────────────────────────────────


class VoluntaryRateBand(BaseModel):
    label: str
    min: int | None = None
    max: int | None = None
    rate: float  # per S$1000 sum assured


class ProductVoluntaryRatesOut(BaseModel):
    """The product-wide voluntary age-band rate table (shared by all the
    product's voluntary plans) + how many voluntary plans it applies to."""

    product_id: str
    bands: list[VoluntaryRateBand]
    voluntary_plan_count: int


class ProductVoluntaryRatesIn(BaseModel):
    bands: list[VoluntaryRateBand]


class PlanFinancials(BaseModel):
    num_employees: int | None = None
    basis: str | None = None
    sum_insured: float | None = None
    premium_rate: float | None = None
    annual_premium: float | None = None
    rate_basis: str | None = None
    rate_tiers: dict[str, dict[str, float]] | None = None
    # Per-member "with dependants" rate from a flat slip table's Dependents row
    # (e.g. GCGP). The flat dependant flex increment is this minus premium_rate.
    dependant_rate: float | None = None
    # Statutory (WICA): the estimated annual earnings the premium is rated on
    # (premium = estimated_annual_earnings x premium_rate).
    estimated_annual_earnings: float | None = None
    # Age-banded voluntary rate table (life products: GTL/GCI). Present on a
    # voluntary tier so the UI can show the rate-by-age table + a live per-member
    # preview, and the slip price tag prices off the member's age band.
    voluntary_rates: list[VoluntaryRateBand] | None = None
    # True when the premium figures above have been grossed up by the product's
    # GST rate (raw slip amounts are GST-exclusive) — so the UI can badge them.
    gst_included: bool = False


class MatchedPlan(BaseModel):
    product_code: str
    product_name: str | None = None
    category_id: str | None = None
    plan_code: str | None = None
    category_display: str | None = None
    method: str | None = None
    confidence: float | None = None
    financials: PlanFinancials | None = None
    benefit_schedule: dict[str, Any] | None = None
    cover_description: str | None = None
    annual_policy_limit: str | None = None
    # Set when an EmployeePlanOverride changed this employee's plan away from the
    # cohort (category) default — see services/coverage_resolver.
    plan_overridden: bool = False
    override_source: str | None = None
    # Dependant ids the member elected to cover for this product (override only).
    covered_dependant_ids: list[str] | None = None


class EmployeeOut(_Base):
    id: str
    staff_id: str
    employee_name: str | None
    attribute_values: dict[str, Any]
    derived_attribute_values: dict[str, Any]
    matched_category_id: str | None
    match_method: str | None
    match_confidence: float | None
    # "active" | "terminated" — terminated leavers are excluded from coverage.
    status: str = "active"
    matched_plans: list[MatchedPlan] = Field(default_factory=list)


class EmployeeList(BaseModel):
    total: int
    offset: int
    limit: int
    items: list[EmployeeOut]


class CoverageProduct(BaseModel):
    product_code: str
    product_name: str | None = None


class CoverageSummaryItem(BaseModel):
    id: str
    staff_id: str
    employee_name: str | None
    product_count: int
    products: list[CoverageProduct]
    #: Terminated on the roster. Only ever true when the caller asked for
    #: leavers, but served unconditionally so the row can say so.
    left: bool = False


class CoverageSummary(BaseModel):
    total: int
    items: list[CoverageSummaryItem]


class EmployeePatch(BaseModel):
    employee_name: str | None = None
    attribute_values: dict[str, Any] | None = None


class DependantOut(_Base):
    id: str
    employee_id: str | None
    attribute_values: dict[str, Any]
    link_method: str | None
    # "active" | "pending_approval" | "rejected" — portal self-adds start pending.
    status: str = "active"
    # Set on approval responses only: user-facing errors from the automatic
    # flex wallet re-assignment (empty = re-assign succeeded or wasn't needed).
    # The dependant itself was still approved — the broker must re-assign from
    # the Flex tab.
    flex_errors: list[str] = Field(default_factory=list)


# ── Benefit statement (read-only, benefits-only employee view) ────────────────


class StatementAttribute(BaseModel):
    """One labelled employee attribute that drove plan assignment."""

    key: str
    label: str
    value: str


class DependantSummary(BaseModel):
    id: str
    name: str | None = None
    relationship: str | None = None
    dob: str | None = None
    # "spouse" | "child" | None — the classification flex pricing keys on,
    # SERVED rather than re-derived client-side. The word lists that decide it
    # are subtle (a bare "step" matches "stepmother", so the backend excludes
    # it) and a client-side mirror of them had already drifted: a step-parent
    # classified as a child in the UI, was shown a confident child-rate price,
    # and then 409'd `unpriced_elections` on submit because the server could not
    # price them. `null` means "not spouse or child" — it is not a price of nil.
    role: str | None = None


class CoverageLine(BaseModel):
    """One product's resolved coverage for an employee.

    ``financials`` is the PER-MEMBER view (``plan_hydration.member_financials``):
    the member's own Amount Covered (basis) and premium — for a voluntary life
    tier, age-banded off the member's age, and reflecting any elected upgrade/
    downgrade — NOT the group sum-insured / total premium.
    """

    product_code: str
    product_name: str | None = None
    category_id: str | None = None
    category_display: str | None = None
    match_method: str | None = None
    match_confidence: float | None = None
    rule_human_readable: str | None = None
    plan_code: str | None = None
    cover_description: str | None = None
    annual_policy_limit: str | None = None
    benefit_schedule: dict[str, Any] | None = None
    financials: PlanFinancials | None = None
    covers_dependants: bool = False
    covered_dependants: list[DependantSummary] = Field(default_factory=list)


class StatementEmployee(BaseModel):
    id: str
    staff_id: str
    employee_name: str | None = None


class FlexBenefitCategoryLine(BaseModel):
    """One claimable item under the employee's Flex wallet."""

    name: str
    claimable: bool = True
    sub_limit: float | None = None
    note: str | None = None


class FlexPriceTagLine(BaseModel):
    """One product's flex price tag (wallet spend) for the member's coverage."""

    product_code: str
    plan_code: str | None = None
    price_tag: float | None = None
    # The dependant portion of ``price_tag`` (covered spouse/children). None when
    # the product has no dependant pricing; 0.0 when Employee-Only.
    dependant_tag: float | None = None


class FlexProrationLine(BaseModel):
    """How an annual flex allowance was scaled to one member's cover period.

    ``note`` is the printable fraction ("6/12 months") so every surface renders
    the same words — see ``services/flex_proration.describe``.
    """

    basis: str
    factor: float
    served: int
    total: int
    full_amount: float
    note: str
    period_start: str | None = None
    period_end: str | None = None


class FlexCoverageLine(BaseModel):
    """The employee's resolved Flexible-Benefits wallet — no premium figures.

    Sourced from the persisted ``Employee.flex_*`` snapshot (wallet amount,
    currency, tier, family status) and enriched with the claimable categories +
    cost-share from the matching scheme tier for display.
    """

    scheme_name: str | None = None
    tier_name: str | None = None
    family_status: str | None = None
    # The EFFECTIVE allowance — pro-rated to the period the member was covered
    # when the scheme says so. `proration` carries the derivation.
    wallet_amount: float | None = None
    # {basis, factor, served, total, full_amount, period_start, period_end} when
    # the allowance was pro-rated, else None. SERVED, never recomputed in the
    # client: the month count has no exact JS equivalent worth maintaining twice,
    # and a fraction that drifts from the figure beside it is silent.
    proration: FlexProrationLine | None = None
    currency: str | None = None
    # How the family status was resolved: "dependants" | "roster" | "none".
    source: str | None = None
    employer_pct: float | None = None
    employee_pct: float | None = None
    benefit_categories: list[FlexBenefitCategoryLine] = Field(default_factory=list)
    # Flex "price tags": wallet spent to offset elected coverage, the net balance,
    # and the per-product breakdown. Present only when a price-tag matrix exists.
    price_tags_total: float | None = None
    flex_balance: float | None = None
    price_tag_lines: list[FlexPriceTagLine] = Field(default_factory=list)
    # False when the member's age couldn't be resolved (missing/unparseable DOB),
    # so price tags couldn't be applied — the balance would otherwise look free.
    price_age_known: bool = True
    # Buy/sell-leave trade folded into the balance (signed flex impact: buy spends,
    # sell credits). Present only when the member has a priced, confirmed trade.
    leave_action: str | None = None
    leave_days: float | None = None
    leave_flex_amount: float | None = None
    # True when the scheme was edited after this wallet was assigned (so the
    # tier's claimable categories/cost-share shown here may be out of date, or the
    # tier no longer resolves) — the UI prompts a re-assign.
    assignment_stale: bool = False


class BenefitStatementOut(BaseModel):
    employee: StatementEmployee
    policy_year_id: str
    is_matched: bool
    attributes: list[StatementAttribute] = Field(default_factory=list)
    coverage: list[CoverageLine] = Field(default_factory=list)
    dependants: list[DependantSummary] = Field(default_factory=list)
    # Flexible-Benefits wallet, present only when a confirmed Flex scheme has been
    # assigned and the employee lands in an eligibility tier.
    flex: FlexCoverageLine | None = None


class DependantList(BaseModel):
    total: int
    offset: int
    limit: int
    items: list[DependantOut]


class DependantPatch(BaseModel):
    attribute_values: dict[str, Any] | None = None
    employee_id: str | None = None
    # Distinguishes "unlink" (employee_id explicitly null) from "don't touch the
    # link" — only relink when the caller sends the key.
    relink: bool = False


class DuplicateEntry(BaseModel):
    """One skipped-as-duplicate row in an upload manifest."""

    row: int  # 1-based data-row number in the uploaded workbook
    name: str | None = None
    staff_id: str | None = None
    nric_masked: str | None = None
    # "in_file"   repeated within this workbook, under the same employee
    # "existing"   already on file under the SAME employee
    #
    # There is deliberately no cross-employee reason: the same life under a
    # DIFFERENT employee is a second coverage line (both parents work here), so
    # it is inserted and counted as dual coverage rather than skipped.
    reason: str
    existing_id: str | None = None  # the record it collides with, when known


class UploadResult(BaseModel):
    inserted: int
    skipped: int
    #: Rows already on file that this upload ADOPTED onto the employee it names
    #: — dependants stored before their sponsor existed. Neither inserted nor
    #: skipped: counting them as either would report a roster that gained
    #: nothing, when what changed is that somebody is now covered.
    linked: int = 0
    errors: list[str]
    #: Advisory notes about rows that WERE imported (e.g. an identification
    #: number that fails its checksum). Separate from `errors` on purpose: a
    #: consumer gating on `errors == []` must not read a successful import of
    #: staff with one mistyped digit as a failed one.
    warnings: list[str] = Field(default_factory=list)
    duplicates: list[DuplicateEntry] = Field(default_factory=list)


class AutoMatchResult(BaseModel):
    matched: int
    unmatched: int


# ── Match results / audit ────────────────────────────────────────────────────


class MatchResultItem(BaseModel):
    employee_id: str
    employee_name: str | None
    staff_id: str
    raw_category: str | None
    matched_category_id: str | None
    matched_category_display: str | None
    match_method: str | None
    match_confidence: float | None


class MatchOverridePayload(BaseModel):
    # Single-category pin (back-compat). When `category_ids` is provided it takes
    # precedence and replaces the employee's entire manual match set.
    category_id: str | None = None
    category_ids: list[str] | None = None


class MatchRunResult(BaseModel):
    employees_total: int
    employees_matched: int
    employees_unmatched: int
    by_method: dict[str, int]
    duration_ms: int
    # Employees whose matching CRASHED (reset to unmatched) — distinct from
    # "no category matched"; these need engineering attention, not roster edits.
    errors: int = 0


class MatchResultsOut(BaseModel):
    # True when matching has never run OR categories changed after the last
    # run (stale matches) — the UI should prompt a (re-)run either way.
    pending: bool
    reason: str | None
    employees_total: int
    employees_matched: int
    employees_unmatched: int
    last_run_at: datetime | None = None
    items: list[MatchResultItem] = Field(default_factory=list)
    items_total: int = 0
    offset: int = 0
    limit: int = 50


class SnapshotOut(BaseModel):
    policy_year_id: str
    year: int
    activated_at: datetime | None
    snapshot: dict[str, Any]


class AuditLogEntry(_Base):
    id: str
    action: str
    entity_type: str
    entity_id: str | None
    user_id: str | None
    actor_name: str | None = None
    before: dict[str, Any] | None
    after: dict[str, Any] | None
    cross_tenant_access: bool
    created_at: datetime


class AuditLogPage(BaseModel):
    total: int
    items: list[AuditLogEntry]


# ── BYOK AI provider config ──────────────────────────────────────────────────

AIProviderStr = Literal["vertex"]


class AIConfigOut(_Base):
    """Metadata about the stored BYOK row — never exposes the cleartext key.

    `key_masked` is derived from `key_fingerprint`; we never decrypt on read so
    the rendered value is stable across GETs.
    """

    provider: AIProviderStr
    endpoint: str | None
    model: str | None
    capacity_mode: str | None = None
    key_fingerprint: str
    last_validated_at: datetime | None
    last_validation_error: str | None
    validated_fingerprint: str | None = None
    validated_model: str | None = None
    validated_location: str | None = None
    validated_capacity_mode: str | None = None
    validation_status: str | None = None

    @computed_field  # type: ignore[prop-decorator]
    @property
    def key_masked(self) -> str:
        return "••••" + self.key_fingerprint[-4:]


def _looks_like_service_account(api_key: str) -> bool:
    """A Vertex BYOK key is the service-account JSON — parse-check the markers."""
    try:
        data = json.loads(api_key)
    except (ValueError, TypeError):
        return False
    return (
        isinstance(data, dict)
        and data.get("type") == "service_account"
        and bool(data.get("private_key"))
        and bool(data.get("client_email"))
    )


class AIConfigUpsert(BaseModel):
    # provider is always "vertex" (Gemini) — the only supported provider.
    provider: AIProviderStr = "vertex"
    # endpoint = the GCP location; model = the Gemini model id.
    endpoint: str | None = None
    model: str | None = Field(default=None, max_length=128)
    capacity_mode: Literal["standard_paygo", "provisioned_throughput"] = "standard_paygo"
    # The service-account JSON key (hence the larger max_length).
    api_key: str = Field(min_length=8, max_length=8192)

    @model_validator(mode="after")
    def _check_endpoint(self) -> AIConfigUpsert:
        if self.endpoint and len(self.endpoint) > 512:
            raise ValueError("endpoint must be ≤ 512 chars")
        if not _looks_like_service_account(self.api_key):
            raise ValueError(
                "api_key for provider='vertex' must be the service-account JSON "
                "key (with type='service_account', private_key and client_email)."
            )
        return self


class AIConfigTestPayload(BaseModel):
    """Optional draft to test without saving. Falls back to the stored row."""

    provider: AIProviderStr | None = None
    endpoint: str | None = None
    model: str | None = Field(default=None, max_length=128)
    capacity_mode: Literal["standard_paygo", "provisioned_throughput"] | None = None
    api_key: str | None = Field(default=None, min_length=8, max_length=8192)

    @model_validator(mode="after")
    def _check_endpoint(self) -> AIConfigTestPayload:
        if self.endpoint and len(self.endpoint) > 512:
            raise ValueError("endpoint must be at most 512 chars")
        return self


class AIConfigTestResult(BaseModel):
    ok: bool
    error: str | None
    latency_ms: int


class AIBudgetUpdate(BaseModel):
    """Set the tenant's monthly AI token budget. 0 = unlimited (tracking only).

    Capped at 1e9 to catch fat-finger entries; that's ~$300k/month of Gemini,
    far beyond any real budget, so it never blocks a legitimate limit.
    """

    monthly_token_budget: int = Field(ge=0, le=1_000_000_000)


# ── Platform-wide AI limits (system-admin; shared key/quota across all firms) ──
_PLATFORM_TOKEN_MAX = 1_000_000_000_000  # 1e12 — fat-finger guard, not a real cap
_PLATFORM_CONCURRENCY_MAX = 512  # far above any sane per-process worker count


class PlatformAICredentialsOut(BaseModel):
    """Metadata about the stored platform key — never exposes the cleartext.

    `configured` is False when no key is stored, in which case every other
    field is None and AI falls through to a per-company BYOK row or env.
    """

    configured: bool
    provider: AIProviderStr | None = None
    location: str | None = None
    model: str | None = None
    capacity_mode: str | None = None
    key_fingerprint: str | None = None
    last_validated_at: datetime | None = None
    last_validation_error: str | None = None
    validated_fingerprint: str | None = None
    validated_model: str | None = None
    validated_location: str | None = None
    validated_capacity_mode: str | None = None
    validation_status: str | None = None

    @computed_field  # type: ignore[prop-decorator]
    @property
    def key_masked(self) -> str | None:
        return "••••" + self.key_fingerprint[-4:] if self.key_fingerprint else None


class PlatformAISettingsOut(BaseModel):
    """Effective platform AI limits in force (0 = disabled) + key status."""

    platform_monthly_token_cap: int
    default_monthly_token_budget: int
    max_concurrent_calls: int
    credentials: PlatformAICredentialsOut


class PlatformAISettingsUpdate(BaseModel):
    """Set the platform-wide AI limits. 0 on any field = that limit disabled.

    Limits only — credentials have their own endpoint so saving a limit can
    never clear the key (and vice versa).
    """

    platform_monthly_token_cap: int = Field(ge=0, le=_PLATFORM_TOKEN_MAX)
    default_monthly_token_budget: int = Field(ge=0, le=_PLATFORM_TOKEN_MAX)
    max_concurrent_calls: int = Field(ge=0, le=_PLATFORM_CONCURRENCY_MAX)


class PlatformAICredentialsUpsert(BaseModel):
    """Set the platform Vertex key — the default every company runs on."""

    provider: AIProviderStr = "vertex"
    # location = the GCP region; model = the Gemini model id.
    location: str | None = Field(default=None, max_length=64)
    model: str | None = Field(default=None, max_length=128)
    capacity_mode: Literal["standard_paygo", "provisioned_throughput"] = "standard_paygo"
    service_account_json: str = Field(min_length=8, max_length=8192)

    @model_validator(mode="after")
    def _check_key(self) -> PlatformAICredentialsUpsert:
        if not _looks_like_service_account(self.service_account_json):
            raise ValueError(
                "service_account_json must be the Vertex service-account JSON "
                "key (with type='service_account', private_key and client_email)."
            )
        return self


class PlatformAICredentialsTestPayload(BaseModel):
    """Optional draft to test without saving. Falls back to the stored key."""

    location: str | None = Field(default=None, max_length=64)
    model: str | None = Field(default=None, max_length=128)
    capacity_mode: Literal["standard_paygo", "provisioned_throughput"] | None = None
    service_account_json: str | None = Field(default=None, min_length=8, max_length=8192)
