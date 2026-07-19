"""Pydantic request/response models for the API."""
from __future__ import annotations

from datetime import date, datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, computed_field, model_validator

ParticipationModelStr = Literal["standard", "extended", "eo_only"]
InsuranceLineStr = Literal["medical", "life", "flex"]
LayoutFamilyStr = Literal["si_based", "plan_tier", "travel", "named_person", "earnings"]


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
    description: str | None = None


class AttributeSchemaPatch(BaseModel):
    display_name: str | None = None
    data_type: str | None = None
    enum_values: list[str] | None = None
    is_required: bool | None = None
    is_pii: bool | None = None
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
    insurer: str | None = None
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
    insurer: str | None = None
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


class ProductOut(_Base):
    id: str
    client_id: str | None
    code: str
    display_name: str
    insurer: str | None = None
    participation_model: str
    has_dependants: bool
    is_outpatient: bool
    # Broker-facing Medical / Life / Flex grouping (computed from code +
    # product_metadata override; not a stored column).
    line: InsuranceLineStr = "medical"
    # Structural classification (computed from code + product_metadata
    # override) — drives the setup-form shape and slip extraction.
    form_profile: str | None = None
    layout_family: str | None = None
    # Display code used on insurer report columns when it differs from the
    # internal code (e.g. GCGP → "GOGP"). Rides product_metadata.
    report_code: str | None = None


class ProductCreate(BaseModel):
    code: str
    display_name: str
    insurer: str | None = None
    participation_model: ParticipationModelStr = "standard"
    has_dependants: bool = False
    is_outpatient: bool = False
    # Optional overrides persisted into product_metadata for custom products.
    line: InsuranceLineStr | None = None
    form_profile: str | None = None
    layout_family: LayoutFamilyStr | None = None
    report_code: str | None = None


class ProductPatch(BaseModel):
    code: str | None = None
    display_name: str | None = None
    insurer: str | None = None
    participation_model: ParticipationModelStr | None = None
    has_dependants: bool | None = None
    is_outpatient: bool | None = None
    # Classification overrides persisted into product_metadata (the broker's
    # answer to a needs_classification upload diagnostic).
    line: InsuranceLineStr | None = None
    form_profile: str | None = None
    layout_family: LayoutFamilyStr | None = None
    report_code: str | None = None


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
    activated_at: datetime | None = None


class PolicyYearCreate(BaseModel):
    start_date: date
    end_date: date
    claim_grace_period_days: int | None = None

    @model_validator(mode="after")
    def _check_range(self) -> PolicyYearCreate:
        if self.end_date < self.start_date:
            raise ValueError("end_date must be on or after start_date")
        if self.claim_grace_period_days is not None and self.claim_grace_period_days < 0:
            raise ValueError("claim_grace_period_days must be zero or positive")
        return self


class PolicyYearUpdate(BaseModel):
    """Partial update of a benefit year (dates + claim grace period).

    Only fields present in the request body are written (``model_fields_set``),
    so a grace-period-only update can't wipe the dates and vice versa.
    """

    start_date: date | None = None
    end_date: date | None = None
    claim_grace_period_days: int | None = None

    @model_validator(mode="after")
    def _check(self) -> PolicyYearUpdate:
        if (
            self.start_date is not None
            and self.end_date is not None
            and self.end_date < self.start_date
        ):
            raise ValueError("end_date must be on or after start_date")
        if self.claim_grace_period_days is not None and self.claim_grace_period_days < 0:
            raise ValueError("claim_grace_period_days must be zero or positive")
        return self


class PolicyYearCopyIn(BaseModel):
    """Create a new benefit year and clone a source year's configuration into it."""

    start_date: date
    end_date: date
    claim_grace_period_days: int | None = None

    @model_validator(mode="after")
    def _check_range(self) -> PolicyYearCopyIn:
        if self.end_date < self.start_date:
            raise ValueError("end_date must be on or after start_date")
        return self


