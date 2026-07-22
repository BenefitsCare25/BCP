import { useQuery } from "@tanstack/react-query";
import { api } from "@/api/client";
import { useSession } from "@/stores/session";

export interface ReportReadiness {
  insurers: string[];
  products_without_insurer: string[];
  plans_missing_report_label: { product_code: string; plan_code: string }[];
  employees_missing_nric: number;
  employees_missing_member_id: Record<string, number>;
  employee_count: number;
}

export function useReportReadiness(policyYearId: string | null) {
  // Scope the key by active client so a tenant switch reads a fresh cache
  // (matches every other tenant-scoped hook in the app).
  const cid = useSession((s) => s.activeClientId);
  return useQuery({
    queryKey: ["report-readiness", policyYearId, cid],
    queryFn: () =>
      api.get<ReportReadiness>(
        `/policy-years/${policyYearId}/reports/readiness`,
      ),
    enabled: Boolean(policyYearId),
  });
}
