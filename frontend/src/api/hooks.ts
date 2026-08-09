import {
  type UseMutationOptions,
  useMutation,
  useQuery,
  useQueryClient,
} from "@tanstack/react-query";
import { api } from "./client";
import { fetchMe, meQueryKey } from "./me";
import { isNotFoundError } from "@/lib/errors";
import { useSession } from "@/stores/session";
import type {
  AIConfig,
  AIConfigTestPayload,
  AIConfigTestResult,
  AIConfigUpsert,
  ApplyAttributeItem,
  ApplyConfigResult,
  ApplyProductItem,
  AttributeSchema,
  AuditLogPage,
  BenefitStatement,
  Category,
  CategoryGroup,
  CoverageSummary,
  ConfigRecommendation,
  AutoMatchResult,
  Dependant,
  Employee,
  FieldSuggestions,
  FlexAssignResult,
  FlexScheme,
  FlexSchemeBody,
  FlexMembership,
  FlexCoverage,
  EntityVocab,
  RosterVocab,
  InsuranceLine,
  MatchResults,
  MatchRunResult,
  MemberCounts,
  ConfirmSetupResult,
  ParseResult,
  PlacementSlipSummary,
  PlanDetail,
  PlanList,
  PolicyYear,
  Product,
  ProductSetup,
  ProductTemplate,
  ProductTerm,
  SetupAnswers,
  SetupProductSummary,
  SlipTemplateProfileSave,
} from "@/types";

// ── Queries ─────────────────────────────────────────────────────────────────

/**
 * Active-client id used to scope tenant query caches. Appended (never prepended)
 * to every tenant-scoped query key so a client switch reads a fresh cache entry
 * — never the previous tenant's data during the refetch window — while keeping
 * the leading segments intact so mutations can still invalidate by prefix.
 */
function useActiveClientId(): string | null {
  return useSession((s) => s.activeClientId);
}

export function usePolicyYears() {
  const cid = useActiveClientId();
  return useQuery({
    queryKey: ["policy-years", cid],
    queryFn: () => api.get<PolicyYear[]>("/policy-years"),
  });
}

// Identity types + the query key live in api/me.ts so the router guard (which
// resolves /me before the shell renders) shares them.
export type { AccessibleClient, MeResponse } from "./me";

export function useMe() {
  const activeClientId = useActiveClientId();
  return useQuery({
    queryKey: meQueryKey(activeClientId),
    queryFn: fetchMe,
    staleTime: 60_000,
  });
}

// ── Firm Home dashboard ────────────────────────────────────────────────────
export interface CompanyYear {
  id: string;
  year: number;
  status: string;
}

export interface CompanySummary {
  id: string;
  name: string;
  current_year: CompanyYear | null;
  member_count: number;
  dependant_count: number;
  claims_to_review: number;
  dependants_pending: number;
  employees_unmatched: number;
  matching_stale: boolean;
  underwriting_pending: number;
  enrollment_open: boolean;
  enrollment_closes_at: string | null;
}

export interface FirmTotals {
  company_count: number;
  member_count: number;
  dependant_count: number;
  claims_to_review: number;
  dependants_pending: number;
  employees_unmatched: number;
  underwriting_pending: number;
  windows_open: number;
}

export interface DashboardSummary {
  firm: FirmTotals;
  companies: CompanySummary[];
}

/**
 * Firm-level roll-up powering the Home page. Scoped server-side to the caller's
 * accessible clients (same firm boundary as everything else), so it's NOT keyed
 * by the active client — it aggregates across companies regardless of selection.
 */
export function useDashboardSummary() {
  return useQuery({
    queryKey: ["dashboard-summary"],
    queryFn: () => api.get<DashboardSummary>("/dashboard/summary"),
    staleTime: 30_000,
  });
}

export function useEmployeeAttributes() {
  const cid = useActiveClientId();
  return useQuery({
    queryKey: ["schemas", "employee-attributes", cid],
    queryFn: () => api.get<AttributeSchema[]>("/schemas/employee-attributes"),
  });
}

export function useProducts() {
  const cid = useActiveClientId();
  return useQuery({
    queryKey: ["schemas", "products", cid],
    queryFn: () => api.get<Product[]>("/schemas/products"),
  });
}

export function useCategoriesGrouped(policyYearId: string | undefined) {
  const cid = useActiveClientId();
  return useQuery({
    queryKey: ["categories", "grouped", policyYearId, cid],
    queryFn: () =>
      api.get<CategoryGroup[]>(
        `/categories/grouped?policy_year_id=${policyYearId}`,
      ),
    enabled: Boolean(policyYearId),
  });
}

/** `includeLeft` is part of the KEY, not just the URL — the two rosters are
 *  different responses and a shared key served the cached active-only list
 *  under the toggle that had just asked for leavers. */
export function useCoverageSummary(
  policyYearId: string | undefined,
  includeLeft = false,
) {
  const cid = useActiveClientId();
  return useQuery({
    queryKey: ["coverage-summary", policyYearId, cid, includeLeft],
    queryFn: () =>
      api.get<CoverageSummary>(
        `/employees/coverage-summary?policy_year_id=${policyYearId}` +
          (includeLeft ? "&include_left=true" : ""),
      ),
    enabled: Boolean(policyYearId),
  });
}

export function useEmployee(employeeId: string | null) {
  const cid = useActiveClientId();
  return useQuery({
    queryKey: ["employee", employeeId, cid],
    queryFn: () => api.get<Employee>(`/employees/${employeeId}`),
    enabled: Boolean(employeeId),
  });
}

export function useBenefitStatement(employeeId: string | null) {
  const cid = useActiveClientId();
  return useQuery({
    queryKey: ["benefit-statement", employeeId, cid],
    queryFn: () =>
      api.get<BenefitStatement>(`/employees/${employeeId}/benefit-statement`),
    enabled: Boolean(employeeId),
  });
}

export function useAutoMatchDependants() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (policyYearId: string) =>
      api.post<AutoMatchResult>(
        `/dependants/auto-match?policy_year_id=${policyYearId}`,
        {},
      ),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["dependants"] });
      qc.invalidateQueries({ queryKey: ["flex-membership"] });
      qc.invalidateQueries({ queryKey: ["flex-coverage"] });
      qc.invalidateQueries({ queryKey: ["audit-log"] });
    },
  });
}

