export type RuleNode = Record<string, unknown> | null;

export interface AttributeSchema {
  id: string;
  client_id: string | null;
  attribute_id: string;
  display_name: string;
  data_type: string;
  enum_values: string[] | null;
  is_required: boolean;
  is_pii: boolean;
  description: string | null;
  derived_from: string | null;
  derivation_rule: Record<string, unknown> | null;
}

// Broker-facing line of business — drives the Medical / Life / Flex tabs.
// Independent of form_profile (a product's structural shape).
export type InsuranceLine = "medical" | "life" | "flex";

export interface Product {
  id: string;
  client_id: string | null;
  code: string;
  display_name: string;
  /** LEGACY, read-only: pre-existing catalog rows only. The insurer is set per
   *  benefit year on Company & Benefits → Header & Policy. */
  insurer: string | null;
  participation_model: string;
  has_dependants: boolean;
  is_outpatient: boolean;
  line: InsuranceLine;
  // Structural classification (registry + product_metadata override).
  form_profile?: string | null;
  layout_family?: string | null;
  // Insurer-report display code when it differs from the internal code
  // (e.g. GCGP → "GOGP"). Rides product_metadata.
  report_code?: string | null;
  // Legal entities this product is written on — the matching gate for ALL its
  // categories. Empty = no restriction. Rides product_metadata.
  entities?: string[];
}

// ── Product registry (static classification catalog from the backend) ────────

export interface RegistryEntry {
  code: string;
  name: string;
  line: InsuranceLine;
  form_profile: string;
  layout_family: string;
  has_dependants: boolean;
}

export interface RegistryProfile {
  id: string;
  label: string;
  basis_model: string;
  rate_model: string;
  layout_family: string;
}

export interface ProductRegistry {
  entries: RegistryEntry[];
  profiles: RegistryProfile[];
  lines: InsuranceLine[];
  layout_families: string[];
}

// ── Slip-driven config recommendations (attributes + products) ───────────────

export interface DerivationSample {
  input: string;
  output: unknown;
}

export interface AttributeRecommendation {
  attribute_id: string;
  display_name: string;
  data_type: string;
  enum_values: string[] | null;
  is_pii: boolean;
  description: string | null;
  reasoning: string;
  already_exists: boolean;
  derived_from: string | null;
  derivation_rule: Record<string, unknown> | null;
  valid: boolean;
  match_count: number;
  sample_size: number;
  samples: DerivationSample[];
  warning: string | null;
}

export interface ProductRecommendation {
  code: string;
  display_name: string;
  participation_model: string;
  has_dependants: boolean;
  is_outpatient: boolean;
  reasoning: string;
  already_exists: boolean;
  category_count: number;
}

export interface ConfigRecommendation {
  policy_year_id: string;
  roster_present: boolean;
  employee_count: number;
  category_count: number;
  attributes: AttributeRecommendation[];
  products: ProductRecommendation[];
  model: string | null;
  cache_hit: boolean;
}

export interface ApplyAttributeItem {
  attribute_id: string;
  display_name: string;
  data_type: string;
  enum_values: string[] | null;
  is_pii: boolean;
  description: string | null;
  derived_from: string | null;
  derivation_rule: Record<string, unknown> | null;
}

export interface ApplyProductItem {
  code: string;
  display_name: string;
  participation_model: string;
  has_dependants: boolean;
  is_outpatient: boolean;
}

export interface ApplyConfigResult {
  attributes_created: string[];
  attributes_updated: string[];
  products_created: string[];
  categories_relinked: number;
  rematched: boolean;
  employees_matched: number | null;
}

export interface PolicyYear {
  id: string;
  client_id: string;
  year: number;
  start_date: string;
  end_date: string;
  // Company-level coverage window derived from per-product periods (earliest
  // product start → latest product end). Equals start_date/end_date when no
  // product overrides exist.
  coverage_start: string;
  coverage_end: string;
  status: "draft" | "active" | "archived";
  // Days after the coverage period ends during which claims may still be
  // submitted. null = no submission deadline (system default).
  claim_grace_period_days: number | null;
  /** Days after a member's LAST DAY OF SERVICE that they keep portal access.
   *  A different bound from the grace period above: that one belongs to the
   *  year, this one to the member. null = the system default (60). */
  leaver_access_days: number | null;
  activated_at: string | null;
}

export interface ProductTerm {
  product_id: string;
  code: string;
  display_name: string;
  coverage_start: string;
  coverage_end: string;
  // True when inheriting the policy year's span (no explicit dates stored —
  // the row may still exist for GST alone).
  is_default: boolean;
  line: InsuranceLine;
  // Tri-state GST opinion: null = inherit (flex-scheme default), true = gross,
  // false = explicit "no GST". Raw slip premiums are always GST-exclusive.
  gst_included: boolean | null;
  gst_rate: number | null;
  // Free cover limit (underwriting): SI auto-accepted without medicals.
  free_cover_limit: number | null;
  // Non-Evidence-Limit age (ANB): members at/above it require underwriting
  // regardless of sum insured. Null = no age gate.
  nel_age_limit: number | null;
  // Insurer-issued policy number for this product's placement.
  policy_number: string | null;
}

export type SourceKind =
  | "manual"
  | "system_generated"
  | "ai_extracted"
  | "csv_import";
