/**
 * Enrollment module — TanStack Query hooks + types.
 *
 * Kept in its own module (not the monolithic hooks.ts/types.ts) so the
 * enrollment surface stays self-contained. Query keys are scoped by the active
 * client id so a tenant switch reads a fresh cache; mutations invalidate by
 * prefix so list views and the benefit statement refresh.
 */
import {
  useMutation,
  useQuery,
  useQueryClient,
} from "@tanstack/react-query";
import { api } from "./client";
import { isNotFoundError } from "@/lib/errors";
import { useSession } from "@/stores/session";
import type { InsuranceLine, PlanFinancials, VoluntaryRateBand } from "@/types";

// Re-exported for existing importers — the canonical declarations live in
// types.ts (they were previously re-declared here, a drift risk).
export type { InsuranceLine, VoluntaryRateBand };

function useClientId(): string | null {
  return useSession((s) => s.activeClientId);
}

// ── Types ───────────────────────────────────────────────────────────────────

export type WindowType = "open" | "new_hire" | "life_event";
export type WindowStatus = "draft" | "open" | "closed";
export type DefaultBehavior = "deemed_keep_current" | "deemed_decline";
/** Where a product's flex price tag comes from: the placement slip's premium, or
 *  the manual portal matrix. Keyed per product on the window. */
export type FlexPriceSource = "slip" | "manual";
/** Company-wide drawdown rule: deduct the whole plan price tag, or only the
 *  upgrade/downgrade difference vs the member's default plan. */
export type FlexDrawdownRule = "full" | "on_change";

export interface EnrollmentWindow {
  id: string;
  policy_year_id: string;
  name: string;
  window_type: WindowType;
  opens_at: string;
  closes_at: string;
  status: WindowStatus;
  default_behavior: DefaultBehavior;
  allow_plan_change: boolean;
  allow_leave: boolean;
  allow_dependant_changes: boolean;
  product_scope: string[] | null;
  flex_price_source: Record<string, FlexPriceSource> | null;
  flex_drawdown_rule: FlexDrawdownRule;
  /** Whether elections may draw more flex than the member's wallet holds.
   *  Off, submit/confirm reject an overdrawn enrollment (409 flex_overdrawn). */
  allow_overdraft: boolean;
  created_by: string | null;
}

export interface WindowCreate {
  name: string;
  window_type: WindowType;
  opens_at: string;
  closes_at: string;
  default_behavior: DefaultBehavior;
  allow_plan_change: boolean;
  allow_leave: boolean;
  allow_dependant_changes: boolean;
  product_scope?: string[] | null;
  flex_price_source?: Record<string, FlexPriceSource> | null;
  flex_drawdown_rule?: FlexDrawdownRule;
  allow_overdraft?: boolean;
}

/** Partial window edit — only the sent fields change (server reads
 *  `model_fields_set`). Rejected on a closed window. */
export interface WindowPatch {
  name?: string;
  opens_at?: string;
  closes_at?: string;
  allow_leave?: boolean;
  allow_dependant_changes?: boolean;
  product_scope?: string[] | null;
  flex_price_source?: Record<string, FlexPriceSource> | null;
  flex_drawdown_rule?: FlexDrawdownRule;
  allow_overdraft?: boolean;
}

export interface WindowCloseSummary {
  confirmed: number;
  deemed_kept: number;
  deemed_declined: number;
  already: number;
}

/** Response from opening/syncing a window — the window plus how many new
 *  enrollments the sync actually created (0 = nothing new to sync). */
export interface WindowOpenResult {
  window: EnrollmentWindow;
  enrollments_created: number;
}

/** One tier's day caps. A null/absent field inherits the policy-level maximum,
 *  so this is a SPARSE override — same shape as every other override layer. */
export interface LeaveTierLimit {
  max_buy_days?: number | null;
  max_sell_days?: number | null;
}

/** Per-day buy/sell-leave price AND day caps, both keyed by the SAME employee
 *  grade/designation attribute — one value of it is a leave "tier". */
export interface LeaveRates {
  attribute: string | null;
  rates: Record<string, number | null>;
  limits?: Record<string, LeaveTierLimit>;
}

export interface LeavePolicy {
  id: string;
  policy_year_id: string;
  allow_buy: boolean;
  allow_sell: boolean;
  min_buy_days: number;
  max_buy_days: number;
  min_sell_days: number;
  max_sell_days: number;
  increment_days: number;
  leave_rates: LeaveRates | Record<string, never>;
  notes: string | null;
}

