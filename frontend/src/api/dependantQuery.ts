/**
 * Dependant selection — the Dependants tab's filter vocabulary and its rows.
 *
 * The employee half of the query is a nested `MemberQuery`, not a parallel
 * vocabulary, so filtering dependants by cohort/product/plan/entity resolves
 * through exactly the chain the Employees tab uses.
 */
import { useQuery } from "@tanstack/react-query";
import { api } from "@/api/client";
import { useSession } from "@/stores/session";
import type { FacetValue, MemberQuery } from "@/api/memberQuery";
import type { DependantList } from "@/types";

export type DependantStatus =
  | "active"
  | "pending_approval"
  | "rejected"
  | "terminated";
export type DependantRole = "spouse" | "child" | "other";
export type LinkState = "any" | "linked" | "unlinked";

export interface DependantQuery {
  q?: string | null;
  /** EMPTY means the default view: active only. There is no "all" token — the
   *  UI ticks every box, so what was asked for is always explicit. */
  statuses?: DependantStatus[];
  /** Raw roster wording ("Spouse", "Son"). Served from the facets, never
   *  hardcoded — the vocabulary is free text with no enum. */
  relationships?: string[];
  roles?: DependantRole[];
  link_state?: LinkState;
  link_methods?: string[];
  age?: { min?: number | null; max?: number | null } | null;
  /** The SPONSORING employee. Setting it necessarily excludes unlinked
   *  dependants — there is no employee to test them against. */
  employee?: MemberQuery | null;
}

export interface DependantFacets {
  active_total: number;
  all_statuses_total: number;
  linked: number;
  unlinked: number;
  /** Spans every status — it is the control that WIDENS the population. */
  statuses: FacetValue[];
  relationships: FacetValue[];
  roles: FacetValue[];
  link_methods: FacetValue[];
}

export function useDependantFacets(policyYearId: string | undefined) {
  const clientId = useSession((s) => s.activeClientId);
  return useQuery({
    queryKey: ["dependants", "facets", clientId, policyYearId],
    queryFn: () =>
      api.get<DependantFacets>(`/policy-years/${policyYearId}/dependant-facets`),
    enabled: !!policyYearId,
  });
}

export function useDependantQueryList(
  policyYearId: string | undefined,
  query: DependantQuery,
  page: { offset: number; limit: number },
) {
  const clientId = useSession((s) => s.activeClientId);
  return useQuery({
    // Prefixed "dependants" — see the note on the member listing key.
    queryKey: [
      "dependants",
      "query",
      clientId,
      policyYearId,
      page.offset,
      page.limit,
      query,
    ],
    queryFn: () =>
      api.post<DependantList>(
        `/policy-years/${policyYearId}/dependant-query/list`,
        { query, ...page },
      ),
    enabled: !!policyYearId,
    placeholderData: (prev) => prev,
  });
}