export type CategoryStatus = "draft" | "needs_review" | "confirmed";

export interface ParticipationDetail {
  employee?: "compulsory" | "voluntary" | null;
  dependant?: "compulsory" | "voluntary" | null;
  direction?: string | null;
  raw?: string | null;
}

export interface Category {
  id: string;
  policy_year_id: string;
  product_id: string | null;
  priority: number;
  display_name: string;
  raw_description: string;
  matching_rule: RuleNode;
  rule_human_readable: string | null;
  participation_model: "compulsory" | "voluntary" | null;
  // Employee/dependant participation split parsed from the slip. The employee
  // card edits `.employee` (mirrored into participation_model); the dependant
  // card edits `.dependant`.
  participation_detail: ParticipationDetail | null;
  plan_assignments: Record<string, unknown> | null;
  source: SourceKind;
  source_ref: string | null;
  confidence: number | null;
  status: CategoryStatus;
  human_modified: boolean;
  modified_by: string | null;
  created_at: string;
  updated_at: string;
}

export interface CategoryGroup {
  product_code: string;
  product_display_name: string;
  product_id: string | null;
  line: InsuranceLine;
  categories: Category[];
}

export interface ProductDiagnostic {
  sheet: string;
  product_code: string;
  layout: "per_plan" | "descriptive" | "none";
  rate_model: string | null;
  n_categories: number;
  n_plans: number;
  n_benefit_items: number;
  confidence: number;
  reconciliation:
    | "consistent"
    | "fan_out"
    | "assign_default"
    | "unmappable"
    | "no_plans";
  issues: string[];
  low_confidence: boolean;
  needs_attention: boolean;
  empty_sob: boolean;
  used_ai: boolean;
  fingerprint: string | null;
  column_roles: SlipColumnRoles | null;
  // Registry classification of the sheet's product code. Unknown codes get a
  // "classify this product" action instead of a silent generic default.
  layout_family: string | null;
  registry_known: boolean;
  needs_classification: boolean;
}

export interface SlipColumnRoles {
  name_col: number;
  key_col: number | null;
  value_col: number | null;
  allow_letter_keys: boolean;
  name_first: boolean;
  confidence?: number;
}

export interface SlipTemplateProfileSave {
  fingerprint: string;
  product_code: string;
  insurer?: string | null;
  sheet_label?: string | null;
  roles: Partial<SlipColumnRoles>;
}

export interface ParseResult {
  placement_slip_id: string;
  policy_year_id: string;
  total_categories: number;
  high_confidence: number;
  needs_review: number;
  replaced_categories: number;
  skipped_sheets: { sheet: string; reason: string }[];
  prefilled_setups: string[];
  products: ProductDiagnostic[];
  /** True when a re-upload auto-rematched employees (absent on older backends). */
  rematched?: boolean;
  employees_matched?: number | null;
}

export interface PlacementSlipSummary {
  id: string;
  filename: string;
  parse_status: string;
  created_at: string;
  total_categories: number;
}

// Age-banded voluntary rate (per S$1000 sum assured) for one age band. The
// table is product-wide (shared by all the product's voluntary plans).
export interface VoluntaryRateBand {
  label: string;
  min: number | null;
  max: number | null;
  rate: number;
}

export interface PlanFinancials {
  num_employees: number | null;
  basis: string | null;
  sum_insured: number | null;
  premium_rate: number | null;
  annual_premium: number | null;
  rate_basis: string | null;
  rate_tiers: Record<string, { rate: number; premium: number }> | null;
  /** Age-banded voluntary rate table (life products) — present on voluntary tiers. */
  voluntary_rates?: VoluntaryRateBand[] | null;
  /** True when the premium figures are grossed up by the product's GST rate. */
  gst_included?: boolean;
}

export interface RateTier {
  rate: number | null;
  premium: number | null;
}

// Editable shape of Category.plan_assignments. plan_code is the linkage to a
// Plan record and must be preserved on edit — never surfaced as an input.
export interface PlanAssignment {
  plan_code?: string | number | null;
  num_employees?: number | null;
  basis?: string | null;
  sum_insured?: number | null;
  premium_rate?: number | null;
  // Separate per-member rate for dependants (per-member medical like GCGP/GCSP
  // publishes distinct Employee vs Dependent rates).
  dependant_rate?: number | null;
  annual_premium?: number | null;
  // Statutory (WICA): the estimated annual earnings the premium is rated on.
  estimated_annual_earnings?: number | null;
  rate_basis?: string | null;
  rate_tiers?: Record<string, RateTier> | null;
  // The slip's own label per tier code (e.g. { SO: "Spouse" }) — display only.
  tier_labels?: Record<string, string> | null;
  // Legal entities (company / subsidiary) covering this category. Stored as a
  // token list; rows written before the picker may still be a comma-joined
  // string, so read it through `insuredNames()` in lib/insured.ts.
  insured?: string[] | string | null;
}

// A qualifier row that sits beneath a benefit value, e.g.
// { label: "Maximum no. of days", value: "120 days" } or
// { label: "Maximum limit", value: "per policy year" }. Captured per plan.
export interface BenefitLimit {
  label: string;
  value: string | null;
}