export type LeavePolicyUpsert = Omit<LeavePolicy, "id" | "policy_year_id">;

export interface LeaveRateValue {
  value: string;
  count: number;
}

/** Available grade/designation attributes + distinct roster values, for the rate grid. */
export interface LeaveRateOptions {
  attributes: string[];
  values: Record<string, LeaveRateValue[]>;
}

/** What ONE member may trade — the year's bounds plus their own eligibility, so
 *  the election UI can state the limit (and its dollar value) before saving. */
export interface MemberLeaveOptions {
  allow_buy: boolean;
  allow_sell: boolean;
  min_buy_days: number;
  max_buy_days: number;
  min_sell_days: number;
  max_sell_days: number;
  increment_days: number;
  /** Roster flag "Eligible to Sell Leave" — absent on the roster = eligible. */
  sell_eligible: boolean;
  /** The grade/designation the member's per-day rate + caps were looked up by. */
  rate_attribute: string | null;
  rate_value: string | null;
  /** True when the caps above are that tier's own, not the company default. */
  limits_from_tier: boolean;
}

export interface EnrollmentRosterItem {
  id: string;
  employee_id: string;
  staff_id: string;
  employee_name: string | null;
  status: string;
}

export interface EnrollmentRoster {
  items: EnrollmentRosterItem[];
  total: number;
  offset: number;
  limit: number;
}

export interface ElectionOut {
  product_id: string;
  product_code: string;
  previous_plan_code: string | null;
  elected_plan_code: string | null;
  tier_category_id: string | null;
  action: string;
  covered_dependant_ids: string[] | null;
  /** Elected freestanding dependant option LEVEL per role ({role: category_id}). */
  dependant_option_ids: Record<string, string> | null;
  /** Flex wallet amount deducted for this election (null = no flex price). */
  flex_price_tag: number | null;
  notes: string | null;
}

/** One electable tier within a member's cohort for a product. */
export interface CohortTier {
  /** Unique dropdown key = tier_category_id + plan_code (each can repeat alone). */
  key: string;
  tier_category_id: string;
  plan_code: string | null;
  label: string;
  participation: string | null;
  direction: "upgrade" | "downgrade" | "same" | "unknown";
  is_baseline: boolean;
  /** The tier the member HOLDS today — the cohort baseline unless a standing
   *  override moved them off it. This, not `is_baseline`, is "your current
   *  plan" and the anchor `differences` are measured from; they coincide for
   *  every member without an override. Resolved server-side (only the server
   *  can read the override), so never derive it here. */
  is_current: boolean;
  financials: PlanFinancials | null;
  /** Flex wallet cost of electing this tier for the member (null = none). */
  price_tag: number | null;
  /** Schedule rows on which this tier differs from the member's CURRENT plan —
   *  what they actually gain or give up. Empty on the baseline, and on products
   *  whose plans share one schedule (the life ones, where the only difference
   *  is the sum insured `financials` already carries). */
  differences: BenefitDifference[];
  /** Count before truncation, so a long list can say what it isn't showing. */
  differences_total: number;
}

/** One benefit row on which an electable tier differs from the baseline. */
export interface BenefitDifference {
  /** The parent benefit when this row is a sub-item ("Specialist Care"), kept
   *  separate so the UI can rank it rather than joining it into one long name. */
  group: string | null;
  /** The row's own headline, with the insurer's bracketed wording split off. */
  benefit: string;
  /** That bracketed wording — load-bearing, so placed rather than dropped. */
  qualifier: string | null;
  /** Verbatim schedule cells; `null` = the plan states nothing for the row. */
  current: string | null;
  elected: string | null;
  /** Value type, so figures format exactly as on the coverage tab. */
  kind: string | null;
}

/** How a product prices coverage for a member's dependants (additive over EO).
 *  "slip_options" = slip dependant option rows that stick to the elected
 *  employee plan (GPA "Spouse (Option N)"); each covered dependant draws the
 *  matching option's slip rate (age-banded rows priced per dependant server-side). */
export type DependantPricingMode =
  | "none"
  | "family_group"
  | "per_pax"
  | "slip_options";
/** Family-composition label scheme (family_group mode). */
export type FamilyScheme = "ec_es_ef" | "so_co_sc";
export type FamilyRole = "spouse" | "child" | "both";