export function useMatchResults(
  policyYearId: string | undefined,
  offset = 0,
  limit = 50,
) {
  const cid = useActiveClientId();
  return useQuery({
    queryKey: ["match-results", policyYearId, offset, limit, cid],
    queryFn: () => {
      const params = new URLSearchParams({
        policy_year_id: policyYearId ?? "",
        offset: String(offset),
        limit: String(limit),
      });
      return api.get<MatchResults>(`/match-results?${params}`);
    },
    enabled: Boolean(policyYearId),
  });
}

export function useRunMatching() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (policyYearId: string) =>
      api.post<MatchRunResult>(
        `/match-results/run?policy_year_id=${policyYearId}`,
        {},
      ),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["match-results"] });
      qc.invalidateQueries({ queryKey: ["employees"] });
      qc.invalidateQueries({ queryKey: ["flex-membership"] });
      qc.invalidateQueries({ queryKey: ["flex-coverage"] });
      qc.invalidateQueries({ queryKey: ["benefit-statement"] });
      qc.invalidateQueries({ queryKey: ["audit-log"] });
    },
  });
}

export function useAuditLog(entityType?: string) {
  const cid = useActiveClientId();
  return useQuery({
    queryKey: ["audit-log", entityType, cid],
    queryFn: () => {
      const params = new URLSearchParams({ limit: "20" });
      if (entityType) params.set("entity_type", entityType);
      return api.get<AuditLogPage>(`/audit-log?${params}`);
    },
  });
}

// ── Mutations ───────────────────────────────────────────────────────────────

export function usePatchCategory(
  options?: UseMutationOptions<Category, Error, { id: string; patch: Partial<Category> }>,
) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ id, patch }) => api.patch<Category>(`/categories/${id}`, patch),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["categories"] });
      // Premium rates live in plan_assignments and are shown under each matched
      // employee — refresh those views so edits propagate live.
      qc.invalidateQueries({ queryKey: ["employees"] });
      qc.invalidateQueries({ queryKey: ["employee"] });
      qc.invalidateQueries({ queryKey: ["audit-log"] });
    },
    ...options,
  });
}

// One age-band rate table per life product, shared by all its voluntary plans.
// Saving fans the write out to every age-banded voluntary category server-side.
export interface VoluntaryRateBandInput {
  label: string;
  min: number | null;
  max: number | null;
  rate: number;
}

export function useSetVoluntaryRates() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({
      policyYearId,
      productId,
      bands,
    }: {
      policyYearId: string;
      productId: string;
      bands: VoluntaryRateBandInput[];
    }) =>
      api.put(
        `/policy-years/${policyYearId}/products/${productId}/voluntary-rates`,
        { bands },
      ),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["categories"] });
      qc.invalidateQueries({ queryKey: ["employees"] });
      qc.invalidateQueries({ queryKey: ["employee"] });
      qc.invalidateQueries({ queryKey: ["audit-log"] });
    },
  });
}

export function useCreateCategory() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: {
      policy_year_id: string;
      product_id: string | null;
      display_name: string;
      participation_model?: "compulsory" | "voluntary" | null;
      plan_assignments?: Record<string, unknown> | null;
    }) => api.post<Category>("/categories", body),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["categories"] });
      qc.invalidateQueries({ queryKey: ["audit-log"] });
    },
  });
}

export function useUpdatePlan() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ id, patch }: { id: string; patch: Partial<PlanDetail> }) =>
      api.patch<PlanDetail>(`/plans/${id}`, patch),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["plans"] });
      // SOB is hydrated onto each matched employee's plan — refresh live.
      qc.invalidateQueries({ queryKey: ["employees"] });
      qc.invalidateQueries({ queryKey: ["employee"] });
      qc.invalidateQueries({ queryKey: ["audit-log"] });
    },
  });
}

export function useConfirmCategory() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: string) =>
      api.post<Category>(`/categories/${id}/confirm`, {}),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["categories"] });
      qc.invalidateQueries({ queryKey: ["audit-log"] });
    },
  });
}

export interface AIStatus {
  configured: boolean;
  // Mirrors `AISource` in core/ai_config.py — resolution order is
  // byok (this company's own key) → platform (the shared default) → env.
  source: "byok" | "platform" | "env" | "none";
  model: string | null;
  cache_kind: string;
  breaker_state: "closed" | "open" | "half_open";
  month_to_date_tokens?: number;
  monthly_token_budget?: number;
}

export function useAIStatus() {
  const cid = useActiveClientId();
  return useQuery({
    queryKey: ["system", "ai-status", cid],
    queryFn: () => api.get<AIStatus>("/system/ai-status"),
    staleTime: 60_000,
  });
}

export interface AISpendSummary {
  month_to_date_tokens: number;
  month_to_date_input_tokens: number;
  month_to_date_output_tokens: number;
  month_to_date_cost_usd: number;
  monthly_token_budget: number;
  by_operation: {
    operation: string;
    calls: number;
    tokens: number;
    input_tokens: number;
    output_tokens: number;
    cost_usd: number;
  }[];
  recent: {
    operation: string;
    model: string;
    input_tokens: number;
    output_tokens: number;
    cost_usd: number;
    cache_hit: boolean;
    created_at: string;
  }[];
}

export function useAISpend() {
  const cid = useActiveClientId();
  return useQuery({
    queryKey: ["ai-spend", "summary", cid],
    queryFn: () => api.get<AISpendSummary>("/ai-spend/summary"),
    staleTime: 30_000,
  });
}

export function useSetAIBudget() {
  const qc = useQueryClient();
  const cid = useActiveClientId();
  return useMutation({
    mutationFn: (monthly_token_budget: number) =>
      api.put<{ monthly_token_budget: number }>("/ai-spend/budget", {
        monthly_token_budget,
      }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["ai-spend", "summary", cid] });
      qc.invalidateQueries({ queryKey: ["system", "ai-status", cid] });
    },
  });
}

/** Status of the stored platform key — never carries the cleartext. */
export interface PlatformAICredentials {
  configured: boolean;
  provider: string | null;
  location: string | null;
  model: string | null;
  key_fingerprint: string | null;
  key_masked: string | null;
  last_validated_at: string | null;
  last_validation_error: string | null;
}

export interface PlatformAILimits {
  platform_monthly_token_cap: number;
  default_monthly_token_budget: number;
  max_concurrent_calls: number;
}

export interface PlatformAISettings extends PlatformAILimits {
  credentials: PlatformAICredentials;
}

export interface PlatformAICredentialsUpsert {
  location?: string | null;
  model?: string | null;
  service_account_json: string;
}

export interface PlatformAICredentialsTestPayload {
  location?: string | null;
  model?: string | null;
  service_account_json?: string | null;
}