export interface BenefitSubItem {
  key: string;
  name: string;
  value: string | null;
  // Footnote attached to this cell, e.g. "Include Implants" or
  // "Surgical schedule applies to surgery S$1,000 and above". Kept separate from
  // `value` so the number stays clean for display + downstream matching.
  note?: string | null;
  limits?: BenefitLimit[];
  kind?: BenefitKind;
}

export interface BenefitItem {
  number: string;
  name: string;
  value: string | null;
  note?: string | null;
  limits?: BenefitLimit[];
  sub_items: BenefitSubItem[];
  properties: Record<string, string>;
  // Persisted from the editor so read-only renderers format by TYPE instead of
  // guessing from the string (a "6" visit count is not "$6").
  kind?: BenefitKind;
}

export interface BenefitSchedule {
  items: BenefitItem[];
}

export interface MatchedPlan {
  product_code: string;
  product_name: string | null;
  category_id: string | null;
  plan_code: string | null;
  category_display: string | null;
  method: string | null;
  confidence: number | null;
  financials: PlanFinancials | null;
  benefit_schedule: BenefitSchedule | null;
  cover_description: string | null;
  annual_policy_limit: string | null;
  /** True when an EmployeePlanOverride changed this plan away from the
   *  cohort (category) default. */
  plan_overridden?: boolean;
  override_source?: string | null;
  /** Dependant ids the member elected to cover (override only). */
  covered_dependant_ids?: string[] | null;
}

export interface Employee {
  id: string;
  staff_id: string;
  employee_name: string | null;
  attribute_values: Record<string, unknown>;
  derived_attribute_values: Record<string, unknown>;
  matched_category_id: string | null;
  match_method: string | null;
  match_confidence: number | null;
  matched_plans: MatchedPlan[];
}

export interface EmployeeList {
  total: number;
  offset: number;
  limit: number;
  items: Employee[];
}

export interface CoverageProduct {
  product_code: string;
  product_name: string | null;
}

export interface CoverageSummaryItem {
  id: string;
  staff_id: string;
  employee_name: string | null;
  product_count: number;
  products: CoverageProduct[];
  /** Terminated on the roster — only present when leavers were asked for. */
  left: boolean;
}

export interface CoverageSummary {
  total: number;
  items: CoverageSummaryItem[];
}

export interface PlanDetail {
  id: string;
  product_id: string;
  policy_year_id: string;
  code: string;
  display_name: string;
  benefit_schedule: BenefitSchedule | null;
  cover_description: string | null;
  annual_policy_limit: string | null;
  // Insurer-facing label for report columns ("4 Bed Restr Hosp / …").
  report_label: string | null;
  financials: PlanFinancials | null;
  source: string;
  confidence: number | null;
  status: string;
  human_modified: boolean;
}

export interface PlanList {
  total: number;
  items: PlanDetail[];
}

export interface Dependant {
  id: string;
  employee_id: string | null;
  attribute_values: Record<string, unknown>;
  link_method: string | null;
  /** "active" | "pending_approval" | "rejected" — portal self-adds start pending. */
  status: string;
}

// ── Benefit statement (read-only employee coverage view) ────────────────────

export interface DependantSummary {
  id: string;
  name: string | null;
  relationship: string | null;
  dob: string | null;
  /** "spouse" | "child" | null — the pricing role, classified server-side.
   *  Never re-derive it from `relationship`: the word lists are subtle and a
   *  client-side copy of them drifted from the backend's once already. */
  role: string | null;
}

export interface StatementAttribute {
  key: string;
  label: string;
  value: string;
}

export interface CoverageLine {
  product_code: string;
  product_name: string | null;
  category_id: string | null;
  category_display: string | null;
  match_method: string | null;
  match_confidence: number | null;
  rule_human_readable: string | null;
  plan_code: string | null;
  cover_description: string | null;
  annual_policy_limit: string | null;
  benefit_schedule: BenefitSchedule | null;
  // Per-member Amount Covered + premium (age-banded for voluntary life tiers,
  // reflecting any elected upgrade/downgrade) — not group totals.
  financials: PlanFinancials | null;
  covers_dependants: boolean;
  covered_dependants: DependantSummary[];
}

export interface FlexBenefitCategoryLine {
  name: string;
  claimable: boolean;
  sub_limit: number | null;
  note: string | null;
}

export interface FlexPriceTagLine {
  product_code: string;
  plan_code: string | null;
  price_tag: number | null;
}

/**
 * How an annual flex allowance was scaled to one member's cover period.
 *
 * SERVED, never recomputed here: the month count has no exact JS equivalent
 * worth maintaining twice, and a fraction that drifts from the figure beside it
 * is silent. `note` is the printable form ("6/12 months").
 */
export interface FlexProrationLine {
  basis: string;
  factor: number;
  served: number;
  total: number;
  full_amount: number;
  note: string;
  period_start: string | null;
  period_end: string | null;
}