/** One family-composition role's incremental flex cost over Employee-Only. */
export interface DependantRole {
  role: FamilyRole;
  label: string; // scheme label: ES/EC/EF or SO/CO/SC
  amount: number | null;
}

/** One plan/tier's dependant pricing (amounts differ per plan). */
export interface DependantTierPricing {
  family: DependantRole[];
  per_pax_rate: number | null;
}

/** One electable freestanding dependant option LEVEL (a dependant-scope slip
 *  row, e.g. GTL "Spouse — S$40,000"). */
export interface DependantOptionChoice {
  category_id: string;
  label: string;
  sum_insured: number | null;
  /** Flat per-dependant flex amount; null = age-banded (see amounts_by_dependant). */
  amount: number | null;
  /** Per-dependant resolved amounts for THIS member's dependants (age-banded
   *  levels price on each dependant's own age; null = unknown date of birth). */
  amounts_by_dependant: Record<string, number | null>;
}

export interface DependantOptionRole {
  role: "spouse" | "child";
  choices: DependantOptionChoice[];
}

/** A product's dependant pricing, keyed per plan/tier (null = unconfigured). */
export interface DependantPricing {
  mode: DependantPricingMode;
  scheme: FamilyScheme | null;
  /** Per-tier pricing keyed by tier key (tier_category_id::plan_code). */
  by_tier: Record<string, DependantTierPricing>;
  /** slip_options only: freestanding option LEVELS the member must elect per
   *  role (the slip states no employee-plan linkage). Empty when the option
   *  rows are linked — those price without an election. */
  option_choices: DependantOptionRole[];
}

export interface ProductTierSet {
  product_id: string;
  product_code: string;
  /** The product's own name. Served (not looked up client-side) so a member
   * surface can lead with words instead of a code — the portal's gloss map
   * only knows the codes someone has written a line for. */
  product_name: string | null;
  employee_participation: string | null;
  dependant_participation: string | null;
  baseline_tier_category_id: string;
  baseline_plan_code: string | null;
  allow_plan_change: boolean;
  can_decline: boolean;
  tiers: CohortTier[];
  /** Dependant pricing for this product (null when not configured). */
  dependant: DependantPricing | null;
}

export interface EnrollmentOptions {
  /** Null on the broker employee-view preview path (no Enrollment row exists). */
  enrollment_id: string | null;
  products: ProductTierSet[];
  /** Member's flex wallet + age so the UI can show the running flex balance. */
  flex_wallet: number | null;
  flex_currency: string | null;
  member_age: number | null;
  /** Per-day buy/sell-leave rate for this member (null = none), for a live balance. */
  member_leave_rate: number | null;
  /** Bounds this member trades leave within (null = the year has no leave policy). */
  leave: MemberLeaveOptions | null;
  /** The window's drawdown rule, so the UI can label each tier's price tag as the
   *  full plan cost or the upgrade/downgrade difference. */
  flex_drawdown_rule: FlexDrawdownRule;
}

// ── Flex price tags (per-policy-year matrix: tier × age band) ─────────────────

export interface FlexPricingTier {
  key: string;
  label: string;
  plan_code: string | null;
  direction: "upgrade" | "downgrade" | "same" | "unknown";
  is_baseline: boolean;
  /** Per-member slip premium for this tier (null = none), for the "from slip" preview. */
  slip_premium: number | null;
  /** Per-member sum insured (basis) — drives the life-product live preview. */
  sum_insured: number | null;
  /** The tier's own cohort (job-category) name, to disambiguate rows the UI can't
   *  fold (a plan repeating across cohorts that price differently). Null when none. */
  cohort_label?: string | null;
}

export type FlexPricingMode = "age_banded" | "plan_type";

/** Per-role dependant eligibility window; a dependant outside it isn't covered. */
export interface DependantAgeLimits {
  spouse?: { min?: number | null; max?: number | null };
  child?: { min?: number | null; max?: number | null };
}

