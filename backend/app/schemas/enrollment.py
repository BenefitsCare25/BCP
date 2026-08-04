"""Pydantic request/response models for the enrollment module."""
from __future__ import annotations

from datetime import date, datetime
from typing import Any, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.schemas.api import PlanFinancials
from app.schemas.member_query import MemberQuery


class _Base(BaseModel):
    model_config = ConfigDict(from_attributes=True)


WindowTypeStr = Literal["open", "new_hire", "life_event"]
WindowStatusStr = Literal["draft", "open", "closed"]
DefaultBehaviorStr = Literal["deemed_keep_current", "deemed_decline"]
# Per-product flex price-tag source + company-wide drawdown rule (set at window
# creation). "slip" = price tag derived from the placement slip's premiums;
# "manual" = price tag configured in the portal matrix. "full" = deduct the whole
# plan price tag; "on_change" = deduct only the upgrade/downgrade difference.
FlexPriceSourceStr = Literal["slip", "manual"]
FlexDrawdownRuleStr = Literal["full", "on_change"]


# ── Enrollment windows (enrollment periods) ─────────────────────────────────


class EnrollmentWindowOut(_Base):
    id: str
    policy_year_id: str
    name: str
    window_type: str
    opens_at: datetime
    closes_at: datetime
    status: str
    default_behavior: str
    allow_plan_change: bool
    allow_leave: bool
    allow_dependant_changes: bool
    product_scope: list[Any] | None
    flex_price_source: dict[str, str] | None = None
    flex_drawdown_rule: str = "full"
    allow_overdraft: bool = False
    created_by: str | None


