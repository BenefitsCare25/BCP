import { useQuery } from "@tanstack/react-query";
import { api } from "@/api/client";
import { keepPreviousInScope } from "@/lib/scopedPlaceholder";
import { useSession } from "@/stores/session";
import type { CoverageLimits } from "@/types";

/** Unsaved setup limits to preview one product with (the setup form's values). */
export interface LimitPreview {
  productCode: string;
  employee_age_limit?: string;
  last_entry_age?: string;
  spouse_age_limit?: string;
  child_age_limit?: string;
  max_sum_insured?: string;
  employees_above_last_entry_age?: string;
}

/**
 * Who crosses a product's slip limits this benefit year — recomputed from the
 * roster, matching and setup on every fetch. Without a preview it is the whole
 * year (the bell, readiness, Member Coverage); with one it is a single product
 * checked against the limits the broker is typing.
 */
export function useCoverageLimits(
  policyYearId: string | null | undefined,
  preview?: LimitPreview,
) {
  const cid = useSession((s) => s.activeClientId);
  const params = new URLSearchParams();
  if (preview) {
    params.set("product_code", preview.productCode);
    for (const [key, value] of Object.entries(preview)) {
      if (key !== "productCode" && value !== undefined) params.set(key, value);
    }
  }
  const query = params.toString();
  return useQuery({
    queryKey: ["coverage-limits", policyYearId, cid, query],
    queryFn: () =>
      api.get<CoverageLimits>(
        `/policy-years/${policyYearId}/coverage-limits${query ? `?${query}` : ""}`,
      ),
    enabled: Boolean(policyYearId),
    // A preview re-queries as the broker types; keep the last answer on screen
    // instead of flashing an empty state between keystrokes — but never across
    // a company or benefit-year switch, where it would show the previous
    // company's crossings under the new company's name.
    ...(preview ? keepPreviousInScope(cid, policyYearId) : {}),
  });
}
