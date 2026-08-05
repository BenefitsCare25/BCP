/**
 * Filter state for the Member Listing page, and its translation to the wire.
 *
 * The page state is a superset of the bulk picker's: it adds match state,
 * products and an age window, and drops the bulk-only affordances (a pasted
 * list, ticked-row exclusions) that a listing has no use for.
 *
 * What must NOT drift between the two surfaces is the MEANING of a filter, and
 * that lives server-side — both serialize to the same `MemberFilters` and are
 * resolved by the same `_filtered` predicate chain. So a broker filtering the
 * roster and a broker selecting for a bulk change can never be shown different
 * populations for the same rule.
 */
import type { CoverageState, MatchStatus, MemberQuery } from "@/api/memberQuery";

export type AgeWindow = { min: number | null; max: number | null };

export type MemberFilterState = {
  q: string;
  includeTerminated: boolean;
  matchStatus: MatchStatus;
  categoryIds: string[];
  productCodes: string[];
  currentPlanCodes: string[];
  coverageState: CoverageState;
  attributes: Record<string, string[]>;
  age: AgeWindow;
};

export const EMPTY_MEMBER_FILTERS: MemberFilterState = {
  q: "",
  includeTerminated: false,
  matchStatus: "any",
  categoryIds: [],
  productCodes: [],
  currentPlanCodes: [],
  coverageState: "any",
  attributes: {},
  age: { min: null, max: null },
};

export const COVERAGE_STATES: { value: CoverageState; label: string }[] = [
  { value: "any", label: "Everyone" },
  { value: "default", label: "On their cohort default" },
  { value: "overridden", label: "Deviating from their cohort" },
  { value: "declined", label: "Currently declined" },
];

export const MATCH_STATES: { value: MatchStatus; label: string }[] = [
  { value: "any", label: "All" },
  { value: "matched", label: "Matched" },
  { value: "unmatched", label: "Unmatched" },
];

function ageOrNull(age: AgeWindow): MemberQuery["age"] {
  // The server rejects an age filter with neither bound, so an untouched window
  // must go over as absent rather than as an empty object.
  if (age.min === null && age.max === null) return null;
  return { min: age.min, max: age.max };
}

/** One builder, shared by the row query and the live headcount — two readings
 *  of "who is selected" is how they drift apart. */
export function toMemberQuery(state: MemberFilterState): MemberQuery {
  return {
    q: state.q.trim() || null,
    include_terminated: state.includeTerminated,
    match_status: state.matchStatus,
    category_ids: state.categoryIds,
    product_codes: state.productCodes,
    current_plan_codes: state.currentPlanCodes,
    coverage_state: state.coverageState,
    attributes: Object.entries(state.attributes)
      .filter(([, values]) => values.length > 0)
      .map(([key, values]) => ({ key, values })),
    age: ageOrNull(state.age),
  };
}

/** Filters the broker has actually set — what the bar prints as removable
 *  chips. `q` is deliberately excluded: it has its own always-visible box, and
 *  echoing it as a chip reads as a second, separate filter. */
export type ActiveFilter = { key: string; label: string; clear: () => void };

export function activeFilters(
  state: MemberFilterState,
  set: (next: MemberFilterState) => void,
  labels: {
    category: (id: string) => string;
    attribute: (key: string) => string;
  },
): ActiveFilter[] {
  const out: ActiveFilter[] = [];
  if (state.includeTerminated) {
    out.push({
      key: "terminated",
      label: "Including leavers",
      clear: () => set({ ...state, includeTerminated: false }),
    });
  }
  if (state.matchStatus !== "any") {
    out.push({
      key: "match",
      label: state.matchStatus === "matched" ? "Matched" : "Unmatched",
      clear: () => set({ ...state, matchStatus: "any" }),
    });
  }
  for (const id of state.categoryIds) {
    out.push({
      key: `cat:${id}`,
      label: labels.category(id),
      clear: () =>
        set({ ...state, categoryIds: state.categoryIds.filter((c) => c !== id) }),
    });
  }
  for (const code of state.productCodes) {
    out.push({
      key: `prod:${code}`,
      label: code,
      clear: () =>
        set({
          ...state,
          productCodes: state.productCodes.filter((p) => p !== code),
        }),
    });
  }
  for (const code of state.currentPlanCodes) {
    out.push({
      key: `plan:${code}`,
      label: `Plan ${code}`,
      clear: () =>
        set({
          ...state,
          currentPlanCodes: state.currentPlanCodes.filter((p) => p !== code),
        }),
    });
  }
  if (state.coverageState !== "any") {
    const label =
      COVERAGE_STATES.find((c) => c.value === state.coverageState)?.label ?? "";
    out.push({
      key: "coverage",
      label,
      clear: () => set({ ...state, coverageState: "any" }),
    });
  }
  for (const [key, values] of Object.entries(state.attributes)) {
    if (!values.length) continue;
    out.push({
      key: `attr:${key}`,
      label: `${labels.attribute(key)}: ${values.join(", ")}`,
      clear: () => {
        const next = { ...state.attributes };
        delete next[key];
        set({ ...state, attributes: next });
      },
    });
  }
  if (state.age.min !== null || state.age.max !== null) {
    const { min, max } = state.age;
    const label =
      min !== null && max !== null
        ? `Age ${min}–${max}`
        : min !== null
          ? `Age ${min}+`
          : `Age up to ${max}`;
    out.push({
      key: "age",
      label,
      clear: () => set({ ...state, age: { min: null, max: null } }),
    });
  }
  return out;
}

/** Does the bar show any filter at all (search included)? Drives the "Clear
 *  all" affordance and the empty-state copy. */
export function memberFiltersAreEmpty(state: MemberFilterState): boolean {
  return (
    !state.q.trim() &&
    !state.includeTerminated &&
    state.matchStatus === "any" &&
    !state.categoryIds.length &&
    !state.productCodes.length &&
    !state.currentPlanCodes.length &&
    state.coverageState === "any" &&
    !Object.values(state.attributes).some((v) => v.length) &&
    state.age.min === null &&
    state.age.max === null
  );
}