export interface FlexCoverageLine {
  scheme_name: string | null;
  tier_name: string | null;
  family_status: string | null;
  /** The EFFECTIVE allowance — pro-rated when the scheme says so. */
  wallet_amount: number | null;
  /** Present only when the allowance was pro-rated. */
  proration: FlexProrationLine | null;
  currency: string | null;
  source: string | null;
  employer_pct: number | null;
  employee_pct: number | null;
  benefit_categories: FlexBenefitCategoryLine[];
  /** Flex price tags: wallet spent to offset coverage, net balance, breakdown. */
  price_tags_total: number | null;
  flex_balance: number | null;
  price_tag_lines: FlexPriceTagLine[];
  /** False when the member's age couldn't be resolved, so price tags weren't applied. */
  price_age_known: boolean;
  /** Buy/sell-leave trade folded into the balance (signed: buy spends, sell credits). */
  leave_action: string | null;
  leave_days: number | null;
  leave_flex_amount: number | null;
  assignment_stale: boolean;
}

export interface BenefitStatement {
  employee: { id: string; staff_id: string; employee_name: string | null };
  policy_year_id: string;
  is_matched: boolean;
  attributes: StatementAttribute[];
  coverage: CoverageLine[];
  dependants: DependantSummary[];
  flex: FlexCoverageLine | null;
}

export interface DependantList {
  total: number;
  offset: number;
  limit: number;
  items: Dependant[];
}

export interface AutoMatchResult {
  matched: number;
  unmatched: number;
}

export type MatchMethod =
  | "exact_name"
  | "fuzzy_name"
  | "rule"
  | "manual_override";

export interface MatchResultItem {
  employee_id: string;
  employee_name: string | null;
  staff_id: string;
  raw_category: string | null;
  matched_category_id: string | null;
  matched_category_display: string | null;
  match_method: MatchMethod | null;
  match_confidence: number | null;
}

export interface MatchResults {
  pending: boolean;
  reason: string | null;
  employees_total: number;
  employees_matched: number;
  employees_unmatched: number;
  last_run_at: string | null;
  items: MatchResultItem[];
  items_total: number;
  offset: number;
  limit: number;
}

export interface MatchRunResult {
  employees_total: number;
  employees_matched: number;
  employees_unmatched: number;
  by_method: Record<string, number>;
  duration_ms: number;
  /** Employees whose matching CRASHED (distinct from unmatched). Absent on
   *  older backends. */
  errors?: number;
}

export interface AuditLogEntry {
  id: string;
  action: string;
  entity_type: string;
  entity_id: string | null;
  user_id: string | null;
  before: Record<string, unknown> | null;
  after: Record<string, unknown> | null;
  cross_tenant_access: boolean;
  created_at: string;
}

export interface AuditLogPage {
  total: number;
  items: AuditLogEntry[];
}

// ── ADC (Additions / Deletions / Changes) roster movement ──
export interface AdcFieldDiff {
  field: string;
  old: string | null;
  new: string | null;
}

export interface AdcOp {
  row: number;
  record_type: "employee" | "dependant";
  name: string | null;
  staff_id: string | null;
  nric_masked: string | null;
  target_id: string | null;
  effective: string | null;
  field_diffs: AdcFieldDiff[];
}

export interface AdcIssue {
  row: number;
  record_type: string;
  message: string;
}

/** A row that IS applied but looks wrong — distinct from `AdcIssue`, which is
 *  a row that could not be applied at all. */
export interface AdcWarning {
  row: number;
  record_type: string;
  message: string;
}

export interface AdcPreview {
  additions: AdcOp[];
  changes: AdcOp[];
  /** Terminations the file STATES, via a past leaving date on the row. */
  deletions: AdcOp[];
  /**
   * On file but named nowhere in the upload. Not a deletion — a partial export
   * looks identical to a full census that dropped people, so these terminate
   * only on the broker's explicit tick. `counts.roster_total` is the
   * denominator for "is this a partial file?".
   */
  missing: AdcOp[];
  issues: AdcIssue[];
  /** Applied anyway — advisory. Rendered apart from `issues` so an advisory
   *  note is never read as a refusal. */
  warnings: AdcWarning[];
  counts: Record<string, number>;
  /** Fingerprint of `missing`, returned with apply so a roster that moved in
   *  between can't terminate a different set than the one reviewed. */
  missing_digest: string | null;
}

export interface AdcApplyResult {
  added: number;
  changed: number;
  deleted: number;
  missing_terminated: number;
  unchanged: number;
  rematched: number;
  issues: AdcIssue[];
  flex_errors: string[];
}

// Vertex/Gemini is the sole provider (AWS Bedrock + Anthropic were removed).
export type AIProvider = "vertex";

export interface AIConfig {
  provider: AIProvider;
  // `endpoint` is the GCP location; `model` is the Gemini model id.
  endpoint: string | null;
  model: string | null;
  key_masked: string;
  key_fingerprint: string;
  last_validated_at: string | null;
  last_validation_error: string | null;
}

export interface AIConfigUpsert {
  provider?: AIProvider;
  // `endpoint` = GCP location; `api_key` = the service-account JSON key.
  endpoint?: string | null;
  model?: string | null;
  api_key: string;
}

export interface AIConfigTestPayload {
  provider?: AIProvider | null;
  endpoint?: string | null;
  model?: string | null;
  api_key?: string | null;
}

export interface AIConfigTestResult {
  ok: boolean;
  error: string | null;
  latency_ms: number;
}

// ── Product setup templates (guided product configuration) ──────────────────

export interface TemplateField {
  id: string;
  label: string;
  type: "text" | "textarea" | "choice" | "number" | "multichoice" | "taglist";
  // Selectable options for `multichoice` fields. `multichoice` and `taglist`
  // both persist their value as a comma-joined string in answers.<section>.
  options?: string[] | null;
}