// Platform-wide (system-admin only) — spans every company on the shared key,
// so the query key is NOT client-scoped.
export function usePlatformAISettings(enabled = true) {
  return useQuery({
    queryKey: ["platform-ai-settings"],
    queryFn: () => api.get<PlatformAISettings>("/platform-ai-settings"),
    enabled,
    staleTime: 60_000,
  });
}

export function useSetPlatformAISettings() {
  const qc = useQueryClient();
  return useMutation({
    // Limits only — the platform key has its own endpoint so saving a limit
    // can never clear it.
    mutationFn: (payload: PlatformAILimits) =>
      api.put<PlatformAISettings>("/platform-ai-settings", payload),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["platform-ai-settings"] });
      qc.invalidateQueries({ queryKey: ["ai-spend", "summary"] });
    },
  });
}

// The platform key is the default every company runs on, so a change to it
// invalidates every surface that reports whether AI is configured.
function invalidatePlatformKey(qc: ReturnType<typeof useQueryClient>) {
  qc.invalidateQueries({ queryKey: ["platform-ai-settings"] });
  qc.invalidateQueries({ queryKey: ["system", "ai-status"] });
  qc.invalidateQueries({ queryKey: ["audit-log"] });
}

export function useSetPlatformAICredentials() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (payload: PlatformAICredentialsUpsert) =>
      api.put<PlatformAISettings>("/platform-ai-settings/credentials", payload),
    onSuccess: () => invalidatePlatformKey(qc),
  });
}

export function useDeletePlatformAICredentials() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: () =>
      api.delete<PlatformAISettings>("/platform-ai-settings/credentials"),
    onSuccess: () => invalidatePlatformKey(qc),
  });
}

export function useTestPlatformAICredentials() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (payload: PlatformAICredentialsTestPayload | undefined) =>
      api.post<AIConfigTestResult>(
        "/platform-ai-settings/credentials/test",
        payload ?? {},
      ),
    onSuccess: () => {
      // Testing the stored key updates last_validated_at; refresh.
      qc.invalidateQueries({ queryKey: ["platform-ai-settings"] });
    },
  });
}

export function useAISuggest() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: string) =>
      api.post<Category>(`/categories/${id}/ai-suggest`, {}),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["categories"] });
      qc.invalidateQueries({ queryKey: ["audit-log"] });
    },
  });
}

export function useDeleteCategory() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => api.delete<void>(`/categories/${id}`),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["categories"] });
      qc.invalidateQueries({ queryKey: ["audit-log"] });
    },
  });
}

export function useBulkDeleteEmployees() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({
      policyYearId,
      confirm = false,
    }: {
      policyYearId: string;
      confirm?: boolean;
    }) =>
      api.delete<{ deleted: number }>(
        `/employees?policy_year_id=${policyYearId}&confirm=${confirm}`,
      ),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["employees"] });
      qc.invalidateQueries({ queryKey: ["match-results"] });
      qc.invalidateQueries({ queryKey: ["flex-membership"] });
      qc.invalidateQueries({ queryKey: ["flex-coverage"] });
      qc.invalidateQueries({ queryKey: ["audit-log"] });
    },
    // The caller handles the enrollment_data_at_risk 409 itself (a confirm-and
    // -retry dialog), so it also owns every other error toast for this action.
    meta: { localErrorHandling: true },
  });
}

export function useBulkDeleteDependants() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({
      policyYearId,
      confirm = false,
    }: {
      policyYearId: string;
      confirm?: boolean;
    }) =>
      api.delete<{ deleted: number; flex_errors?: string[] }>(
        `/dependants?policy_year_id=${policyYearId}&confirm=${confirm}`,
      ),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["dependants"] });
      qc.invalidateQueries({ queryKey: ["flex-membership"] });
      qc.invalidateQueries({ queryKey: ["flex-coverage"] });
      qc.invalidateQueries({ queryKey: ["benefit-statement"] });
      qc.invalidateQueries({ queryKey: ["audit-log"] });
    },
    // The caller handles the member_data_at_risk 409 itself (a confirm-and-retry
    // dialog), so it also owns every other error toast for this action.
    meta: { localErrorHandling: true },
  });
}

export function usePlacementSlips(policyYearId: string | undefined) {
  const cid = useActiveClientId();
  return useQuery({
    queryKey: ["placement-slips", policyYearId, cid],
    queryFn: () =>
      api.get<PlacementSlipSummary[]>(
        `/placement-slips?policy_year_id=${policyYearId}`,
      ),
    enabled: Boolean(policyYearId),
  });
}

export function useUploadSlip() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({
      file,
      policyYearId,
      acknowledgePeriodMismatch,
    }: {
      file: File;
      policyYearId: string;
      acknowledgePeriodMismatch?: boolean;
    }) => {
      const fd = new FormData();
      fd.append("file", file);
      fd.append("policy_year_id", policyYearId);
      if (acknowledgePeriodMismatch) {
        fd.append("acknowledge_period_mismatch", "true");
      }
      return api.upload<ParseResult>("/placement-slips/parse", fd);
    },
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["categories"] });
      qc.invalidateQueries({ queryKey: ["product-setups"] });
      qc.invalidateQueries({ queryKey: ["setup-products"] });
      qc.invalidateQueries({ queryKey: ["plans"] });
      qc.invalidateQueries({ queryKey: ["placement-slips"] });
      // Re-upload can auto-rematch employees server-side (`rematched`).
      qc.invalidateQueries({ queryKey: ["match-results"] });
      qc.invalidateQueries({ queryKey: ["audit-log"] });
    },
  });
}

/** Persist a broker's SOB column-mapping correction for a template. The next
 *  upload of a sheet with the same fingerprint reuses it. */
export function useSaveTemplateProfile() {
  return useMutation({
    mutationFn: (payload: SlipTemplateProfileSave) =>
      api.put<{ id: string; fingerprint: string }>(
        "/placement-slips/template-profiles",
        payload,
      ),
  });
}

// `useUploadEmployees` / `useUploadDependants` were removed here. The roster is
// changed through the listing SYNC (`api/adc.ts` → preview/apply), which adds,
// updates AND terminates; the old insert-only upload silently discarded every
// edit to a person already on file. `POST /employees/upload` and
// `/dependants/upload` still exist server-side as the low-level insert
// primitive their dedup regression suites exercise — no UI calls them.