export interface FlexPricingProduct {
  product_id: string;
  product_code: string;
  /** Insurance line — drives the Life/Medical label + age-banded vs tiered layout. */
  line: InsuranceLine;
  /** Default config shape from the product's insurance line (life → age_banded). */
  pricing_mode: FlexPricingMode;
  /** Age-banded voluntary rate table (life products), else null. */
  voluntary_rates: VoluntaryRateBand[] | null;
  /** Effective dependant eligibility windows (configured over defaults). */
  dependant_age_limits: DependantAgeLimits;
  tiers: FlexPricingTier[];
  /** Suggested dependant mode from the slip ("per_pax" when a per-dependant rate
   *  exists, "family_group" when EO/ES/EC/EF rates exist). */
  dependant_suggested_mode: DependantPricingMode;
  /** Slip-derived family increments, keyed by tier key → role → amount over EO. */
  slip_family: Record<string, Partial<Record<FamilyRole, number>>>;
  /** Slip-derived per-dependant rate, keyed by tier key → rate. */
  slip_per_pax: Record<string, number>;
}

/** Saved dependant config for a product, stored in the pricing bag. Family/per-pax
 *  amounts are keyed per tier (tier key) — the dependant tag differs per plan. */
export interface DependantConfig {
  mode?: DependantPricingMode;
  scheme?: FamilyScheme;
  family_tags?: Record<string, Partial<Record<FamilyRole, number | null>>>;
  per_pax?: Record<string, { flat?: number | null }>;
  /** Per-role dependant eligibility windows (life products); out-of-range → not covered. */
  age_limits?: DependantAgeLimits;
}

export interface FlexAgeBand {
  label: string;
  min: number | null;
  max: number | null;
}

/** One product's matrix: age bands + price tags keyed by tier key → band label. */
export interface FlexPricingProductBlock {
  age_bands: FlexAgeBand[];
  price_tags: Record<string, Record<string, number | null>>;
  /** Per-policy-year override of the product's default pricing_mode. */
  mode?: FlexPricingMode;
  /** Dependant pricing config (family-tier amounts or per-pax flat rate). */
  dependant?: DependantConfig;
}

export interface FlexPricingBag {
  products?: Record<string, FlexPricingProductBlock>;
}

export interface FlexPricing {
  policy_year_id: string;
  pricing: FlexPricingBag;
  /** Available products + their electable tiers, for rendering the grid rows. */
  products: FlexPricingProduct[];
}

export interface LeaveElectionOut {
  action: "none" | "buy" | "sell";
  days: number;
  status: string;
  /** Signed flex-wallet impact (buy spends, sell credits); null = unpriced. */
  flex_amount: number | null;
}

export interface EnrollmentDetail {
  id: string;
  window_id: string;
  policy_year_id: string;
  employee_id: string;
  staff_id: string;
  employee_name: string | null;
  status: string;
  baseline_snapshot: {
    products?: Record<
      string,
      {
        plan_code: string | null;
        tier_category_id?: string | null;
        declined: boolean;
        covered_dependant_ids: string[] | null;
        dependant_option_ids?: Record<string, string> | null;
        compulsory: boolean;
      }
    >;
  } | null;
  submitted_at: string | null;
  confirmed_at: string | null;
  elections: ElectionOut[];
  leave: LeaveElectionOut | null;
  /** Product codes whose category has participation_model='compulsory' for this member. */
  compulsory_product_codes: string[];
}

export interface ElectionIn {
  product_code: string;
  plan_code?: string | null;
  tier_category_id?: string | null;
  declined?: boolean;
  covered_dependant_ids?: string[] | null;
  /** Elected freestanding dependant option LEVEL per role ({role: category_id})
   *  — only for products whose options expose `option_choices`. */
  dependant_option_ids?: Record<string, string> | null;
  notes?: string | null;
}

export interface BulkRowOutcome {
  employee_id: string | null;
  staff_id: string | null;
  outcome: string;
  reason: string | null;
  from_plan: string | null;
  to_plan: string | null;
}

export interface BulkResult {
  rows: BulkRowOutcome[];
  counts: Record<string, number>;
}

export interface BulkApplyResult extends BulkResult {
  id: string;
  status: string;
}

export interface BulkRequest {
  product_code: string;
  action: "set_plan" | "decline";
  target_plan_code?: string | null;
  selector: {
    employee_ids?: string[];
    staff_ids?: string[];
    /** Cohort filter: every employee whose matched categories include this
     *  Category id (their baseline tier). */
    category_id?: string | null;
    /** Coverage filter: every employee whose EFFECTIVE plan for this
     *  request's product equals this plan code — selects "everyone currently
     *  on Plan A" without typing staff ids. */
    current_plan_code?: string | null;
  };
  dependant_action?: { mode: "include_all" | "exclude_all" | "set"; dependant_ids: string[] } | null;
}

// ── Windows ─────────────────────────────────────────────────────────────────