export interface TemplateSubItem {
  key: string;
  name: string;
  kind?: BenefitKind;
  // Optional default pre-fill carried by a hand-authored file template (e.g. a
  // carrier-standard schedule the slip references but doesn't reproduce). Blank
  // on slip-synthesized templates.
  value?: string;
  note?: string | null;
}

/** Every benefit value type, as a RUNTIME list.
 *
 * The union below is derived from it rather than the other way round, because
 * `kind` arrives from the server as a plain string (`benefit_schedule` is
 * untyped JSON) and has to be narrowed at runtime before the formatter trusts
 * it. A hand-kept second copy of these names in the narrowing code compiled
 * fine and silently dropped any kind added here — the value then rendered as a
 * bare number ("3000" for "S$3,000") on the row a member decides cover on. */
export const BENEFIT_KINDS = [
  "amount",
  "currency",
  "percent",
  "text",
  "days",
  "boolean",
  "copay",
  "list",
  "scale",
  "group",
] as const;

export type BenefitKind = (typeof BENEFIT_KINDS)[number];

export interface TemplateBenefitItem {
  number: string;
  name: string;
  kind: BenefitKind;
  // Optional default pre-fill (see TemplateSubItem). Pre-fills every plan and
  // stays editable; blank on slip-synthesized templates.
  value?: string;
  note?: string | null;
  sub_items: TemplateSubItem[];
}

export interface TemplatePlan {
  code: string;
  label: string;
  default_selected: boolean;
}

export interface TemplateTier {
  code: string;
  label: string;
}

export interface TemplateArrangement {
  id: string;
  label: string;
  default_enabled: boolean;
}

export interface RateCell {
  rate: number;
  premium: number;
}

export type FormProfile =
  | "tiered_medical"
  | "outpatient"
  | "dental"
  | "sum_assured"
  | "accident"
  | "travel"
  | "statutory";

// Basis-of-Cover column shape + Rate-table shape (mirrors backend form_profiles.py).
export type BasisModel = "tiered" | "per_member" | "sum_assured";
export type RateModel =
  | "tiered"
  | "per_member"
  | "per_1000_si"
  | "flat" // GBT travel: one flat annual policy premium
  | "earnings_based"; // WICA statutory: rate × estimated annual earnings

export interface ProductTemplate {
  code: string;
  version: number;
  display_name: string;
  has_dependants: boolean;
  is_outpatient: boolean;
  participation_model: "standard" | "extended" | "eo_only";
  form_profile: FormProfile;
  basis_model: BasisModel;
  rate_model: RateModel;
  // Second SOB value axis (e.g. dental ["Panel","Non-Panel"]); empty = per-plan.
  column_axis: string[];
  sections: string[];
  header_fields: TemplateField[];
  eligibility_fields: TemplateField[];
  plans: TemplatePlan[];
  tiers: TemplateTier[];
  benefit_items: TemplateBenefitItem[];
  additional_arrangements: TemplateArrangement[];
}

// Free-text suggestions for the setup form, read live from this client's prior
// confirmed setups — the dynamic replacement for hardcoded choice lists.
export interface FieldSuggestions {
  header: Record<string, string[]>;
  eligibility: Record<string, string[]>;
  participation: string[];
  cover_description: string[];
}

// A product the broker can set up: slip-detected (structure synthesized from the
// uploaded slip) and/or backed by a hand-authored template file.
export interface SetupProductSummary {
  code: string;
  display_name: string;
  has_template_file: boolean;
  has_slip_data: boolean;
  line: InsuranceLine;
  // True when the client has its own catalog row for this code (added by the
  // user or created from a slip) — vs a bare global recognition row.
  is_client_product: boolean;
  // Structural classification (registry + product_metadata override).
  form_profile?: string | null;
  layout_family?: string | null;
  has_dependants?: boolean;
  // The client Product row id when one exists — the classification PATCH target.
  product_id?: string | null;
}

// Answer shape persisted in ProductSetup.answers and projected to Plan rows.
// `uid` is a stable client-only key for React reconciliation; `default_value`
// is the template's standard value, used to flag edited cells without indexing
// back into the (possibly reordered) template. Both are ignored by the backend.
export interface BenefitSubItemAnswer {
  uid: string;
  key: string;
  name: string;
  value: string | null;
  // Per-plan footnote (e.g. "Include Implants"). Plan-specific because a slip
  // often annotates only some plans (e.g. bargainable-employee variants).
  note?: string | null;
  // Qualifier rows ("Maximum no. of days") — label is shared structure, value
  // is per-plan.
  limits?: BenefitLimit[];
  kind?: BenefitKind;
}

export interface BenefitItemAnswer {
  uid: string;
  number: string;
  name: string;
  // Drives the SOB renderer (amount/text input, Yes/No, condition list, …).
  // Carried on the answer so it survives line add/remove reordering.
  kind?: BenefitKind;
  value: string | null;
  default_value: string | null;
  note?: string | null;
  limits?: BenefitLimit[];
  properties: Record<string, string>;
  sub_items: BenefitSubItemAnswer[];
}

