import { useQuery } from "@tanstack/react-query";
import { api } from "./client";
import { useSession } from "@/stores/session";

// ── Firm Home dashboard ────────────────────────────────────────────────────
export interface CompanyYear {
  id: string;
  year: number;
  status: string;
  start_date: string;
  end_date: string;
}

export interface CompanySummary {
  id: string;
  name: string;
  current_year: CompanyYear | null;
  next_year?: CompanyYear | null;
  member_count: number;
  dependant_count: number;
  claims_to_review: number;
  verification_pending: number;
  insured_claims_to_review: number;
  wallet_claims_to_review: number;
  claims_with_insurer: number;
  claims_overdue: number;
  messages_awaiting_reply: number;
  dependants_pending: number;
  employees_unmatched: number;
  matching_stale: boolean;
  underwriting_pending: number;
  enrollment_open: boolean;
  enrollment_open_count?: number;
  enrollment_closes_at: string | null;
  enrollment_scheduled?: number;
  enrollment_opens_at?: string | null;
  enrollment_overdue?: number;
}

export interface FirmTotals {
  company_count: number;
  member_count: number;
  dependant_count: number;
  claims_to_review: number;
  verification_pending: number;
  insured_claims_to_review: number;
  wallet_claims_to_review: number;
  claims_with_insurer: number;
  claims_overdue: number;
  messages_awaiting_reply: number;
  dependants_pending: number;
  employees_unmatched: number;
  underwriting_pending: number;
  windows_open: number;
}

export interface DashboardSummary {
  firm: FirmTotals;
  companies: CompanySummary[];
  insurers: string[];
  work_by_year?: CompanySummary[];
  business_date?: string;
}

/**
 * Firm-level roll-up powering the Home page. Scoped server-side to the caller's
 * accessible clients. The optional year narrows company dashboards; the request
 * client selects the firm schema, so both form part of the cache key.
 */
export function useDashboardSummary(
  policyYearId?: string | null,
  options: { localErrorHandling?: boolean } = {},
) {
  const clientId = useSession((state) => state.activeClientId);
  const params = new URLSearchParams();
  if (policyYearId) params.set("policy_year_id", policyYearId);
  return useQuery({
    queryKey: ["dashboard-summary", policyYearId ?? null, clientId],
    queryFn: () =>
      api.get<DashboardSummary>(
        `/dashboard/summary?${params}`,
      ),
    staleTime: 30_000,
    refetchInterval: 30_000,
    refetchOnMount: "always",
    meta: { localErrorHandling: options.localErrorHandling ?? false },
  });
}
