/**
 * Filters for the Dependants tab of Member Listing.
 *
 * Three filters — **status, role, employee cohort** — beside the search box and
 * the Linked/Unlinked chips. Two of them close real gaps rather than adding
 * convenience:
 *
 * - **Status** reaches the pending portal self-adds, which today exist ONLY in
 *   the approvals card, and the rejected/terminated rows, which have no UI at
 *   all.
 * - **Employee cohort** is the nested member query, which is what makes a
 *   category filter mean anything on this tab: a dependant's category is its
 *   employee's category.
 *
 * `DependantFilterState` and the server carry more (raw relationship, link
 * method, an age window, the full employee query); adding one back is a control
 * here and nothing else.
 */
import { useState } from "react";
import { ChevronDown, Filter, X } from "lucide-react";
import type {
  DependantFacets,
  DependantQuery,
  DependantRole,
  DependantStatus,
  LinkState,
} from "@/api/dependantQuery";
import type { MemberFacets } from "@/api/memberQuery";
import {
  type MemberFilterState,
  EMPTY_MEMBER_FILTERS,
  toMemberQuery,
} from "@/lib/memberFilters";
import { cn } from "@/lib/cn";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Segmented } from "@/components/ui/segmented";
import { MatchSetPicker } from "@/components/configuration/flex/MatchSetPicker";

export type DependantFilterState = {
  q: string;
  statuses: DependantStatus[];
  relationships: string[];
  roles: DependantRole[];
  linkState: LinkState;
  linkMethods: string[];
  age: { min: number | null; max: number | null };
  /** The sponsoring employee, as the same state the Employees tab uses. */
  employee: MemberFilterState;
};

export const EMPTY_DEPENDANT_FILTERS: DependantFilterState = {
  q: "",
  statuses: [],
  relationships: [],
  roles: [],
  linkState: "any",
  linkMethods: [],
  age: { min: null, max: null },
  employee: EMPTY_MEMBER_FILTERS,
};

const STATUS_LABELS: Record<DependantStatus, string> = {
  active: "Active",
  pending_approval: "Pending approval",
  rejected: "Rejected",
  terminated: "Terminated",
};

const ROLE_LABELS: Record<DependantRole, string> = {
  spouse: "Spouse",
  child: "Child",
  other: "Other",
};

const LINK_STATES: { value: LinkState; label: string }[] = [
  { value: "any", label: "All" },
  { value: "linked", label: "Linked" },
  { value: "unlinked", label: "Unlinked" },
];

export function toDependantQuery(state: DependantFilterState): DependantQuery {
  const employee = toMemberQuery(state.employee);
  const employeeIsSet =
    !!employee.q ||
    !!employee.category_ids?.length ||
    !!employee.product_codes?.length ||
    !!employee.current_plan_codes?.length ||
    !!employee.attributes?.length ||
    !!employee.age ||
    employee.coverage_state !== "any" ||
    employee.match_status !== "any";
  return {
    q: state.q.trim() || null,
    statuses: state.statuses,
    relationships: state.relationships,
    roles: state.roles,
    link_state: state.linkState,
    link_methods: state.linkMethods,
    age:
      state.age.min === null && state.age.max === null
        ? null
        : { min: state.age.min, max: state.age.max },
    // Sent only when actually set: an employee filter necessarily drops every
    // unlinked dependant, so an empty one would silently hide them.
    employee: employeeIsSet ? employee : null,
  };
}

export function dependantFiltersAreEmpty(s: DependantFilterState): boolean {
  return (
    !s.q.trim() &&
    !s.statuses.length &&
    !s.relationships.length &&
    !s.roles.length &&
    !s.linkMethods.length &&
    s.linkState === "any" &&
    s.age.min === null &&
    s.age.max === null &&
    !toDependantQuery(s).employee
  );
}

type Props = {
  state: DependantFilterState;
  onChange: (next: DependantFilterState) => void;
  facets: DependantFacets | undefined;
  memberFacets: MemberFacets | undefined;
  total: number | undefined;
};