export interface PlanAnswer {
  code: string;
  label: string;
  selected: boolean;
  // The slip's verbatim Schedule-of-Benefits column header for this plan
  // ("PLAN 1/U01/U04/U06"). Present only when the sheet had per-plan columns
  // AND the header names this plan's code; absent on descriptive layouts and
  // manually-built drafts. Drives the SOB column label — see columnLabel().
  source_label?: string | null;
  // Legacy per-plan SOB grid. Superseded by SetupAnswers.sob (decoupled benefit
  // columns); retained optional so pre-`sob` drafts still load + migrate. New
  // drafts carry only code/label/selected here.
  benefit_items?: BenefitItemAnswer[];
}

// ── Schedule of Benefits — decoupled benefit columns ────────────────────────
// The SOB is stored ONCE as a shared row skeleton (`items`) plus a small set of
// `columns` (the genuinely-varying benefit levels), instead of replicating the
// whole grid into every basis-of-cover plan. Each column maps to ≥1 plan code.
// A life/CI product with 22 sum-insured tiers collapses to one "All plans"
// column; GHS keeps its 4 real columns. Effective cell value for (item, column)
// = overrides[columnId] ?? base_value (the sentinel NOT_COVERED marks a per-
// column exclusion). Mirrors the enrollment "default + sparse override" model.

export interface SobColumn {
  id: string;
  label: string;
  // Basis-of-cover plan codes that receive this benefit level.
  plan_codes: string[];
}

export interface SobSubItemAnswer {
  uid: string;
  key: string;
  name: string;
  note?: string | null;
  limits?: BenefitLimit[];
  kind?: BenefitKind;
  base_value: string | null;
  // Sparse per-column deviation from base_value, keyed by SobColumn.id.
  overrides: Record<string, string | null>;
}

export interface SobItemAnswer {
  uid: string;
  number: string;
  name: string;
  kind?: BenefitKind;
  note?: string | null;
  limits?: BenefitLimit[];
  // Shared default + sparse per-column override (keyed by SobColumn.id).
  base_value: string | null;
  overrides: Record<string, string | null>;
  // Plan-independent axis values (dental Panel/Non-Panel), shared across columns.
  properties: Record<string, string>;
  // Per-column properties (outpatient copay: per_visit / co_payment / per_year),
  // keyed by SobColumn.id → {field → value}.
  column_properties?: Record<string, Record<string, string>>;
  sub_items: SobSubItemAnswer[];
}

export interface SobSchedule {
  columns: SobColumn[];
  items: SobItemAnswer[];
}

export interface BasisOfCoverRow {
  id: string;
  // Legal entities this row covers, as TOKENS — one element per entity, so a
  // registered name containing a comma stays one entity. Empty = every entity.
  insured: string[];
  category: string;
  participation: "compulsory" | "voluntary" | "";
  plan_code: string;
  // Per-tier headcounts for tiered (medical) products. Untiered products have
  // no tier columns and carry their headcount in `num_employees` instead.
  tiers: Record<string, number>;
  num_employees?: number;
  // Sum-assured products (life/accident) carry the cover amount + its basis
  // (flat / "12x monthly salary" / "% of GTL"). Null/absent for other models.
  sum_insured?: number | null;
  basis?: string | null;
}

// Live "members matched" preview for the Basis-of-Cover form. One row per
// requested draft category, keyed by the row's client-side id.
export interface MemberCountRow {
  key: string;
  employees: number;
  dependants: number;
}

export interface MemberCounts {
  counts: MemberCountRow[];
  employees_total: number;
  employees_matched: number;
  has_dependants: boolean;
}

export interface SetupAnswers {
  // Mostly free-text slip fields, plus `entities` — a token list of the legal
  // entities this product covers, which IS the employee-matching gate (the
  // free-text `insured` beside it is slip wording only).
  header: Record<string, string | string[]>;
  eligibility: Record<string, string>;
  participation: string;
  cover_description: string;
  plans: PlanAnswer[];
  // Decoupled Schedule of Benefits (benefit columns + shared rows). Optional for
  // back-compat: pre-`sob` drafts carry the grid in plans[].benefit_items and are
  // migrated on load. New/confirmed drafts always populate this.
  sob?: SobSchedule;
  rate_table: Record<string, Record<string, RateCell>>;
  categories: BasisOfCoverRow[];
  arrangements: Record<string, boolean>;
}

export interface ProductSetup {
  id: string;
  policy_year_id: string;
  product_code: string;
  template_version: number;
  answers: SetupAnswers;
  status: "draft" | "confirmed";
  origin: "manual" | "placement_slip";
  confirmed_at: string | null;
  materialized_product_id: string | null;
}

export interface ConfirmSetupResult {
  product_id: string;
  product_code: string;
  plans_created: number;
  plans_updated: number;
  plans_removed: number;
  categories_created: number;
  categories_removed: number;
  /** True when confirm re-ran employee matching (absent on older backends). */
  rematched?: boolean;
  employees_matched?: number;
}

// ── Flexible Benefits (Flex) ────────────────────────────────────────────────

/** Canonical family-status codes — must match the backend `family_status`
 *  attribute enum (single / married / +1 / +2 / +3-or-more children). */
export type FamilyStatusCode = "S" | "M" | "M1C" | "M2C" | "M3C";

export const FAMILY_STATUS_LABELS: Record<FamilyStatusCode, string> = {
  S: "Single",
  M: "Married",
  M1C: "Married + 1 child",
  M2C: "Married + 2 children",
  M3C: "Married + 3 or more children",
};