export function useEnrollmentWindows(policyYearId: string | undefined) {
  const cid = useClientId();
  return useQuery({
    queryKey: ["enrollment-windows", policyYearId, cid],
    queryFn: () =>
      api.get<EnrollmentWindow[]>(`/policy-years/${policyYearId}/enrollment-windows`),
    enabled: !!policyYearId,
  });
}

export function useCreateWindow(policyYearId: string | undefined) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: WindowCreate) =>
      api.post<EnrollmentWindow>(
        `/policy-years/${policyYearId}/enrollment-windows`,
        body,
      ),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["enrollment-windows"] }),
  });
}

/** Partial window edit. Used by the Price Tag tab to write the per-product
 *  price-tag source onto every still-editable window (a closed one is history —
 *  the server 409s it). */
export function useUpdateWindow() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ id, body }: { id: string; body: WindowPatch }) =>
      api.patch<EnrollmentWindow>(`/enrollment-windows/${id}`, body),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["enrollment-windows"] });
      // The price-tag source decides what each plan draws from the wallet, so
      // every surface that prices coverage has to re-read.
      qc.invalidateQueries({ queryKey: ["enrollment-options"] });
      qc.invalidateQueries({ queryKey: ["benefit-statement"] });
    },
  });
}

export function useDeleteWindow() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => api.delete<void>(`/enrollment-windows/${id}`),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["enrollment-windows"] }),
  });
}

export function useOpenWindow() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: string) =>
      api.post<WindowOpenResult>(`/enrollment-windows/${id}/open`, {}),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["enrollment-windows"] });
      qc.invalidateQueries({ queryKey: ["enrollments"] });
    },
  });
}

export function useCloseWindow() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: string) =>
      api.post<WindowCloseSummary>(`/enrollment-windows/${id}/close`, {}),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["enrollment-windows"] });
      qc.invalidateQueries({ queryKey: ["enrollments"] });
      qc.invalidateQueries({ queryKey: ["benefit-statement"] });
      qc.invalidateQueries({ queryKey: ["coverage-summary"] });
      // Close projects elections into overrides → history + override views change.
      qc.invalidateQueries({ queryKey: ["coverage-history"] });
      qc.invalidateQueries({ queryKey: ["plan-overrides"] });
    },
  });
}

// ── Leave policy ─────────────────────────────────────────────────────────────

export function useLeavePolicy(policyYearId: string | undefined) {
  const cid = useClientId();
  return useQuery({
    queryKey: ["leave-policy", policyYearId, cid],
    queryFn: () =>
      api
        .get<LeavePolicy>(`/policy-years/${policyYearId}/leave-policy`)
        .catch((e: unknown) => {
          // Only an unset policy (404) maps to null — real failures must surface.
          if (isNotFoundError(e)) return null;
          throw e;
        }),
    enabled: !!policyYearId,
  });
}

export function useUpsertLeavePolicy(policyYearId: string | undefined) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: LeavePolicyUpsert) =>
      api.put<LeavePolicy>(`/policy-years/${policyYearId}/leave-policy`, body),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["leave-policy"] });
      // Leave rates change the snapshotted flex impact on new elections.
      qc.invalidateQueries({ queryKey: ["benefit-statement"] });
    },
  });
}

export function useLeaveRateOptions(policyYearId: string | undefined) {
  const cid = useClientId();
  return useQuery({
    queryKey: ["leave-rate-options", policyYearId, cid],
    queryFn: () =>
      api.get<LeaveRateOptions>(`/policy-years/${policyYearId}/leave-rate-options`),
    enabled: !!policyYearId,
  });
}

// ── Flex pricing (price-tag matrix) ──────────────────────────────────────────

export function useFlexPricing(policyYearId: string | undefined) {
  const cid = useClientId();
  return useQuery({
    queryKey: ["flex-pricing", policyYearId, cid],
    queryFn: () => api.get<FlexPricing>(`/policy-years/${policyYearId}/flex-pricing`),
    enabled: !!policyYearId,
  });
}

export function useSaveFlexPricing(policyYearId: string | undefined) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (pricing: FlexPricingBag) =>
      api.put<FlexPricing>(`/policy-years/${policyYearId}/flex-pricing`, { pricing }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["flex-pricing"] });
      // Price tags surface on the elections options + benefit statement.
      qc.invalidateQueries({ queryKey: ["enrollment-options"] });
      qc.invalidateQueries({ queryKey: ["benefit-statement"] });
    },
  });
}

