import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
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

// The API's caps on the preview values (schemas/coverage_limits.py). Limit
// answers are free text whose first number is the limit, so a clamp to the cap
// keeps long wording checkable instead of failing the whole preview.
const LIMIT_TEXT_MAX = 1000;
const EXEMPT_LIST_MAX = 4000;

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
      if (key === "productCode" || value === undefined) continue;
      const max =
        key === "employees_above_last_entry_age" ? EXEMPT_LIST_MAX : LIMIT_TEXT_MAX;
      params.set(key, value.slice(0, max));
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

export interface DependantCoverDecision {
  dependantId: string;
  productCode: string;
  decision: "keep" | "exclude";
  reason?: string;
}

/** Everything that shows who a dependant's cover includes. */
function invalidateCover(qc: ReturnType<typeof useQueryClient>) {
  for (const key of ["coverage-limits", "benefit-statement", "categories", "member-counts"]) {
    qc.invalidateQueries({ queryKey: [key] });
  }
}

/** Keep a dependant above an age limit covered (saying why) or exclude them. */
export function useSetDependantCoverDecision(policyYearId: string | null | undefined) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: DependantCoverDecision) =>
      api.put(`/policy-years/${policyYearId}/dependant-cover-decisions`, {
        dependant_id: body.dependantId,
        product_code: body.productCode,
        decision: body.decision,
        reason: body.reason ?? null,
      }),
    onSuccess: () => invalidateCover(qc),
  });
}

/** Undo a decision: the dependant needs one again. */
export function useClearDependantCoverDecision(policyYearId: string | null | undefined) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ dependantId, productCode }: { dependantId: string; productCode: string }) =>
      api.delete(
        `/policy-years/${policyYearId}/dependant-cover-decisions/${encodeURIComponent(
          dependantId,
        )}/${encodeURIComponent(productCode)}`,
      ),
    onSuccess: () => invalidateCover(qc),
  });
}