export interface FlexMeta {
  scheme_name?: string;
  /** Default currency; tiers may override per country. */
  currency?: string;
  system_cap?: number | null;
  tax_treatment?: string | null;
  lapse_proration?: string | null;
  /** Effective period (ISO dates); blank inherits the policy year window. */
  effective_start?: string | null;
  effective_end?: string | null;
  /** Raw amounts are GST-exclusive; when true, flex price tags gross up by
   *  gst_rate (platform default 9%). */
  gst_included?: boolean | null;
  gst_rate?: number | null;
  /** Scheme-wide dependant eligibility age windows (age next-birthday), the
   *  default for every product; a product's Flex-pricing entry overrides it.
   *  Dependants past the max draw no coverage and no flex. */
  dependant_age_limits?: {
    spouse?: { min?: number | null; max?: number | null } | null;
    child?: { min?: number | null; max?: number | null } | null;
  } | null;
}

export interface FlexEmployeeType {
  /** Provenance: the eligibility text as the document stated it (read-only). */
  raw?: string;
  job_grade_min?: number | null;
  job_grade_max?: number | null;
  confirmed_only?: boolean | null;
  confidential_status?: string | null;
  /** Roster-anchored match sets: employees are tagged to this tier when their
   *  designation OR grade (raw roster value) is selected here (union). */
  match_designations?: string[];
  match_grades?: string[];
  /** Eligibility tokens the AI extracted but couldn't map to a roster value. */
  unresolved?: string[];
}

export interface FlexLimit {
  family_status: FamilyStatusCode;
  amount: number;
}

export interface FlexCostSharing {
  employer_pct?: number;
  employee_pct?: number;
  exceptions?: string[];
}

export interface FlexBenefitCategory {
  name: string;
  claimable: boolean;
  sub_limit?: number | null;
  note?: string | null;
}

export interface FlexTier {
  name: string;
  /** Country this tier applies to (matched by employee nationality at runtime). */
  country?: string | null;
  /** ISO 4217 currency; falls back to meta.currency. */
  currency?: string | null;
  /** Flat annual cap when the wallet is NOT keyed to family status (limits empty). */
  system_cap?: number | null;
  employee_type: FlexEmployeeType;
  limits: FlexLimit[];
  cost_sharing?: FlexCostSharing | null;
  benefit_categories: FlexBenefitCategory[];
}

export type EntitlementStart =
  | "date_of_hire"
  | "policy_year_start"
  | "confirmation_date";

/** How the annual allowance is scaled to the period a member was covered. */
export type ProrationBasis = "none" | "months_served" | "days_served";
/** Which end of the year the pro-ration applies to. */
export type ProrationAppliesTo = "leavers" | "joiners" | "both";

export interface FlexProration {
  basis?: ProrationBasis | null;
  applies_to?: ProrationAppliesTo | null;
  /**
   * Extracted from documents that state it, but deliberately NOT acted on: a
   * flex wallet pays up to the limit, so utilisation can never exceed the
   * allowance and there is no shortfall to recover.
   */
  leaver_recovery?: boolean | null;
}

export interface FlexEligibility {
  entitlement_start?: EntitlementStart | null;
  proration?: FlexProration | null;
}

export interface FlexSpouseDef {
  eligible?: boolean;
  age_limit?: number | null;
  documentation?: string[];
}

export interface FlexChildDef {
  eligible?: boolean;
  age_limit?: number | null;
  tertiary_age_limit?: number | null;
  conditions?: string[];
  documentation?: string[];
}

export interface FlexDependantDef {
  spouse?: FlexSpouseDef | null;
  child?: FlexChildDef | null;
  verification?: { children_required?: boolean } | null;
  note?: string | null;
}

export interface FlexSchemeBody {
  meta: FlexMeta;
  tiers: FlexTier[];
  eligibility?: FlexEligibility | null;
  dependant_def?: FlexDependantDef | null;
}

export interface FlexScheme {
  id: string;
  policy_year_id: string;
  status: "draft" | "confirmed";
  origin: "manual" | "upload";
  scheme: FlexSchemeBody;
  source_ref: string | null;
  confidence: number | null;
  confirmed_at: string | null;
}

// ── Flex membership (family-status counts from the employee + dependant lists) ─
export interface FlexTierHeadcount {
  name: string;
  country: string | null;
  currency: string | null;
  eligible: number;
  by_family_status: Partial<Record<FamilyStatusCode, number>>;
  wallet_by_family_status: Partial<Record<FamilyStatusCode, number | null>>;
}

export interface FlexEmployeeAssignment {
  employee_id: string;
  family_status: FamilyStatusCode | null;
  source: "dependants" | "roster" | "none";
  spouse_count: number;
  child_count: number;
  tier_name: string | null;
  currency: string | null;
  wallet_amount: number | null;
}