export function useSetCurrentPolicyYear() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (policyYearId: string) =>
      api.post<PolicyYear>(`/policy-years/${policyYearId}/set-current`, {}),
    // Which year is current changes what a LOT of unrelated queries return —
    // the claim-type vocabulary, the dashboard roll-up, panel assignments,
    // portal previews. Listing them would guarantee one gets missed and shows
    // stale-empty, so invalidate broadly: this is a rare, deliberate action.
    // (Safe here unlike a tenant switch — the active client is unchanged, so no
    // in-flight query can be refetched against the wrong tenant.)
    onSuccess: () => qc.invalidateQueries(),
  });
}

export interface AttributePayload {
  attribute_id: string;
  display_name: string;
  data_type: string;
  enum_values?: string[] | null;
  is_required?: boolean;
  is_pii?: boolean;
  description?: string | null;
}

// Where a catalog create lands. "company" (default) = the active client;
// "firm" = a shared firm-library default (client_id NULL), admins only.
export type CatalogScope = "company" | "firm";

export function useCreateAttribute() {
  const qc = useQueryClient();
  return useMutation({
    // Accept either a bare payload (defaults to company scope, keeping existing
    // callers working) or an explicit { payload, scope }.
    mutationFn: (
      input:
        | AttributePayload
        | { payload: AttributePayload; scope: CatalogScope },
    ) => {
      const payload = "payload" in input ? input.payload : input;
      const scope: CatalogScope = "payload" in input ? input.scope : "company";
      return api.post<AttributeSchema>(
        `/schemas/employee-attributes?scope=${scope}`,
        payload,
      );
    },
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["schemas", "employee-attributes"] });
      qc.invalidateQueries({ queryKey: ["audit-log"] });
    },
  });
}

export function useUpdateAttribute() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({
      id,
      patch,
    }: {
      id: string;
      patch: Partial<AttributePayload>;
    }) =>
      api.patch<AttributeSchema>(`/schemas/employee-attributes/${id}`, patch),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["schemas", "employee-attributes"] });
      qc.invalidateQueries({ queryKey: ["audit-log"] });
    },
  });
}

export function useDeleteAttribute() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: string) =>
      api.delete<void>(`/schemas/employee-attributes/${id}`),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["schemas", "employee-attributes"] });
      qc.invalidateQueries({ queryKey: ["categories"] });
      qc.invalidateQueries({ queryKey: ["audit-log"] });
    },
  });
}

export function useRecommendConfig() {
  return useMutation({
    mutationFn: (policyYearId: string) =>
      api.post<ConfigRecommendation>(
        `/policy-years/${policyYearId}/recommend-config`,
        {},
      ),
  });
}

export function useApplyConfig() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({
      policyYearId,
      attributes,
      products,
      rerun_matching,
    }: {
      policyYearId: string;
      attributes: ApplyAttributeItem[];
      products: ApplyProductItem[];
      rerun_matching: boolean;
    }) =>
      api.post<ApplyConfigResult>(`/policy-years/${policyYearId}/apply-config`, {
        attributes,
        products,
        rerun_matching,
      }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["schemas", "employee-attributes"] });
      qc.invalidateQueries({ queryKey: ["schemas", "products"] });
      qc.invalidateQueries({ queryKey: ["categories"] });
      qc.invalidateQueries({ queryKey: ["match-results"] });
      qc.invalidateQueries({ queryKey: ["employees"] });
      qc.invalidateQueries({ queryKey: ["audit-log"] });
    },
  });
}

export interface ProductPayload {
  code: string;
  display_name: string;
  // No insurer: it is per benefit year (Company & Benefits → Header & Policy),
  // never a catalog column — see backend services/product_insurer.py.
  participation_model: "standard" | "extended" | "eo_only";
  has_dependants: boolean;
  is_outpatient: boolean;
  // Optional overrides persisted into product_metadata for custom products.
  line?: InsuranceLine;
  form_profile?: string;
  layout_family?: string;
  report_code?: string | null;
}

export function useCreateProduct() {
  const qc = useQueryClient();
  return useMutation({
    // Bare payload → company scope (existing callers); { payload, scope } to
    // target the firm library.
    mutationFn: (
      input: ProductPayload | { payload: ProductPayload; scope: CatalogScope },
    ) => {
      const payload = "payload" in input ? input.payload : input;
      const scope: CatalogScope = "payload" in input ? input.scope : "company";
      return api.post<Product>(`/schemas/products?scope=${scope}`, payload);
    },
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["schemas", "products"] });
      // The new code becomes configurable immediately in its line's setup panel.
      qc.invalidateQueries({ queryKey: ["setup-products"] });
      qc.invalidateQueries({ queryKey: ["audit-log"] });
    },
  });
}

export function useUpdateProduct() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({
      id,
      patch,
    }: {
      id: string;
      patch: Partial<ProductPayload>;
    }) => api.patch<Product>(`/schemas/products/${id}`, patch),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["schemas", "products"] });
      // Classification (form_profile / line / layout_family) reshapes the
      // setup templates and tab routing.
      qc.invalidateQueries({ queryKey: ["setup-products"] });
      qc.invalidateQueries({ queryKey: ["audit-log"] });
    },
  });
}

/** Remove a product from a policy year's tab (categories, plans, setup draft,
 *  coverage override, and the client catalog row when unused elsewhere). */
export function useRemoveProduct(policyYearId: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (code: string) =>
      api.delete<void>(
        `/policy-years/${policyYearId}/products/${encodeURIComponent(code)}`,
      ),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["schemas", "products"] });
      qc.invalidateQueries({ queryKey: ["setup-products"] });
      qc.invalidateQueries({ queryKey: ["product-setups"] });
      qc.invalidateQueries({ queryKey: ["categories"] });
      qc.invalidateQueries({ queryKey: ["product-terms"] });
      qc.invalidateQueries({ queryKey: ["audit-log"] });
    },
  });
}

export function useDeleteProduct() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => api.delete<void>(`/schemas/products/${id}`),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["schemas", "products"] });
      qc.invalidateQueries({ queryKey: ["categories"] });
      qc.invalidateQueries({ queryKey: ["audit-log"] });
    },
  });
}

export function useCreatePolicyYear() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (payload: {
      start_date: string;
      end_date: string;
      claim_grace_period_days?: number | null;
      leaver_access_days?: number | null;
    }) => api.post<PolicyYear>("/policy-years", payload),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["policy-years"] });
      qc.invalidateQueries({ queryKey: ["audit-log"] });
    },
  });
}

