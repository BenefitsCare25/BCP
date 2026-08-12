/**
 * Dual coverage — lives insured twice under one company.
 *
 * Detection is computed server-side on every read; only the broker's DECISION is
 * stored. `unresolved_cases` counts CASES only — opportunities (married
 * colleagues whose child is listed once) are the normal state of such a family
 * and would bury the real duplicates if counted.
 */
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "@/api/client";
import { useSession } from "@/stores/session";

export interface DualParty {
  employee_id: string | null;
  staff_id: string;
  employee_name: string | null;
  dependant_id: string | null;
  relationship: string | null;
  covered: boolean;
  covered_products: string[];
  /** Sponsor is not an active employee, or the row is unlinked: evidence the
   *  duplicate exists, never a live coverage line. */
  unlinked: boolean;
}

export interface DualDecision {
  decision: "carried_by" | "intentional_both" | "not_a_match" | "dismissed";
  carried_by_employee_id: string | null;
  carried_by_staff_id: string | null;
  note: string | null;
  decided_by: string | null;
  /** The decider's name. `decided_by` is a uuid and must never be printed. */
  decided_by_name: string | null;
  decided_at: string | null;
  /** The family changed after the decision was taken, so it no longer describes
   *  the situation and the case is counted as unresolved again. */
  stale: boolean;
}

export interface DualCase {
  subject_key: string;
  name: string;
  dob: string | null;
  nric_masked: string | null;
  relationship: string | null;
  match_tier: "nric" | "name_dob";
  flags: ("listed_twice" | "employee_as_spouse")[];
  parties: DualParty[];
  overlapping_products: string[];
  severity: "warn" | "info";
  decision: DualDecision | null;
}

/** One dependant ROW's membership in a shared life — enough for a roster table
 *  to mark the row and name every employee the life reaches. Never capped
 *  server-side: the table is paginated, so a cap would leave later pages
 *  silently unmarked. */
export interface DualLifeRef {
  dependant_id: string;
  subject_key: string;
  severity: "warn" | "info";
  resolved: boolean;
  parties: DualParty[];
}

export interface DualOpportunity {
  subject_key: string;
  employees: DualParty[];
  child_name: string;
  child_dob: string | null;
  listed_under_staff_id: string;
  other_staff_id: string;
  decision: DualDecision | null;
}

export interface DualCoverage {
  unresolved_cases: number;
  total_cases: number;
  total_opportunities: number;
  cases: DualCase[];
  opportunities: DualOpportunity[];
  preview_cap: number;
  lives: DualLifeRef[];
}

/** dependant row id → the shared life it belongs to. */
export function livesByDependant(data?: DualCoverage): Map<string, DualLifeRef> {
  return new Map((data?.lives ?? []).map((l) => [l.dependant_id, l]));
}

/**
 * `focus` names one case the response must contain whatever `preview_cap` does.
 * The table marks its rows from `lives`, which is uncapped, so without it a row
 * past the cap opened the sheet onto a case that wasn't in `cases` — reported as
 * "Nothing open for this person" on the row that had just been clicked.
 */
export function useDualCoverage(
  policyYearId: string | undefined,
  focus?: string | null,
) {
  const clientId = useSession((s) => s.activeClientId);
  return useQuery({
    queryKey: ["dependants", "dual-coverage", clientId, policyYearId, focus ?? null],
    queryFn: () =>
      api.get<DualCoverage>(
        `/policy-years/${policyYearId}/dual-coverage${
          focus ? `?focus=${encodeURIComponent(focus)}` : ""
        }`,
      ),
    enabled: !!policyYearId,
  });
}

export function useRecordDualDecision(policyYearId: string | undefined) {
  const qc = useQueryClient();
  const clientId = useSession((s) => s.activeClientId);
  return useMutation({
    mutationFn: (body: {
      subject_key: string;
      subject_kind?: "life" | "couple";
      decision: DualDecision["decision"];
      carried_by_employee_id?: string | null;
      note?: string | null;
    }) =>
      api.post(`/policy-years/${policyYearId}/dual-coverage/decisions`, body),
    onSuccess: () =>
      qc.invalidateQueries({
        queryKey: ["dependants", "dual-coverage", clientId, policyYearId],
      }),
  });
}

/**
 * Drop or restore ONE side's cover for a life listed under two employees.
 *
 * This is the only control here that MOVES MONEY — the decision buttons record
 * who carries the life, this changes who pays for them. Both rows stay on the
 * roster either way. Invalidates the coverage-bearing queries too, since a
 * dropped side changes the benefit statement and the member's flex tier.
 */
export function useSetDualCover(policyYearId: string | undefined) {
  const qc = useQueryClient();
  const clientId = useSession((s) => s.activeClientId);
  return useMutation({
    mutationFn: ({
      dependantId,
      covered,
    }: {
      dependantId: string;
      covered: boolean;
    }) =>
      api.put<{ covered: boolean; products_changed: string[] }>(
        `/policy-years/${policyYearId}/dual-coverage/dependants/${dependantId}/cover`,
        { covered },
      ),
    onSuccess: () => {
      qc.invalidateQueries({
        queryKey: ["dependants", "dual-coverage", clientId, policyYearId],
      });
      for (const key of [
        "benefit-statement",
        "plan-overrides",
        "flex-membership",
        "flex-coverage",
        "employees",
      ]) {
        qc.invalidateQueries({ queryKey: [key] });
      }
    },
  });
}

export function useReopenDualDecision(policyYearId: string | undefined) {
  const qc = useQueryClient();
  const clientId = useSession((s) => s.activeClientId);
  return useMutation({
    mutationFn: (subjectKey: string) =>
      api.delete(
        `/policy-years/${policyYearId}/dual-coverage/decisions/${subjectKey}`,
      ),
    onSuccess: () =>
      qc.invalidateQueries({
        queryKey: ["dependants", "dual-coverage", clientId, policyYearId],
      }),
  });
}
