/** Category mutations used outside the per-product category editor. */
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { api } from "@/api/client";

export interface BulkConfirmResult {
  confirmed: number;
  skipped_invalid_rules: number;
  threshold: number;
}

/** Confirms exactly the rows the mapping summary flags `bulk_confirmable`. */
export function useBulkConfirmCategories() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (policyYearId: string) =>
      api.post<BulkConfirmResult>(
        `/categories/bulk-confirm?policy_year_id=${encodeURIComponent(policyYearId)}`,
        {},
      ),
    onSettled: () => {
      qc.invalidateQueries({ queryKey: ["categories"] });
      qc.invalidateQueries({ queryKey: ["eligibility-mappings"] });
      qc.invalidateQueries({ queryKey: ["policy-year-readiness"] });
      qc.invalidateQueries({ queryKey: ["member-counts"] });
      qc.invalidateQueries({ queryKey: ["audit-log"] });
    },
  });
}