export function useUpdatePolicyYear() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({
      policyYearId,
      payload,
    }: {
      policyYearId: string;
      payload: {
        start_date?: string;
        end_date?: string;
        claim_grace_period_days?: number | null;
        leaver_access_days?: number | null;
      };
    }) => api.patch<PolicyYear>(`/policy-years/${policyYearId}`, payload),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["policy-years"] });
      qc.invalidateQueries({ queryKey: ["audit-log"] });
    },
  });
}

export function useDeletePolicyYear() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (policyYearId: string) =>
      api.delete<void>(`/policy-years/${policyYearId}`),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["policy-years"] });
      qc.invalidateQueries({ queryKey: ["audit-log"] });
    },
  });
}

export interface PolicyYearCopyResult {
  policy_year: PolicyYear;
  copied: Record<string, number>;
}

export function useCopyPolicyYear() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({
      sourceId,
      payload,
    }: {
      sourceId: string;
      /** Both deadline settings are OPTIONAL overrides — omitted, the source
       *  year's value carries over. The backend honours them symmetrically
       *  (`PolicyYearCopyIn`); it used to accept only the grace period and
       *  pydantic dropped the other in silence, so a caller setting the
       *  run-off on copy compiled clean and lost the value. */
      payload: {
        start_date: string;
        end_date: string;
        claim_grace_period_days?: number | null;
        leaver_access_days?: number | null;
      };
    }) =>
      api.post<PolicyYearCopyResult>(
        `/policy-years/${sourceId}/copy`,
        payload,
      ),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["policy-years"] });
      qc.invalidateQueries({ queryKey: ["audit-log"] });
    },
  });
}

// ── Admin / provisioning ─────────────────────────────────────────────────────

export interface BrokerFirmOut {
  id: string;
  name: string;
  client_count: number;
}

export interface AdminClient {
  id: string;
  /** The broker's internal short handle ("CDL") — what every broker-facing
   *  list and the company switcher print. */
  name: string;
  broker_firm_id: string;
  /** The registered company name, or null until a broker fills it in. Never
   *  derived from `name`: a short handle is not a legal name. */
  legal_name: string | null;
  /** The URL alias. On this single-host deployment it is the `/portal/{slug}`
   *  segment every emailed invite points at, so changing it invalidates live
   *  links — see `PATCH /admin/clients/{id}`, which never moves it on a plain
   *  rename. */
  slug: string | null;
}

export interface AdminUser {
  id: string;
  email: string;
  display_name: string | null;
  role: string;
  status: string;
  broker_firm_id: string | null;
  client_ids: string[];
}

export interface AdminInvitation {
  id: string;
  email: string;
  role: string;
  status: string;
  broker_firm_id: string;
  token: string;
  user_id: string;
  expires_at: string | null;
}

/** Firms are internal plumbing on a single-firm platform — the only consumer is
 *  FirmPicker, which renders nothing unless several somehow exist. There is no
 *  create-firm UI; bootstrap is scripts/create_system_admin.py --firm-name. */
export function useBrokerFirms(enabled = true) {
  return useQuery({
    queryKey: ["admin", "broker-firms"],
    queryFn: () => api.get<BrokerFirmOut[]>("/admin/broker-firms"),
    enabled,
  });
}

export function useCreateBrokerFirm() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (name: string) =>
      api.post<BrokerFirmOut>("/admin/broker-firms", { name }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["admin", "broker-firms"] }),
  });
}

export function useAdminClients() {
  return useQuery({
    queryKey: ["admin", "clients"],
    queryFn: () => api.get<AdminClient[]>("/admin/clients"),
  });
}

export function useCreateClient() {
  const qc = useQueryClient();
  return useMutation({
    // broker_firm_id is system_admin-only and optional: the backend falls back
    // to the sole firm when the platform has exactly one, and rejects an
    // unqualified create only when there are several to choose between.
    mutationFn: (body: {
      name: string;
      broker_firm_id?: string;
      legal_name?: string;
    }) => api.post<AdminClient>("/admin/clients", body),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["admin", "clients"] });
      qc.invalidateQueries({ queryKey: ["me"] }); // switcher's accessible clients
      qc.invalidateQueries({ queryKey: ["dashboard-summary"] }); // Home grid
    },
  });
}

export function usePatchClient() {
  const qc = useQueryClient();
  return useMutation({
    // A PARTIAL patch: only the keys present are applied server-side, so a
    // rename cannot blank the legal name and an edit that leaves the alias
    // alone cannot move it (which would break live invite links).
    mutationFn: ({
      id,
      ...body
    }: {
      id: string;
      name?: string;
      legal_name?: string | null;
      slug?: string;
    }) => api.patch<AdminClient>(`/admin/clients/${id}`, body),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["admin", "clients"] });
      qc.invalidateQueries({ queryKey: ["me"] });
      qc.invalidateQueries({ queryKey: ["dashboard-summary"] });
    },
  });
}

export function useDeleteClient() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => api.delete<void>(`/admin/clients/${id}`),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["admin", "clients"] });
      qc.invalidateQueries({ queryKey: ["me"] }); // switcher's accessible clients
      qc.invalidateQueries({ queryKey: ["dashboard-summary"] }); // Home grid
    },
  });
}

export function useAdminUsers() {
  return useQuery({
    queryKey: ["admin", "users"],
    queryFn: () => api.get<AdminUser[]>("/admin/users"),
  });
}

export function usePatchUser() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({
      id,
      patch,
    }: {
      id: string;
      patch: Partial<Pick<AdminUser, "display_name" | "role" | "status">> & {
        client_ids?: string[];
      };
    }) => api.patch<AdminUser>(`/admin/users/${id}`, patch),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["admin", "users"] }),
  });
}

export function useInvitations() {
  return useQuery({
    queryKey: ["admin", "invitations"],
    queryFn: () => api.get<AdminInvitation[]>("/admin/invitations"),
  });
}

export function useCreateInvitation() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: {
      email: string;
      role: string;
      client_ids?: string[];
      broker_firm_id?: string; // system_admin only; see useCreateClient
    }) => api.post<AdminInvitation>("/admin/invitations", body),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["admin", "invitations"] });
      qc.invalidateQueries({ queryKey: ["admin", "users"] });
    },
  });
}

export function useRevokeInvitation() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: string) =>
      api.post<{ revoked: boolean }>(`/admin/invitations/${id}/revoke`, {}),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["admin", "invitations"] });
      qc.invalidateQueries({ queryKey: ["admin", "users"] });
    },
  });
}