class EnrollmentWindowCreate(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    window_type: WindowTypeStr = "open"
    opens_at: datetime
    closes_at: datetime
    default_behavior: DefaultBehaviorStr = "deemed_keep_current"
    allow_plan_change: bool = True
    allow_leave: bool = False
    allow_dependant_changes: bool = True
    product_scope: list[str] | None = None
    # {product_id: "slip" | "manual"} — products omitted fall back to "manual".
    flex_price_source: dict[str, FlexPriceSourceStr] | None = None
    flex_drawdown_rule: FlexDrawdownRuleStr = "full"
    # Whether elections may draw more flex than the member's wallet holds. Off
    # (the default), submit/confirm reject an overdrawn enrollment.
    allow_overdraft: bool = False

    @model_validator(mode="after")
    def _check_dates(self) -> Self:
        if self.opens_at >= self.closes_at:
            raise ValueError("opens_at must be before closes_at.")
        return self


class WindowOpenResult(BaseModel):
    """Response for the open/sync endpoint — the window plus how many new
    enrollments the sync created, so the UI can confirm the sync actually did
    something instead of a bare "success" toast."""

    window: EnrollmentWindowOut
    enrollments_created: int


class EnrollmentWindowPatch(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=255)
    opens_at: datetime | None = None
    closes_at: datetime | None = None
    default_behavior: DefaultBehaviorStr | None = None
    allow_plan_change: bool | None = None
    allow_leave: bool | None = None
    allow_dependant_changes: bool | None = None
    product_scope: list[str] | None = None
    flex_price_source: dict[str, FlexPriceSourceStr] | None = None
    flex_drawdown_rule: FlexDrawdownRuleStr | None = None
    allow_overdraft: bool | None = None


# ── Leave policy (buy/sell leave configuration — days only) ──────────────────


class LeavePolicyOut(_Base):
    id: str
    policy_year_id: str
    allow_buy: bool
    allow_sell: bool
    min_buy_days: float
    max_buy_days: float
    min_sell_days: float
    max_sell_days: float
    increment_days: float
    # Per-day buy/sell rate keyed by an employee attribute (grade/designation):
    # {"attribute": "<key>", "rates": {<value>: rate}}. Empty = leave priced at 0.
    leave_rates: dict[str, Any] = Field(default_factory=dict)
    notes: str | None


class LeavePolicyUpsert(BaseModel):
    allow_buy: bool = True
    allow_sell: bool = True
    min_buy_days: float = Field(default=0.0, ge=0)
    max_buy_days: float = Field(default=0.0, ge=0)
    min_sell_days: float = Field(default=0.0, ge=0)
    max_sell_days: float = Field(default=0.0, ge=0)
    increment_days: float = Field(default=1.0, gt=0)
    leave_rates: dict[str, Any] = Field(default_factory=dict)
    notes: str | None = None

    @model_validator(mode="after")
    def _check(self) -> Self:
        if self.max_buy_days < self.min_buy_days:
            raise ValueError("max_buy_days must be >= min_buy_days.")
        if self.max_sell_days < self.min_sell_days:
            raise ValueError("max_sell_days must be >= min_sell_days.")
        return self


class LeaveRateValue(BaseModel):
    value: str
    count: int


class LeaveRateOptions(BaseModel):
    """Available grade/designation attributes + their distinct roster values, so the
    leave-policy config can offer a rate cell per category."""

    attributes: list[str]
    values: dict[str, list[LeaveRateValue]]


# ── Enrollments + elections + leave ─────────────────────────────────────────

LeaveActionStr = Literal["none", "buy", "sell"]


class EnrollmentElectionIn(BaseModel):
    product_code: str
    plan_code: str | None = None
    # The elected cohort tier. Required to disambiguate tiers that share a
    # plan_code (e.g. GPA "Option N"); optional otherwise (the server resolves it
    # from plan_code within the member's cohort).
    tier_category_id: str | None = None
    declined: bool = False
    covered_dependant_ids: list[str] | None = None
    # Elected freestanding dependant option LEVEL per role (``{role: category_id}``,
    # role ∈ spouse/child) — only for products whose slip lists multiple unlinked
    # dependant option levels (the options API exposes them as ``option_choices``).
    dependant_option_ids: dict[str, str] | None = None
    notes: str | None = None

    @model_validator(mode="after")
    def _check(self) -> Self:
        if self.declined and self.plan_code:
            raise ValueError("A declined benefits selection cannot also name a plan_code.")
        if self.declined and self.tier_category_id:
            raise ValueError("A declined benefits selection cannot also name a tier.")
        if self.declined and self.dependant_option_ids:
            raise ValueError(
                "A declined benefits selection cannot also elect dependant option levels."
            )
        if self.dependant_option_ids:
            bad = set(self.dependant_option_ids) - {"spouse", "child"}
            if bad:
                raise ValueError(
                    f"Unknown dependant option role(s): {', '.join(sorted(bad))}."
                )
        return self


class ElectionsUpdate(BaseModel):
    elections: list[EnrollmentElectionIn] = Field(min_length=1)


class EnrollmentSubmitIn(BaseModel):
    """Optional submit body. ``acknowledge_unpriced`` lets the broker
    deliberately submit elections that change coverage without a configured
    flex price (otherwise submit 409s with code ``unpriced_elections``)."""

    acknowledge_unpriced: bool = False


class LeaveElectionIn(BaseModel):
    action: LeaveActionStr = "none"
    days: float = Field(default=0.0, ge=0)


class EnrollmentElectionOut(_Base):
    product_id: str
    product_code: str
    previous_plan_code: str | None
    elected_plan_code: str | None
    tier_category_id: str | None = None
    action: str
    covered_dependant_ids: list[str] | None
    dependant_option_ids: dict[str, str] | None = None
    # Flex wallet amount deducted for this election (None = no flex price).
    flex_price_tag: float | None = None
    notes: str | None


# ── Electable cohort tiers (scoped, direction-aware election options) ────────


class BenefitDifferenceOut(BaseModel):
    """One benefit row on which an electable tier differs from the baseline."""

    # The parent benefit when this row is a sub-item ("Specialist Care"), so
    # the UI can set it quietly above the specific benefit instead of joining
    # the two into one long sentence. None for a top-level row.
    group: str | None = None
    # The row's own headline, with the insurer's bracketed wording split off.
    benefit: str
    # That bracketed wording ("on cashless basis · including Specialist
    # Outpatient Clinics in Govt Restructured hospitals"). Load-bearing — it is
    # the difference between being billed and not — but it is also most of the
    # string, so it is placed rather than dropped.
    qualifier: str | None = None
    # The verbatim schedule cells. `None` means the plan states nothing for the
    # row, which the member surface prints as "Not covered" rather than as an
    # empty cell — a blank there reads as a rendering fault, not as an answer.
    current: str | None = None
    elected: str | None = None
    # The row's value type, so the figures format exactly as they do on the
    # coverage tab (`lib/benefitSchedule.ts::formatValue`). Without it a limit
    # of 20000 prints as "20000" beside the same row's "S$20,000".
    kind: str | None = None


class CohortTierOut(BaseModel):
    # Stable unique key for this tier within the product = tier_category_id +
    # plan_code. Use it as the election dropdown's value: tier_category_id and
    # plan_code can each repeat across tiers, only the pair is unique.
    key: str
    tier_category_id: str
    plan_code: str | None
    label: str
    participation: str | None
    direction: str  # 'upgrade' | 'downgrade' | 'same' | 'unknown'
    is_baseline: bool
    # The tier this member holds TODAY — the cohort default unless a standing
    # override moved them off it. This, not ``is_baseline``, is what "your
    # current plan" means and what ``differences`` are measured from; the two
    # coincide for every member without an override. Served rather than derived
    # because only the server can resolve an override.
    is_current: bool = False
    financials: PlanFinancials | None = None
    # Flex wallet cost of electing this tier for this member (resolved by their
    # age band). None = no price configured. Distinct from the insurer premium
    # carried in ``financials``.
    price_tag: float | None = None
    # What actually CHANGES if this tier is elected: the schedule rows on which
    # it differs from the baseline tier. Empty on the baseline itself and on
    # products whose plans share one schedule (the life ones, where the only
    # difference is the sum insured `financials` already carries).
    #
    # This is the half of the decision the surface used to omit. "Less cover —
    # adds back S$82.84" tells a member a switch is cheaper without telling
    # them what they give up, and the coverage tab can't help: it only ever
    # renders the plan they hold today, never the one they are considering.
    differences: list[BenefitDifferenceOut] = Field(default_factory=list)
    # The count BEFORE truncation, so a long list can say what it isn't
    # showing. Equal to len(differences) in every real case.
    differences_total: int = 0


class DependantRoleOut(BaseModel):
    """One family-composition role's flex cost for a tier (family_group mode)."""

    role: str  # 'spouse' | 'child' | 'both'
    label: str  # scheme label: ES/EC/EF or SO/CO/SC
    amount: float | None  # incremental flex over Employee-Only (None = unpriced)


class DependantTierPricingOut(BaseModel):
    """One plan/tier's dependant pricing — the amounts differ per plan."""

    family: list[DependantRoleOut] = Field(default_factory=list)
    per_pax_rate: float | None = None  # per_pax only: flat amount per dependant


class DependantOptionChoiceOut(BaseModel):
    """One electable freestanding dependant option LEVEL (a dependant-scope slip
    row, e.g. GTL "Spouse — S$40,000")."""

    category_id: str
    label: str
    sum_insured: float | None = None
    # Flat per-dependant flex amount; None = age-banded (see amounts_by_dependant).
    amount: float | None = None
    # Per-dependant resolved amounts for THIS member's dependants (age-banded rows
    # price on each dependant's own age). None value = that dependant can't price
    # (unknown date of birth).
    amounts_by_dependant: dict[str, float | None] = Field(default_factory=dict)


class DependantOptionRoleOut(BaseModel):
    role: str  # 'spouse' | 'child'
    choices: list[DependantOptionChoiceOut] = Field(default_factory=list)


class DependantPricingOut(BaseModel):
    """A product's dependant pricing, keyed per plan/tier so the election UI can show
    the extra flex drawn for the SELECTED tier (additive over Employee-Only)."""

    # 'slip_options' = slip dependant option rows that stick to the elected
    # employee plan; `family` then carries per-DEPENDANT amounts per role
    # (None = age-banded, priced per dependant server-side at save).
    mode: str  # 'none' | 'family_group' | 'per_pax' | 'slip_options'
    scheme: str | None = None  # family_group only: 'ec_es_ef' | 'so_co_sc'
    by_tier: dict[str, DependantTierPricingOut] = Field(default_factory=dict)
    # slip_options only: freestanding option LEVELS the member must elect per role
    # (the slip states no employee-plan linkage). Empty when the option rows are
    # linked (marker/composition/sole-row) — those price without an election.
    option_choices: list[DependantOptionRoleOut] = Field(default_factory=list)


class ProductTierSetOut(BaseModel):
    product_id: str
    product_code: str
    # The product's own name, so a MEMBER surface never has to lead with a code.
    # The Printed-Label Rule (DESIGN.md) says no code appears without a
    # plain-language gloss beside it, and the portal's gloss map only covers
    # codes it knows — an unlisted product left the member choosing between
    # tiers under a bare "GXYZ". None when the product row is gone.
    product_name: str | None = None
    employee_participation: str | None
    dependant_participation: str | None
    baseline_tier_category_id: str
    baseline_plan_code: str | None
    allow_plan_change: bool
    can_decline: bool
    tiers: list[CohortTierOut]
    # Dependant pricing for this product (None when not configured), priced for the
    # baseline tier so the UI can preview "add spouse +$X".
    dependant: DependantPricingOut | None = None


class MemberLeaveOptionsOut(BaseModel):
    """What this member may trade in leave — the policy-year bounds PLUS their own
    eligibility, so the election UI can state the limit (and its dollar value at
    ``member_leave_rate``) up front instead of surfacing it as a 422 on save.

    Every field here is already enforced server-side by
    ``enrollment_validation.validate_leave`` / ``apply_leave``; this only makes the
    same rules legible. ``sell_eligible`` is the per-member roster flag
    ("Eligible to Sell Leave") — absent = eligible.
    """

    allow_buy: bool
    allow_sell: bool
    min_buy_days: float
    # RESOLVED maxima: the member's tier override if it has one, else the policy
    # default (`leave_pricing_resolver.leave_limits_for`). Never the raw global
    # field — the UI must state the same cap `validate_leave` enforces.
    max_buy_days: float
    min_sell_days: float
    max_sell_days: float
    increment_days: float
    sell_eligible: bool
    # The grade/designation the member's rate + caps were looked up by, so the UI
    # can say WHY they are priced/capped rather than showing a bare "no rate".
    rate_attribute: str | None = None
    rate_value: str | None = None
    # True when the caps above came from that tier's own entry (not the default).
    limits_from_tier: bool = False


class EnrollmentOptionsOut(BaseModel):
    """Per-product electable tiers for one member, scoped to their cohort."""

    # None when options are built without a materialized enrollment row
    # (the portal preview surface).
    enrollment_id: str | None = None
    products: list[ProductTierSetOut]
    # Member's flex wallet + age, so the UI can show the running flex balance as
    # the member elects (wallet minus total price tags). None when no flex exists.
    flex_wallet: float | None = None
    flex_currency: str | None = None
    member_age: int | None = None
    # Per-day buy/sell-leave rate for this member (None when no rate applies), so the
    # UI can fold a live leave trade into the running balance. The bounds that rate
    # is charged within live on `leave` (None when the year has no leave policy).
    member_leave_rate: float | None = None
    leave: MemberLeaveOptionsOut | None = None
    # The window's flex drawdown rule, so the UI can label each tier's price tag as
    # the full plan cost ("full") or the upgrade/downgrade difference ("on_change").
    flex_drawdown_rule: str = "full"


class LeaveElectionOut(_Base):
    action: str
    days: float
    status: str
    # Signed flex-wallet impact of the trade (buy spends, sell credits); None = unpriced.
    flex_amount: float | None = None


class EnrollmentRosterItem(BaseModel):
    id: str
    employee_id: str
    staff_id: str
    employee_name: str | None
    status: str


class EnrollmentRoster(BaseModel):
    items: list[EnrollmentRosterItem]
    total: int
    offset: int
    limit: int


class EnrollmentOut(BaseModel):
    id: str
    window_id: str
    policy_year_id: str
    employee_id: str
    staff_id: str
    employee_name: str | None
    status: str
    baseline_snapshot: dict[str, Any] | None
    submitted_at: datetime | None
    confirmed_at: datetime | None
    elections: list[EnrollmentElectionOut]
    leave: LeaveElectionOut | None
    # Product codes that are compulsory for this employee — cannot be declined.
    compulsory_product_codes: list[str] = Field(default_factory=list)


class WindowCloseSummary(BaseModel):
    confirmed: int
    deemed_kept: int
    deemed_declined: int
    already: int


class PortalEnrollmentOut(BaseModel):
    """The member's enrollment surface (also mirrored by the broker's
    employee-view preview): the open in-period window, the member's own
    enrollment session, and their cohort-scoped electable options. All None
    when no enrollment window is currently open."""

    window: EnrollmentWindowOut | None = None
    enrollment: EnrollmentOut | None = None
    options: EnrollmentOptionsOut | None = None


# ── Bulk plan update ────────────────────────────────────────────────────────

BulkActionStr = Literal["set_plan", "decline"]
DependantModeStr = Literal["include_all", "exclude_all", "set"]


class BulkSelector(MemberQuery):
    """The bulk tool's selector — a full ``MemberQuery`` plus the two singular
    fields the original API shipped with.

    The legacy fields are folded into their plural counterparts by the validator
    below, so ``services/member_query`` only ever sees one shape and old request
    bodies (and the tests pinning them) keep working with no adapter.
    """

    # Legacy: superseded by ``category_ids`` / ``current_plan_codes``.
    category_id: str | None = None
    current_plan_code: str | None = None

    @model_validator(mode="before")
    @classmethod
    def _fold_legacy(cls, data: Any) -> Any:
        if not isinstance(data, dict):
            return data
        folded = dict(data)
        for legacy, plural in (
            ("category_id", "category_ids"),
            ("current_plan_code", "current_plan_codes"),
        ):
            value = folded.get(legacy)
            if value:
                existing = list(folded.get(plural) or [])
                if value not in existing:
                    existing.append(value)
                folded[plural] = existing
        return folded


class BulkDependantAction(BaseModel):
    mode: DependantModeStr
    dependant_ids: list[str] = Field(default_factory=list)


class BulkPlanUpdateRequest(BaseModel):
    product_code: str
    action: BulkActionStr = "set_plan"
    target_plan_code: str | None = None
    selector: BulkSelector
    dependant_action: BulkDependantAction | None = None
    # Apply only: the fingerprint the preview returned. When present, apply
    # refuses (409 ``selection_changed``) if the population or its coverage has
    # moved since — the guard that makes applying a RULE rather than a list of
    # ids safe. Absent (legacy callers) = no guard.
    selection_digest: str | None = Field(default=None, max_length=64)

    @model_validator(mode="after")
    def _check(self) -> Self:
        if self.action == "set_plan" and not self.target_plan_code:
            raise ValueError("target_plan_code is required when action is 'set_plan'.")
        if self.action == "decline" and self.target_plan_code:
            raise ValueError("target_plan_code must be omitted when action is 'decline'.")
        return self


class BulkRowOutcome(BaseModel):
    employee_id: str | None
    staff_id: str | None
    # "applied" | "would_apply" | "no_change" | "skipped" | "error".
    # ``no_change`` is its own outcome because folding it into "applied" made
    # "applied 412" mean anything between 8 and 412 real changes.
    outcome: str
    reason: str | None = None
    employee_name: str | None = None
    from_plan: str | None = None
    to_plan: str | None = None
    declined_before: bool = False
    declined_after: bool = False
    # The flex price tag this row would write (and what it replaces). Computed on
    # BOTH preview and apply — the dry run has to be able to state what the real
    # run will write.
    flex_price_tag_before: float | None = None
    flex_price_tag_after: float | None = None
    # The row REMOVES the member's override (the target equals their cohort
    # default), so there is no tag to write. Distinct from a tag that could not
    # be resolved — without the flag a "move everyone back to the default" run
    # reports every row as unpriced.
    override_cleared: bool = False


class BulkChangeGroup(BaseModel):
    """Rows collapsed to ``from → to`` with a headcount — what a broker actually
    reads before applying. The row table is the drill-down, not the summary."""

    from_plan: str | None = None
    to_plan: str | None = None
    declined_after: bool = False
    count: int


class BulkImpact(BaseModel):
    """Totals over the rows that would change.

    Deliberately flex-only: the price tag is the figure this tool WRITES, so it
    can be stated exactly. Premium and sum-insured deltas need the target plan
    resolved to a cohort tier per member (a bare plan_code carries no basis), so
    they land with the cohort-aware pass rather than being estimated here.
    """

    members_changing: int = 0
    flex_price_tag_before: float = 0.0
    flex_price_tag_after: float = 0.0
    flex_price_tag_delta: float = 0.0
    # Rows whose price tag could not be resolved (no pricing configured for the
    # target tier) — a delta that quietly omits them would understate the spend.
    unpriced: int = 0


class BulkPreviewResult(BaseModel):
    rows: list[BulkRowOutcome]
    counts: dict[str, int]
    groups: list[BulkChangeGroup] = Field(default_factory=list)
    impact: BulkImpact = Field(default_factory=BulkImpact)
    selection_digest: str | None = None
    rows_total: int = 0
    rows_offset: int = 0


class BulkApplyResult(BaseModel):
    id: str
    status: str
    counts: dict[str, int]
    rows: list[BulkRowOutcome]
    groups: list[BulkChangeGroup] = Field(default_factory=list)
    impact: BulkImpact = Field(default_factory=BulkImpact)
    rows_total: int = 0


# ── Employee plan overrides (effective-coverage state) ──────────────────────


class PlanOverrideOut(_Base):
    id: str
    employee_id: str
    policy_year_id: str
    product_id: str
    product_code: str
    plan_code: str | None
    declined: bool
    covered_dependant_ids: list[str] | None
    dependant_option_ids: dict[str, str] | None = None
    source: str
    source_ref: str | None
    effective_from: date | None
    modified_by: str | None


# ── Coverage history + revert (track / reset flexibility) ────────────────────


class CoverageHistoryEntry(BaseModel):
    """One coverage-change event in a member's timeline."""

    id: str
    at: str
    action: str
    label: str
    actor: str | None = None
    product_code: str | None = None
    from_plan: str | None = None
    to_plan: str | None = None
    declined: bool | None = None


class CoverageHistoryOut(BaseModel):
    employee_id: str
    entries: list[CoverageHistoryEntry]
    # Whether a window baseline exists for this member (gates the revert-to-baseline
    # control so the UI doesn't offer an action the server would 409).
    has_baseline: bool = False


class CoverageRevertRequest(BaseModel):
    """Revert a member's coverage to the window baseline or the cohort default."""

    target: Literal["baseline", "default"]
    # Limit the revert to these product codes; omit/empty = all overridden products.
    product_codes: list[str] | None = None
    # Pin a specific window's baseline (target='baseline' only); omit = latest.
    window_id: str | None = None


class CoverageChangeOut(BaseModel):
    product_code: str
    outcome: str  # "reverted" | "reset_to_default" | "unchanged" | "skipped"
    from_plan: str | None = None
    to_plan: str | None = None
    detail: str | None = None


class CoverageRevertResult(BaseModel):
    employee_id: str
    target: str
    changes: list[CoverageChangeOut]


class PlanOverrideUpsert(BaseModel):
    """Set or update one employee's plan override for a product (manual admin)."""

    plan_code: str | None = None
    declined: bool = False
    covered_dependant_ids: list[str] | None = None
    # Elected freestanding dependant option LEVEL per role ({role: category_id}).
    # Omit to leave the stored value untouched; pass explicitly (incl. null) to
    # set or clear it. Declining always clears it.
    dependant_option_ids: dict[str, str] | None = None
    effective_from: date | None = None

    @model_validator(mode="after")
    def _check(self) -> Self:
        if self.declined and self.plan_code:
            raise ValueError("A declined override cannot also name a plan_code.")
        if self.declined and self.dependant_option_ids:
            raise ValueError(
                "A declined override cannot also elect dependant option levels."
            )
        if self.dependant_option_ids:
            bad = set(self.dependant_option_ids) - {"spouse", "child"}
            if bad:
                raise ValueError(
                    f"Unknown dependant option role(s): {', '.join(sorted(bad))}."
                )
        if not self.declined and not self.plan_code and self.covered_dependant_ids is None:
            raise ValueError(
                "Provide a plan_code, set declined=true, or specify covered_dependant_ids."
            )
        return self