export interface FlexMembership {
  employees_total: number;
  /** Keyed by S/M/M1C/M2C/M3C plus "unknown". */
  family_status_counts: Record<string, number>;
  /** Keyed by how each status was resolved: dependants / roster / none. */
  source_counts: Record<string, number>;
  tiers: FlexTierHeadcount[];
  assignments: FlexEmployeeAssignment[];
  scheme_status: string | null;
  /** Active employees who matched no tier (no wallet). */
  ineligible_count: number;
  /** Unmatched headcount bucketed by the designation that didn't match a tier. */
  ineligible_designations: Record<string, number>;
  /** Active employees satisfying more than one reconciled tier (assigned to the
   *  first; the overlap is surfaced so the broker can tighten the tiers). */
  ambiguous_count: number;
  ambiguous_examples: {
    designation: string | null;
    grade: string | null;
    tiers: string[];
  }[];
}

// ── Flex coverage validation ("is anyone left out?") ──────────────────────────
export type CoverageBucketKey =
  | "no_family_status"
  | "not_in_any_tier"
  | "multiple_tiers"
  | "unclassified_relationship"
  | "outside_age_window"
  | "orphaned"
  | "inactive_link";

export interface CoverageRow {
  /** Employee staff id (null for an orphaned dependant). */
  staff_id: string | null;
  /** Employee name (or the dependant's own name for orphans). */
  name: string | null;
  /** Designation for employee rows; the dependant's name for dependant rows. */
  label: string | null;
  /** Human-readable reason / offending raw value. */
  detail: string;
}

export interface CoverageBucket {
  key: CoverageBucketKey;
  label: string;
  kind: "employee" | "dependant";
  count: number;
  /** Capped preview of who; the full list is in the .xlsx export. */
  rows: CoverageRow[];
  truncated: boolean;
}

export interface FlexCoverage {
  employees_total: number;
  employees_ok: number;
  dependants_total: number;
  dependants_ok: number;
  has_tiers: boolean;
  scheme_status: string | null;
  buckets: CoverageBucket[];
  preview_cap: number;
}

/** A distinct roster value with its headcount, for the match-set pickers. */
export interface VocabValue {
  value: string;
  count: number;
  /** Already selected by some tier's match set. Optional because "claimed" is a
   *  FLEX-TIER notion — a value may only belong to one tier — and means nothing
   *  to the roster filter bars, which reuse the same picker. The picker only
   *  ever reads it as a truthy flag. */
  claimed?: boolean;
}

/** Distinct employee-type + job-grade values present on the active roster. */
export interface RosterVocab {
  employees_total: number;
  designations: VocabValue[];
  grades: VocabValue[];
}

/**
 * Legal entities the Insured picker offers.
 *
 * `roster` values exist on the active roster — picking one guarantees the
 * matching gate lets those employees through. `known` values are named in the
 * configuration (a category's insured list or a setup header) but match NO
 * roster entity, so they are the reconciliation backlog; their `count` is 0.
 */
export interface EntityVocab {
  employees_total: number;
  roster: VocabValue[];
  known: (VocabValue & {
    /** The roster spelling this most likely means — powers one-click aliasing. */
    suggestion?: string | null;
  })[];
}

/** Outcome of persisting Flex wallets onto the roster (the assign endpoint). */
export interface FlexAssignResult {
  employees_total: number;
  employees_assigned: number;
  employees_with_status: number;
  by_tier: Record<string, number>;
  duration_ms: number;
}

// ── Utilization (claims usage vs limits — member portal + broker views) ───────

export interface UtilizationBucket {
  product_code: string | null;
  product_name: string | null;
  /** null = the product-level roll-up row. */
  benefit_key: string | null;
  /** Parsed numeric annual limit, when known. */
  limit: number | null;
  /** Verbatim limit text for display ("As charged", "S$650/day"). */
  limit_display: string | null;
  approved: number;
  /** In-flight claims — shown separately, never subtracted from remaining. */
  pending: number;
  remaining: number | null;
  claim_count: number;
  /** The claims `pending` was summed from. SERVED, never re-derived: "which
   *  statuses count as pending" is defined server-side by subtraction from the
   *  settled set, so a client-side filter drifts the day a status is added. */
  pending_claim_ids: string[];
  /** Claims against coverage no longer on the statement. */
  orphaned: boolean;
  /** True when a limit text existed but couldn't be parsed to a number, so
   *  the over-limit guard is inactive (absent on older backends). */
  limit_unparsed?: boolean;
}

export interface FlexCategoryUtilization {
  name: string;
  sub_limit: number | null;
  approved: number;
  pending: number;
  remaining: number | null;
}

export interface FlexUtilization {
  currency: string | null;
  wallet_amount: number | null;
  /**
   * Present only when the allowance was pro-rated to the member's cover period.
   * NARROWER than `FlexProrationLine` on purpose: `schemas/claims.FlexProration`
   * carries no period bounds, so typing this as the statement's shape would
   * promise two fields the utilization endpoints never send.
   */
  proration: Omit<FlexProrationLine, "period_start" | "period_end"> | null;
  price_tags_total: number | null;
  flex_balance: number | null;
  approved: number;
  pending: number;
  /** The claims `pending` was summed from. SERVED, never re-derived here: which
   *  statuses count as pending is defined by subtraction server-side
   *  (`utilization.PENDING_STATUSES`), so a mirrored copy would drift into
   *  offering a different set from the figure it sits under. */
  pending_claim_ids: string[];
  available: number | null;
  categories: FlexCategoryUtilization[];
}

export interface Utilization {
  policy_year_id: string;
  insured: UtilizationBucket[];
  flex: FlexUtilization | null;
}