export function useAIConfig(enabled = true) {
  // Scoped by the active client: BYOK config is per-tenant, and a user with
  // access to multiple clients switches between them via the client header.
  // `enabled=false` for system_admin — /ai-config is broker_admin-only and
  // would 403 (BYOK is a per-company broker surface, not a platform one).
  const cid = useActiveClientId();
  return useQuery({
    queryKey: ["ai-config", cid],
    queryFn: async () => {
      const data = await api.get<AIConfig | undefined>("/ai-config");
      return data ?? null;
    },
    enabled,
    staleTime: 30_000,
  });
}

export function usePutAIConfig() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (payload: AIConfigUpsert) =>
      api.put<AIConfig>("/ai-config", payload),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["ai-config"] });
      qc.invalidateQueries({ queryKey: ["system", "ai-status"] });
      qc.invalidateQueries({ queryKey: ["audit-log"] });
    },
  });
}

export function useDeleteAIConfig() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: () => api.delete<void>("/ai-config"),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["ai-config"] });
      qc.invalidateQueries({ queryKey: ["system", "ai-status"] });
      qc.invalidateQueries({ queryKey: ["audit-log"] });
    },
  });
}

export function useTestAIConfig() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (payload: AIConfigTestPayload | undefined) =>
      api.post<AIConfigTestResult>("/ai-config/test", payload ?? {}),
    onSuccess: () => {
      // Test against the stored config updates last_validated_at; refresh.
      qc.invalidateQueries({ queryKey: ["ai-config"] });
    },
  });
}

export function useSetMatchOverride() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({
      employeeId,
      categoryId,
      categoryIds,
    }: {
      employeeId: string;
      categoryId?: string | null;
      categoryIds?: string[];
    }) =>
      api.post(`/match-results/employees/${employeeId}/override`, {
        ...(categoryIds !== undefined
          ? { category_ids: categoryIds }
          : { category_id: categoryId ?? null }),
      }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["match-results"] });
      qc.invalidateQueries({ queryKey: ["employees"] });
      qc.invalidateQueries({ queryKey: ["employee"] });
      qc.invalidateQueries({ queryKey: ["benefit-statement"] });
      qc.invalidateQueries({ queryKey: ["audit-log"] });
    },
  });
}

export function useUpdateEmployee() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({
      employeeId,
      employee_name,
      attribute_values,
    }: {
      employeeId: string;
      // No `| null`: the backend treats an explicit null as a no-op, so
      // allowing it only invites a silently-ignored "clear name" attempt.
      employee_name?: string;
      attribute_values?: Record<string, unknown>;
    }) =>
      api.patch<Employee>(`/employees/${employeeId}`, {
        employee_name,
        attribute_values,
      }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["employees"] });
      qc.invalidateQueries({ queryKey: ["employee"] });
      // Editing attributes re-derives family status → membership counts change.
      qc.invalidateQueries({ queryKey: ["flex-membership"] });
      qc.invalidateQueries({ queryKey: ["flex-coverage"] });
      qc.invalidateQueries({ queryKey: ["benefit-statement"] });
      qc.invalidateQueries({ queryKey: ["audit-log"] });
    },
  });
}

export function useUpdateDependant() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({
      dependantId,
      attribute_values,
      employee_id,
      relink,
    }: {
      dependantId: string;
      attribute_values?: Record<string, unknown>;
      employee_id?: string | null;
      relink?: boolean;
    }) =>
      api.patch<Dependant>(`/dependants/${dependantId}`, {
        attribute_values,
        employee_id,
        relink: relink ?? false,
      }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["dependants"] });
      qc.invalidateQueries({ queryKey: ["flex-membership"] });
      qc.invalidateQueries({ queryKey: ["flex-coverage"] });
      qc.invalidateQueries({ queryKey: ["audit-log"] });
    },
  });
}

// Fetches every plan for the policy year, paging past the server's per-request
// cap (MAX_LIMIT). The election UI groups plans by product for its dropdowns, so
// a truncated list silently drops whole products' options — page fully instead.
const PLANS_PAGE_SIZE = 200; // matches backend MAX_LIMIT

export function usePlans(policyYearId: string | undefined, productId?: string) {
  const cid = useActiveClientId();
  return useQuery({
    queryKey: ["plans", policyYearId, productId, cid],
    queryFn: async () => {
      const items: PlanList["items"] = [];
      let total = 0;
      do {
        const params = new URLSearchParams();
        if (policyYearId) params.set("policy_year_id", policyYearId);
        if (productId) params.set("product_id", productId);
        params.set("offset", String(items.length));
        params.set("limit", String(PLANS_PAGE_SIZE));
        const page = await api.get<PlanList>(`/plans?${params.toString()}`);
        total = page.total;
        items.push(...page.items);
        if (page.items.length < PLANS_PAGE_SIZE) break; // last page reached
      } while (items.length < total);
      return { total, items } satisfies PlanList;
    },
    enabled: Boolean(policyYearId),
  });
}

// ── Product setup (guided product configuration) ────────────────────────────

// Products available to set up in this policy year — slip-detected (structure
// synthesized from the upload) plus any hand-authored template.
export function useSetupProducts(policyYearId: string | undefined) {
  const cid = useActiveClientId();
  return useQuery({
    queryKey: ["setup-products", policyYearId, cid],
    queryFn: () =>
      api.get<SetupProductSummary[]>(
        `/policy-years/${policyYearId}/setup-products`,
      ),
    enabled: Boolean(policyYearId),
  });
}

// Resolved structure (file template or slip-synthesized) for one product.
export function useSetupTemplate(
  policyYearId: string | undefined,
  code: string | undefined,
) {
  const cid = useActiveClientId();
  return useQuery({
    queryKey: ["setup-template", policyYearId, code, cid],
    queryFn: () =>
      api.get<ProductTemplate>(
        `/policy-years/${policyYearId}/setup-products/${code}/template`,
      ),
    enabled: Boolean(policyYearId && code),
  });
}

export function useProductSetups(policyYearId: string | undefined) {
  const cid = useActiveClientId();
  return useQuery({
    queryKey: ["product-setups", policyYearId, cid],
    queryFn: () =>
      api.get<ProductSetup[]>(
        `/policy-years/${policyYearId}/product-setups`,
      ),
    enabled: Boolean(policyYearId),
  });
}

// Per-product coverage periods for a policy year (override or inherited span).
export function useProductTerms(policyYearId: string | undefined) {
  const cid = useActiveClientId();
  return useQuery({
    queryKey: ["product-terms", policyYearId, cid],
    queryFn: () =>
      api.get<ProductTerm[]>(`/policy-years/${policyYearId}/product-terms`),
    enabled: Boolean(policyYearId),
  });
}