// ── Enrollments ──────────────────────────────────────────────────────────────

export function useEnrollmentRoster(
  windowId: string | undefined,
  opts: { offset?: number; limit?: number; q?: string; status?: string } = {},
) {
  const cid = useClientId();
  const { offset = 0, limit = 50, q, status } = opts;
  return useQuery({
    queryKey: ["enrollments", windowId, offset, limit, q, status, cid],
    queryFn: () => {
      const params = new URLSearchParams({ offset: String(offset), limit: String(limit) });
      if (q) params.set("q", q);
      if (status) params.set("status", status);
      return api.get<EnrollmentRoster>(
        `/enrollment-windows/${windowId}/enrollments?${params}`,
      );
    },
    enabled: !!windowId,
  });
}

export function useEnrollment(enrollmentId: string | null) {
  const cid = useClientId();
  return useQuery({
    queryKey: ["enrollment", enrollmentId, cid],
    queryFn: () => api.get<EnrollmentDetail>(`/enrollments/${enrollmentId}`),
    enabled: !!enrollmentId,
  });
}

/** Cohort-scoped, direction-aware electable tiers per product for one member. */
export function useEnrollmentOptions(enrollmentId: string | null) {
  const cid = useClientId();
  return useQuery({
    queryKey: ["enrollment-options", enrollmentId, cid],
    queryFn: () => api.get<EnrollmentOptions>(`/enrollments/${enrollmentId}/options`),
    enabled: !!enrollmentId,
  });
}

function invalidateEnrollment(qc: ReturnType<typeof useQueryClient>) {
  qc.invalidateQueries({ queryKey: ["enrollment"] });
  qc.invalidateQueries({ queryKey: ["enrollments"] });
  qc.invalidateQueries({ queryKey: ["benefit-statement"] });
  qc.invalidateQueries({ queryKey: ["coverage-summary"] });
  qc.invalidateQueries({ queryKey: ["coverage-history"] });
}

export function useSetElections() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ id, elections }: { id: string; elections: ElectionIn[] }) =>
      api.put<EnrollmentDetail>(`/enrollments/${id}/elections`, { elections }),
    onSuccess: () => invalidateEnrollment(qc),
  });
}

export function useSetLeave() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ id, action, days }: { id: string; action: string; days: number }) =>
      api.put<EnrollmentDetail>(`/enrollments/${id}/leave`, { action, days }),
    onSuccess: () => invalidateEnrollment(qc),
  });
}

export function useSubmitEnrollment() {
  const qc = useQueryClient();
  return useMutation({
    // acknowledgeUnpriced: deliberate re-submit after the server flagged
    // changed-but-unpriced elections (409 code "unpriced_elections").
    mutationFn: ({
      id,
      acknowledgeUnpriced = false,
    }: {
      id: string;
      acknowledgeUnpriced?: boolean;
    }) =>
      api.post<EnrollmentDetail>(`/enrollments/${id}/submit`, {
        acknowledge_unpriced: acknowledgeUnpriced,
      }),
    onSuccess: () => invalidateEnrollment(qc),
    // The election panel handles submit errors itself (overdraft toast /
    // unpriced-elections dialog) — skip the global error toast.
    meta: { localErrorHandling: true },
  });
}

export function useConfirmEnrollment() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => api.post<EnrollmentDetail>(`/enrollments/${id}/confirm`, {}),
    onSuccess: () => invalidateEnrollment(qc),
  });
}

/** Reopen a confirmed enrollment (window still open) so plans can change again. */
export function useReopenEnrollment() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => api.post<EnrollmentDetail>(`/enrollments/${id}/reopen`, {}),
    onSuccess: () => invalidateEnrollment(qc),
  });
}

// ── Coverage history + revert (track / reset flexibility) ────────────────────

export interface CoverageHistoryEntry {
  id: string;
  at: string;
  action: string;
  label: string;
  actor: string | null;
  product_code: string | null;
  from_plan: string | null;
  to_plan: string | null;
  declined: boolean | null;
}

export interface CoverageHistory {
  employee_id: string;
  entries: CoverageHistoryEntry[];
  /** Whether a window baseline exists — gates the 'Revert to baseline' control. */
  has_baseline: boolean;
}

