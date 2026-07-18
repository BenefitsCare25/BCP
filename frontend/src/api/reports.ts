import { useQuery } from "@tanstack/react-query";
import { api } from "@/api/client";

export interface ReportReadiness {
  insurers: string[];
  products_without_insurer: string[];
  plans_missing_report_label: { product_code: string; plan_code: string }[];
  employees_missing_nric: number;
  employees_missing_member_id: Record<string, number>;
  employee_count: number;
}

export function useReportReadiness(policyYearId: string | null) {
  return useQuery({
    queryKey: ["report-readiness", policyYearId],
    queryFn: () =>
      api.get<ReportReadiness>(
        `/policy-years/${policyYearId}/reports/readiness`,
      ),
    enabled: Boolean(policyYearId),
  });
}