interface ProductTermArgs {
  productId: string;
  // Partial update — send only the dimensions being changed. Dates move as a
  // pair; GST (tri-state) and dates are independent so one never resets the
  // other. Omit a field entirely to leave it untouched server-side.
  coverageStart?: string | null;
  coverageEnd?: string | null;
  gstIncluded?: boolean | null;
  gstRate?: number | null;
  freeCoverLimit?: number | null;
  nelAgeLimit?: number | null;
  policyNumber?: string | null;
}

export function useSetProductTerm(policyYearId: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({
      productId,
      coverageStart,
      coverageEnd,
      gstIncluded,
      gstRate,
      freeCoverLimit,
      nelAgeLimit,
      policyNumber,
    }: ProductTermArgs) => {
      // Only include keys the caller actually set — the backend applies exactly
      // the fields present (model_fields_set), so omitted dimensions are kept.
      const body: Record<string, unknown> = {};
      if (coverageStart !== undefined) body.coverage_start = coverageStart;
      if (coverageEnd !== undefined) body.coverage_end = coverageEnd;
      if (gstIncluded !== undefined) body.gst_included = gstIncluded;
      if (gstRate !== undefined) body.gst_rate = gstRate;
      if (freeCoverLimit !== undefined) body.free_cover_limit = freeCoverLimit;
      if (nelAgeLimit !== undefined) body.nel_age_limit = nelAgeLimit;
      if (policyNumber !== undefined) body.policy_number = policyNumber;
      return api.put<ProductTerm>(
        `/policy-years/${policyYearId}/product-terms/${productId}`,
        body,
      );
    },
    onSuccess: () => {
      // The override shifts both the per-product list and the policy-year
      // envelope shown elsewhere (top bar, activations).
      qc.invalidateQueries({ queryKey: ["product-terms", policyYearId] });
      qc.invalidateQueries({ queryKey: ["policy-years"] });
    },
  });
}

export function useResetProductTerm(policyYearId: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (productId: string) =>
      api.delete<void>(
        `/policy-years/${policyYearId}/product-terms/${productId}`,
      ),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["product-terms", policyYearId] });
      qc.invalidateQueries({ queryKey: ["policy-years"] });
    },
  });
}

export function useFieldSuggestions(
  policyYearId: string | undefined,
  code: string | undefined,
) {
  const cid = useActiveClientId();
  return useQuery({
    queryKey: ["field-suggestions", policyYearId, code, cid],
    queryFn: () =>
      api.get<FieldSuggestions>(
        `/policy-years/${policyYearId}/product-setups/${code}/field-suggestions`,
      ),
    enabled: Boolean(policyYearId && code),
  });
}

/** Live employee + dependant match counts for the Basis-of-Cover draft rows.
 * `categories` should be debounced by the caller — the query key hashes it, so
 * each distinct set of descriptions is one cached request. */
export function useMemberCounts(
  policyYearId: string | undefined,
  productCode: string | undefined,
  hasDependants: boolean,
  // `insured` (the row's legal-entity list) scopes counts to that entity's
  // employees on multi-subsidiary schemes — mirror of the matching gate.
  categories: {
    key: string;
    description: string;
    insured?: string[] | string | null;
  }[],
) {
  const cid = useActiveClientId();
  const enabled = Boolean(
    policyYearId &&
      categories.some((c) => c.description.trim().length > 0),
  );
  return useQuery({
    queryKey: [
      "member-counts",
      policyYearId,
      productCode,
      hasDependants,
      cid,
      categories,
    ],
    queryFn: () =>
      api.post<MemberCounts>(`/policy-years/${policyYearId}/member-counts`, {
        product_code: productCode ?? null,
        has_dependants: hasDependants,
        categories,
      }),
    enabled,
    staleTime: 30_000,
  });
}

interface SetupMutationArgs {
  code: string;
  answers: SetupAnswers;
  templateVersion: number;
}

export function useSaveSetup(policyYearId: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ code, answers, templateVersion }: SetupMutationArgs) =>
      api.put<ProductSetup>(
        `/policy-years/${policyYearId}/product-setups/${code}`,
        { answers, template_version: templateVersion },
      ),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["product-setups", policyYearId] });
    },
  });
}

export function useDiscardSetup(policyYearId: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (code: string) =>
      api.delete<void>(
        `/policy-years/${policyYearId}/product-setups/${code}`,
      ),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["product-setups", policyYearId] });
      qc.invalidateQueries({ queryKey: ["audit-log"] });
    },
  });
}

export function useConfirmSetup(policyYearId: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ code, answers, templateVersion }: SetupMutationArgs) =>
      api.post<ConfirmSetupResult>(
        `/policy-years/${policyYearId}/product-setups/${code}/confirm`,
        { answers, template_version: templateVersion },
      ),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["product-setups", policyYearId] });
      qc.invalidateQueries({ queryKey: ["plans"] });
      // Confirm materializes Category rows and re-runs matching, so refresh the
      // category list / coverage, employee matches, and match results too.
      qc.invalidateQueries({ queryKey: ["categories"] });
      qc.invalidateQueries({ queryKey: ["employees"] });
      qc.invalidateQueries({ queryKey: ["match-results"] });
      qc.invalidateQueries({ queryKey: ["audit-log"] });
    },
  });
}

// ── Flexible Benefits (Flex) ────────────────────────────────────────────────

/** The one Flex scheme for a policy year. 404 → no scheme yet (returns null). */
export function useFlexScheme(policyYearId: string | undefined) {
  const cid = useActiveClientId();
  return useQuery({
    queryKey: ["flex-scheme", policyYearId, cid],
    queryFn: () =>
      api
        .get<FlexScheme>(`/policy-years/${policyYearId}/flex-scheme`)
        .catch((e: unknown) => {
          if (isNotFoundError(e)) return null;
          throw e;
        }),
    enabled: Boolean(policyYearId),
  });
}

/** Upload one or more Flex documents → AI extracts each → merge into the draft. */
export function useUploadFlex(policyYearId: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (files: File[]) => {
      const fd = new FormData();
      for (const file of files) fd.append("files", file);
      return api.upload<FlexScheme>(
        `/policy-years/${policyYearId}/flex-scheme/extract`,
        fd,
      );
    },
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["flex-scheme", policyYearId] });
      qc.invalidateQueries({ queryKey: ["flex-membership", policyYearId] });
      qc.invalidateQueries({ queryKey: ["flex-coverage", policyYearId] });
      qc.invalidateQueries({ queryKey: ["flex-roster-vocab", policyYearId] });
      qc.invalidateQueries({ queryKey: ["audit-log"] });
    },
  });
}