export function DependantFilterBar({
  state,
  onChange,
  facets,
  memberFacets,
  total,
}: Props) {
  const [open, setOpen] = useState(false);
  const chips = buildChips(state, onChange, memberFacets);
  const isEmpty = dependantFiltersAreEmpty(state);

  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-center gap-2">
        <Input
          value={state.q}
          onChange={(e) => onChange({ ...state, q: e.target.value })}
          placeholder="Search name, NRIC, staff ID…"
          aria-label="Search dependants"
          className="h-8 w-[240px]"
        />
        <Segmented
          value={state.linkState}
          onChange={(linkState) => onChange({ ...state, linkState })}
          options={LINK_STATES}
        />
        <Button
          type="button"
          variant={open ? "default" : "outline"}
          size="sm"
          onClick={() => setOpen((v) => !v)}
        >
          <Filter className="size-3.5" />
          Filters
          {chips.length > 0 && (
            <Badge variant="info" className="ml-1">
              {chips.length}
            </Badge>
          )}
          <ChevronDown
            className={cn("size-3.5 transition-transform", open && "rotate-180")}
          />
        </Button>
        <span className="text-sm text-muted-foreground">
          {total === undefined ? "…" : `${total.toLocaleString()} matching`}
        </span>
        {!isEmpty && (
          <Button
            type="button"
            variant="ghost"
            size="sm"
            onClick={() => onChange(EMPTY_DEPENDANT_FILTERS)}
          >
            Clear all
          </Button>
        )}
      </div>

      {chips.length > 0 && (
        <div className="flex flex-wrap items-center gap-1.5">
          {chips.map((chip) => (
            <button
              key={chip.key}
              type="button"
              onClick={chip.clear}
              className="focus-ring inline-flex items-center gap-1 rounded-full border border-input bg-muted px-2 py-0.5 text-2xs text-foreground/80 hover:bg-accent"
            >
              {chip.label}
              <X className="size-3" />
            </button>
          ))}
        </div>
      )}

      {open && (
        <div className="grid gap-4 rounded-lg border border-border bg-muted/40 p-4 md:grid-cols-3">
          <div className="space-y-1">
            <Label className="block">Status</Label>
            <div className="flex flex-wrap gap-1.5">
              {(Object.keys(STATUS_LABELS) as DependantStatus[]).map((status) => {
                const on = state.statuses.includes(status);
                const count = facets?.statuses.find((s) => s.value === status)?.count;
                return (
                  <button
                    key={status}
                    type="button"
                    onClick={() =>
                      onChange({
                        ...state,
                        statuses: on
                          ? state.statuses.filter((s) => s !== status)
                          : [...state.statuses, status],
                      })
                    }
                    className={cn(
                      "focus-ring rounded-full border px-2.5 py-1 text-2xs",
                      on
                        ? "border-primary bg-primary text-primary-foreground"
                        : "border-input bg-card text-foreground/80 hover:bg-accent",
                    )}
                  >
                    {STATUS_LABELS[status]}
                    {count !== undefined && (
                      <span className={cn("ml-1", !on && "text-subtle")}>{count}</span>
                    )}
                  </button>
                );
              })}
            </div>
            <p className="text-2xs text-subtle">
              Nothing ticked shows active dependants only.
            </p>
          </div>

          <div className="space-y-1">
            <Label className="block">Role</Label>
            <div className="flex flex-wrap gap-1.5">
              {(Object.keys(ROLE_LABELS) as DependantRole[]).map((role) => {
                const on = state.roles.includes(role);
                const count = facets?.roles.find((r) => r.value === role)?.count;
                return (
                  <button
                    key={role}
                    type="button"
                    onClick={() =>
                      onChange({
                        ...state,
                        roles: on
                          ? state.roles.filter((r) => r !== role)
                          : [...state.roles, role],
                      })
                    }
                    className={cn(
                      "focus-ring rounded-full border px-2.5 py-1 text-2xs",
                      on
                        ? "border-primary bg-primary text-primary-foreground"
                        : "border-input bg-card text-foreground/80 hover:bg-accent",
                    )}
                  >
                    {ROLE_LABELS[role]}
                    {count !== undefined && (
                      <span className={cn("ml-1", !on && "text-subtle")}>{count}</span>
                    )}
                  </button>
                );
              })}
            </div>
            <p className="text-2xs text-subtle">
              From the roster's wording. “Other” is where parents and
              unrecognised relationships land.
            </p>
          </div>

          <MatchSetPicker
            label="Employee cohort"
            hint="Filters by the SPONSORING employee's category — which necessarily hides unlinked dependants, since they have no employee to test."
            selected={state.employee.categoryIds}
            options={(memberFacets?.categories ?? []).map((c) => ({
              value: c.id,
              count: c.count,
            }))}
            renderValue={(id) => {
              const cat = memberFacets?.categories.find((c) => c.id === id);
              if (!cat) return "Unknown cohort";
              return cat.product_code
                ? `${cat.product_code} · ${cat.label}`
                : cat.label;
            }}
            onChange={(categoryIds) =>
              onChange({ ...state, employee: { ...state.employee, categoryIds } })
            }
            placeholder="Any cohort"
          />
        </div>
      )}
    </div>
  );
}