export interface CoverageChange {
  product_code: string;
  outcome: "reverted" | "reset_to_default" | "unchanged" | "skipped";
  from_plan: string | null;
  to_plan: string | null;
  detail: string | null;
}

export interface CoverageRevertRequest {
  target: "baseline" | "default";
  product_codes?: string[] | null;
  window_id?: string | null;
}

export interface CoverageRevertResult {
  employee_id: string;
  target: string;
  changes: CoverageChange[];
}

export function useCoverageHistory(employeeId: string | null) {
  const cid = useClientId();
  return useQuery({
    queryKey: ["coverage-history", employeeId, cid],
    queryFn: () =>
      api.get<CoverageHistory>(`/employees/${employeeId}/coverage-history`),
    enabled: !!employeeId,
  });
}

export function useRevertCoverage(employeeId: string | undefined) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: CoverageRevertRequest) =>
      api.post<CoverageRevertResult>(`/employees/${employeeId}/coverage/revert`, body),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["coverage-history"] });
      qc.invalidateQueries({ queryKey: ["plan-overrides"] });
      qc.invalidateQueries({ queryKey: ["benefit-statement"] });
      qc.invalidateQueries({ queryKey: ["coverage-summary"] });
      // The revert controls live inside the elections panel; refresh the enrollment
      // + its options so the tier markers and price tags there aren't left stale.
      qc.invalidateQueries({ queryKey: ["enrollment"] });
      qc.invalidateQueries({ queryKey: ["enrollment-options"] });
    },
  });
}

/** Discard a member's in-progress elections (window must still accept edits). */
export function useResetEnrollment() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => api.post<EnrollmentDetail>(`/enrollments/${id}/reset`, {}),
    onSuccess: () => {
      invalidateEnrollment(qc);
      qc.invalidateQueries({ queryKey: ["coverage-history"] });
    },
  });
}

// ── Plan overrides (orphan reconciliation) ───────────────────────────────────

/** One EmployeePlanOverride row (mirrors backend PlanOverrideOut). */
export interface PlanOverride {
  id: string;
  employee_id: string;
  policy_year_id: string;
  product_id: string;
  product_code: string;
  plan_code: string | null;
  declined: boolean;
  covered_dependant_ids: string[] | null;
  dependant_option_ids: Record<string, string> | null;
  source: string;
  source_ref: string | null;
  effective_from: string | null;
  modified_by: string | null;
}

/** Overrides stranded by a re-match — the product is no longer in the
 *  employee's cohort. Inert (the resolver skips them), surfaced for cleanup. */
export function useOrphanOverrides(policyYearId: string | undefined) {
  const cid = useClientId();
  return useQuery({
    queryKey: ["plan-overrides", "orphans", policyYearId, cid],
    queryFn: () =>
      api.get<PlanOverride[]>(
        `/policy-years/${policyYearId}/plan-overrides/orphans`,
      ),
    enabled: !!policyYearId,
  });
}

/** Remove one override so the employee reverts to their category default. */
export function useDeletePlanOverride() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({
      employeeId,
      productCode,
    }: {
      employeeId: string;
      productCode: string;
    }) =>
      api.delete<void>(
        `/employees/${employeeId}/plan-overrides/${encodeURIComponent(productCode)}`,
      ),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["plan-overrides"] });
      qc.invalidateQueries({ queryKey: ["benefit-statement"] });
      qc.invalidateQueries({ queryKey: ["coverage-summary"] });
    },
  });
}

// ── Bulk plan update ─────────────────────────────────────────────────────────

export function usePreviewBulk(policyYearId: string | undefined) {
  return useMutation({
    mutationFn: (body: BulkRequest) =>
      api.post<BulkResult>(`/policy-years/${policyYearId}/bulk-plan-updates/preview`, body),
  });
}

export function useApplyBulk(policyYearId: string | undefined) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: BulkRequest) =>
      api.post<BulkApplyResult>(`/policy-years/${policyYearId}/bulk-plan-updates/apply`, body),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["benefit-statement"] });
      qc.invalidateQueries({ queryKey: ["coverage-summary"] });
      // Bulk updates rewrite overrides directly — refresh everything that
      // renders an employee's effective coverage.
      qc.invalidateQueries({ queryKey: ["enrollment"] });
      qc.invalidateQueries({ queryKey: ["enrollment-options"] });
      qc.invalidateQueries({ queryKey: ["coverage-history"] });
      qc.invalidateQueries({ queryKey: ["plan-overrides"] });
    },
  });
}