/** Re-seed unreconciled tiers' match sets from the current roster (for when the
 *  roster was uploaded after the flex document). */
export function useSuggestFlexMatches(policyYearId: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: () =>
      api.post<FlexScheme>(
        `/policy-years/${policyYearId}/flex-scheme/suggest-matches`,
        {},
      ),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["flex-scheme", policyYearId] });
      qc.invalidateQueries({ queryKey: ["flex-membership", policyYearId] });
      qc.invalidateQueries({ queryKey: ["flex-coverage", policyYearId] });
      qc.invalidateQueries({ queryKey: ["flex-roster-vocab", policyYearId] });
      qc.invalidateQueries({ queryKey: ["audit-log"] });
    },
  });
}

export function useSaveFlexScheme(policyYearId: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (scheme: FlexSchemeBody) =>
      api.put<FlexScheme>(`/policy-years/${policyYearId}/flex-scheme`, { scheme }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["flex-scheme", policyYearId] });
      qc.invalidateQueries({ queryKey: ["flex-membership", policyYearId] });
      qc.invalidateQueries({ queryKey: ["flex-coverage", policyYearId] });
      qc.invalidateQueries({ queryKey: ["flex-roster-vocab", policyYearId] });
    },
  });
}

export function useConfirmFlexScheme(policyYearId: string) {
  const qc = useQueryClient();
  return useMutation({
    // acknowledge=true proceeds past the "employees with no wallet" coverage warning.
    mutationFn: (acknowledge: boolean = false) =>
      api.post<FlexScheme>(
        `/policy-years/${policyYearId}/flex-scheme/confirm`,
        { acknowledge },
      ),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["flex-scheme", policyYearId] });
      qc.invalidateQueries({ queryKey: ["flex-membership", policyYearId] });
      qc.invalidateQueries({ queryKey: ["flex-coverage", policyYearId] });
      qc.invalidateQueries({ queryKey: ["flex-roster-vocab", policyYearId] });
      // Confirm assigns wallets onto employees → benefit statements change.
      qc.invalidateQueries({ queryKey: ["benefit-statement"] });
      qc.invalidateQueries({ queryKey: ["employees"] });
      qc.invalidateQueries({ queryKey: ["audit-log"] });
    },
  });
}

/** (Re-)assign Flex wallets across the roster from the confirmed scheme. */
export function useAssignFlex(policyYearId: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: () =>
      api.post<FlexAssignResult>(
        `/policy-years/${policyYearId}/flex-scheme/assign`,
        {},
      ),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["flex-membership", policyYearId] });
      qc.invalidateQueries({ queryKey: ["flex-coverage", policyYearId] });
      qc.invalidateQueries({ queryKey: ["flex-roster-vocab", policyYearId] });
      qc.invalidateQueries({ queryKey: ["benefit-statement"] });
      qc.invalidateQueries({ queryKey: ["employees"] });
      qc.invalidateQueries({ queryKey: ["audit-log"] });
    },
  });
}

export function useDiscardFlexScheme(policyYearId: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: () =>
      api.delete<void>(`/policy-years/${policyYearId}/flex-scheme`),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["flex-scheme", policyYearId] });
      qc.invalidateQueries({ queryKey: ["flex-membership", policyYearId] });
      qc.invalidateQueries({ queryKey: ["flex-coverage", policyYearId] });
      qc.invalidateQueries({ queryKey: ["audit-log"] });
    },
  });
}

/**
 * Family-status headcounts + per-employee flex assignment, computed live from the
 * employee & dependant listings. Drives the Flex breakdown card and the
 * family-status column on the employee roster.
 */
export function useFlexMembership(policyYearId: string | undefined) {
  const cid = useActiveClientId();
  return useQuery({
    queryKey: ["flex-membership", policyYearId, cid],
    queryFn: () =>
      api.get<FlexMembership>(
        `/policy-years/${policyYearId}/flex-scheme/membership`,
      ),
    enabled: Boolean(policyYearId),
  });
}

/**
 * Coverage validation: which active employees / dependants are left out of the
 * flex sizing, and who they are (capped preview). The full list downloads via
 * `downloadFlexCoverage`.
 */
export function useFlexCoverage(policyYearId: string | undefined) {
  const cid = useActiveClientId();
  return useQuery({
    queryKey: ["flex-coverage", policyYearId, cid],
    queryFn: () =>
      api.get<FlexCoverage>(
        `/policy-years/${policyYearId}/flex-scheme/coverage`,
      ),
    enabled: Boolean(policyYearId),
  });
}

/** Stream the full flex coverage report (.xlsx) to a browser download. */
export async function downloadFlexCoverage(policyYearId: string): Promise<void> {
  const res = await api.downloadResponse(
    `/policy-years/${policyYearId}/flex-scheme/coverage/export`,
  );
  const blob = await res.blob();
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  const match = /filename="([^"]+)"/.exec(
    res.headers.get("content-disposition") || "",
  );
  a.download = match?.[1] || "flex-coverage.xlsx";
  a.click();
  setTimeout(() => URL.revokeObjectURL(url), 10_000);
}

/**
 * Distinct employee-type (designation) + job-grade values on the active roster,
 * each flagged whether a tier already selects it. Drives the tier match-set
 * dropdowns and their coverage hints.
 */
/**
 * Legal entities available to a category's Insured field: roster entities with
 * headcounts, plus entities named in the config that match no roster value.
 * Powers the Insured token picker on the setup form and category cards.
 */
export function useEntityVocab(policyYearId: string | undefined) {
  const cid = useActiveClientId();
  return useQuery({
    queryKey: ["entity-vocab", policyYearId, cid],
    queryFn: () =>
      api.get<EntityVocab>(`/policy-years/${policyYearId}/entity-vocab`),
    enabled: Boolean(policyYearId),
  });
}

export function useFlexRosterVocab(policyYearId: string | undefined) {
  const cid = useActiveClientId();
  return useQuery({
    queryKey: ["flex-roster-vocab", policyYearId, cid],
    queryFn: () =>
      api.get<RosterVocab>(
        `/policy-years/${policyYearId}/flex-scheme/roster-vocab`,
      ),
    enabled: Boolean(policyYearId),
  });
}