class PolicyYearCopyResult(BaseModel):
    policy_year: PolicyYearOut
    copied: dict[str, int]


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
    # Medical / Life / Flex line, so coverage rows scope to their tab.
    line: InsuranceLineStr = "medical"
    # Tri-state GST opinion: None = inherit (flex-scheme default), True = gross,
    # False = explicit "no GST". Slip amounts are always GST-exclusive.
    gst_included: bool | None = None
    gst_rate: float | None = None
    # Free cover limit (underwriting): SI auto-accepted without medicals.
    free_cover_limit: float | None = None
    # Insurer-issued policy number for this product's placement.
    policy_number: str | None = None


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
    policy_number: str | None = Field(default=None, max_length=64)

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
    # Medical / Life / Flex line this product group belongs to (for tab routing).
    line: InsuranceLineStr = "medical"
    categories: list[CategoryOut]


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


class FlexCoverageLine(BaseModel):
    """The employee's resolved Flexible-Benefits wallet — no premium figures.

    Sourced from the persisted ``Employee.flex_*`` snapshot (wallet amount,
    currency, tier, family status) and enriched with the claimable categories +
    cost-share from the matching scheme tier for display.
    """

    scheme_name: str | None = None
    tier_name: str | None = None
    family_status: str | None = None
    wallet_amount: float | None = None
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
    # "in_file" (repeated within this workbook) | "existing" (already on file)
    reason: str
    existing_id: str | None = None  # the record it collides with, when known


class UploadResult(BaseModel):
    inserted: int
    skipped: int
    errors: list[str]
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


class ActivationResult(BaseModel):
    policy_year_id: str
    status: str
    activated_at: datetime
    snapshot_counts: dict[str, int]


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
    before: dict[str, Any] | None
    after: dict[str, Any] | None
    cross_tenant_access: bool
    created_at: datetime


class AuditLogPage(BaseModel):
    total: int
    items: list[AuditLogEntry]


# ── BYOK AI provider config ──────────────────────────────────────────────────

AIProviderStr = Literal["azure_foundry", "anthropic"]


class AIConfigOut(_Base):
    """Metadata about the stored BYOK row — never exposes the cleartext key.

    `key_masked` is derived from `key_fingerprint`; we never decrypt on read so
    the rendered value is stable across GETs.
    """

    provider: AIProviderStr
    endpoint: str | None
    model: str | None
    key_fingerprint: str
    last_validated_at: datetime | None
    last_validation_error: str | None

    @computed_field  # type: ignore[prop-decorator]
    @property
    def key_masked(self) -> str:
        return "••••" + self.key_fingerprint[-4:]


class AIConfigUpsert(BaseModel):
    provider: AIProviderStr
    endpoint: str | None = None
    model: str | None = Field(default=None, max_length=128)
    api_key: str = Field(min_length=8, max_length=4096)

    @model_validator(mode="after")
    def _check_endpoint(self) -> AIConfigUpsert:
        if self.provider == "azure_foundry" and not (self.endpoint or "").strip():
            raise ValueError("endpoint is required for provider='azure_foundry'")
        if self.endpoint and len(self.endpoint) > 512:
            raise ValueError("endpoint must be ≤ 512 chars")
        if self.provider == "azure_foundry" and self.endpoint:
            from app.core.ai_config import normalize_foundry_endpoint

            normalize_foundry_endpoint(self.endpoint)
        return self


class AIConfigTestPayload(BaseModel):
    """Optional draft to test without saving. Falls back to the stored row."""

    provider: AIProviderStr | None = None
    endpoint: str | None = None
    model: str | None = Field(default=None, max_length=128)
    api_key: str | None = Field(default=None, min_length=8, max_length=4096)

    @model_validator(mode="after")
    def _check_endpoint(self) -> AIConfigTestPayload:
        if self.endpoint and len(self.endpoint) > 512:
            raise ValueError("endpoint must be at most 512 chars")
        if self.provider == "azure_foundry" and self.endpoint:
            from app.core.ai_config import normalize_foundry_endpoint

            normalize_foundry_endpoint(self.endpoint)
        return self


class AIConfigTestResult(BaseModel):
    ok: bool
    error: str | None
    latency_ms: int
