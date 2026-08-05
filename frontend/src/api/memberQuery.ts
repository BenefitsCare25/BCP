/**
 * Roster selection — the vocabulary, the live headcount, and pasted-list
 * resolution behind the member picker.
 *
 * A selection is a RULE (`MemberQuery`), not a list of people. The same object
 * goes to the count endpoint while the broker is composing it and to the bulk
 * preview/apply afterwards, so what they were shown and what runs are the same
 * query.
 */
import { useMutation, useQuery } from "@tanstack/react-query";
import { api } from "@/api/client";
import { useSession } from "@/stores/session";
import type { EmployeeList } from "@/types";

export type CoverageState = "any" | "default" | "overridden" | "declined";
export type MatchStatus = "any" | "matched" | "unmatched";

export interface AttributeFilter {
  key: string;
  values: string[];
  op?: "in" | "not_in";
}

export interface MemberQuery {
  q?: string | null;
  include_terminated?: boolean;
  category_ids?: string[];
  product_codes?: string[];
  current_plan_codes?: string[];
  coverage_state?: CoverageState;
  /** Whether roster matching found the member a cohort. */
  match_status?: MatchStatus;
  attributes?: AttributeFilter[];
  age?: { min?: number | null; max?: number | null } | null;
  /** Explicit additions (ticked rows, a pasted list). */
  employee_ids?: string[];
  staff_ids?: string[];
  /** Explicit removals, applied last — how unticking a row is expressed. */
  exclude_employee_ids?: string[];
}

export interface FacetValue {
  value: string;
  count: number;
}

export interface AttributeFacet {
  key: string;
  label: string;
  values: FacetValue[];
  truncated: boolean;
}

export interface CategoryFacet {
  id: string;
  label: string;
  product_code: string | null;
  count: number;
}

export interface PlanFacet {
  code: string;
  count: number;
}

export interface ProductFacet {
  id: string;
  code: string;
  name: string | null;
  covered: number;
  declined: number;
  plans: PlanFacet[];
}

export interface MemberFacets {
  employees_total: number;
  terminated_total: number;
  attributes: AttributeFacet[];
  categories: CategoryFacet[];
  products: ProductFacet[];
}

export interface UnresolvedRef {
  kind: "employee_id" | "staff_id";
  value: string;
  reason: string;
}

export interface MemberQueryCount {
  total: number;
  unresolved: UnresolvedRef[];
}

export interface ResolvedMember {
  id: string;
  staff_id: string;
  employee_name: string | null;
  matched_on: "staff_id" | "nric";
}

export interface MemberListResolve {
  matched: ResolvedMember[];
  unmatched: string[];
  duplicates: number;
}

/** Does this query select anybody at all? Mirrors the server's validator, so the
 *  picker can skip a request that would only ever come back 422. */
export function isQueryEmpty(query: MemberQuery): boolean {
  return !(
    query.q ||
    query.category_ids?.length ||
    query.product_codes?.length ||
    query.current_plan_codes?.length ||
    query.attributes?.length ||
    query.age ||
    (query.coverage_state && query.coverage_state !== "any") ||
    (query.match_status && query.match_status !== "any") ||
    query.employee_ids?.length ||
    query.staff_ids?.length
  );
}

/**
 * A filtered page of the roster.
 *
 * POST-for-read because `attributes[]` and `age` are nested and don't survive a
 * query string — the count and resolve endpoints are POST for the same reason.
 * Unlike `useMemberQueryCount` this accepts an EMPTY query: for a listing that
 * is the default view, not a footgun.
 */
export function useMemberQueryList(
  policyYearId: string | undefined,
  query: MemberQuery,
  page: { offset: number; limit: number },
  productCode?: string,
) {
  const clientId = useSession((s) => s.activeClientId);
  return useQuery({
    // Prefixed "employees" so every existing
    // `invalidateQueries({queryKey: ["employees"]})` in api/hooks.ts also
    // refreshes this listing. Without the prefix the table silently kept
    // showing stale rows after a save, a delete or a matching run.
    queryKey: [
      "employees",
      "query",
      clientId,
      policyYearId,
      productCode ?? null,
      page.offset,
      page.limit,
      query,
    ],
    queryFn: () =>
      api.post<EmployeeList>(
        `/policy-years/${policyYearId}/member-query/list`,
        { query, product_code: productCode ?? null, ...page },
      ),
    enabled: !!policyYearId,
    // Keep the previous page on screen while the next one loads, so changing a
    // filter doesn't blank the table under the broker.
    placeholderData: (prev) => prev,
  });
}

export function useMemberFacets(policyYearId: string | undefined) {
  const clientId = useSession((s) => s.activeClientId);
  return useQuery({
    // The active client is in the key: every tenant-scoped query must be, or a
    // company switch serves the previous company's roster from cache.
    queryKey: ["employees", "facets", clientId, policyYearId],
    queryFn: () =>
      api.get<MemberFacets>(`/policy-years/${policyYearId}/member-facets`),
    enabled: !!policyYearId,
  });
}

/** Live "N members match", refreshed as the filters change. */
export function useMemberQueryCount(
  policyYearId: string | undefined,
  query: MemberQuery,
  productCode: string | undefined,
) {
  const clientId = useSession((s) => s.activeClientId);
  const enabled = !!policyYearId && !isQueryEmpty(query);
  return useQuery({
    queryKey: ["employees", "count", clientId, policyYearId, productCode, query],
    queryFn: () =>
      api.post<MemberQueryCount>(
        `/policy-years/${policyYearId}/member-query/count`,
        { query, product_code: productCode ?? null },
      ),
    enabled,
    // The headcount is a readout, not state to act on — keep the previous number
    // on screen while the next one loads instead of flashing a skeleton on every
    // keystroke.
    placeholderData: (prev) => prev,
  });
}

export function useResolveMemberList(policyYearId: string | undefined) {
  return useMutation({
    mutationFn: (body: { text: string; include_terminated?: boolean }) =>
      api.post<MemberListResolve>(
        `/policy-years/${policyYearId}/member-query/resolve`,
        body,
      ),
  });
}