type Chip = { key: string; label: string; clear: () => void };

function buildChips(
  state: DependantFilterState,
  set: (next: DependantFilterState) => void,
  memberFacets: MemberFacets | undefined,
): Chip[] {
  const out: Chip[] = [];
  for (const status of state.statuses) {
    out.push({
      key: `st:${status}`,
      label: STATUS_LABELS[status],
      clear: () =>
        set({ ...state, statuses: state.statuses.filter((s) => s !== status) }),
    });
  }
  for (const role of state.roles) {
    out.push({
      key: `role:${role}`,
      label: ROLE_LABELS[role],
      clear: () => set({ ...state, roles: state.roles.filter((r) => r !== role) }),
    });
  }
  for (const rel of state.relationships) {
    out.push({
      key: `rel:${rel}`,
      label: rel,
      clear: () =>
        set({
          ...state,
          relationships: state.relationships.filter((r) => r !== rel),
        }),
    });
  }
  for (const method of state.linkMethods) {
    out.push({
      key: `lm:${method}`,
      label: `via ${method}`,
      clear: () =>
        set({
          ...state,
          linkMethods: state.linkMethods.filter((m) => m !== method),
        }),
    });
  }
  if (state.linkState !== "any") {
    out.push({
      key: "link",
      label: state.linkState === "linked" ? "Linked" : "Unlinked",
      clear: () => set({ ...state, linkState: "any" }),
    });
  }
  if (state.age.min !== null || state.age.max !== null) {
    const { min, max } = state.age;
    out.push({
      key: "age",
      label:
        min !== null && max !== null
          ? `Age ${min}–${max}`
          : min !== null
            ? `Age ${min}+`
            : `Age up to ${max}`,
      clear: () => set({ ...state, age: { min: null, max: null } }),
    });
  }
  for (const id of state.employee.categoryIds) {
    const cat = memberFacets?.categories.find((c) => c.id === id);
    out.push({
      key: `ecat:${id}`,
      label: `Employee: ${cat?.label ?? "cohort"}`,
      clear: () =>
        set({
          ...state,
          employee: {
            ...state.employee,
            categoryIds: state.employee.categoryIds.filter((c) => c !== id),
          },
        }),
    });
  }
  for (const code of state.employee.productCodes) {
    out.push({
      key: `eprod:${code}`,
      label: `Employee: ${code}`,
      clear: () =>
        set({
          ...state,
          employee: {
            ...state.employee,
            productCodes: state.employee.productCodes.filter((p) => p !== code),
          },
        }),
    });
  }
  for (const [key, values] of Object.entries(state.employee.attributes)) {
    if (!values.length) continue;
    const label =
      memberFacets?.attributes.find((a) => a.key === key)?.label ?? key;
    out.push({
      key: `eattr:${key}`,
      label: `Employee ${label}: ${values.join(", ")}`,
      clear: () => {
        const attributes = { ...state.employee.attributes };
        delete attributes[key];
        set({ ...state, employee: { ...state.employee, attributes } });
      },
    });
  }
  return out;
}
