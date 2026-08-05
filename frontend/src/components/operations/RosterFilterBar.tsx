/**
 * Filters for the Employees tab of Member Listing.
 *
 * Three filters — **cohort, product, entity** — beside the search box and the
 * match chips the tab already had. The wire model (`MemberFilterState` →
 * `MemberFilters`) carries far more (plan, coverage state, age, every roster
 * attribute) and the server resolves all of it; this bar deliberately renders
 * only the three a broker reaches for daily. Adding one back is a picker here,
 * nothing else — no API or state change.
 *
 * Collapsed by default: a picker grid left open pushes the table it filters
 * below the fold, the mistake the coverage-pane rebuild was undoing. What is
 * set stays visible as removable chips, so a filtered view never lies about
 * being filtered.
 *
 * Every vocabulary is SERVED (`/member-facets`) with headcounts — nothing is
 * hardcoded, because a value that isn't on this roster matches nobody and has
 * no business being offered.
 */
import { useMemo, useState } from "react";
import { ChevronDown, Filter, X } from "lucide-react";
import type { MemberFacets } from "@/api/memberQuery";
import {
  MATCH_STATES,
  type MemberFilterState,
  activeFilters,
  memberFiltersAreEmpty,
} from "@/lib/memberFilters";
import { cn } from "@/lib/cn";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Segmented } from "@/components/ui/segmented";
import { MatchSetPicker } from "@/components/configuration/flex/MatchSetPicker";

/** The roster's legal-entity column, as the parser names it. */
const ENTITY_KEY = "entity";

type Props = {
  state: MemberFilterState;
  onChange: (next: MemberFilterState) => void;
  facets: MemberFacets | undefined;
  facetsLoading: boolean;
  /** The filtered headcount — the list response's own total, so the number the
   *  bar states and the rows below it can never disagree. */
  total: number | undefined;
};

export function RosterFilterBar({
  state,
  onChange,
  facets,
  facetsLoading,
  total,
}: Props) {
  const [open, setOpen] = useState(false);

  const categoryLabel = useMemo(() => {
    const byId = new Map((facets?.categories ?? []).map((c) => [c.id, c]));
    return (id: string) => {
      const cat = byId.get(id);
      if (!cat) return "Unknown cohort";
      return cat.product_code ? `${cat.product_code} · ${cat.label}` : cat.label;
    };
  }, [facets]);

  const attributeLabel = useMemo(() => {
    const byKey = new Map((facets?.attributes ?? []).map((a) => [a.key, a.label]));
    return (key: string) => byKey.get(key) ?? key;
  }, [facets]);

  const chips = activeFilters(state, onChange, {
    category: categoryLabel,
    attribute: attributeLabel,
  });
  const isEmpty = memberFiltersAreEmpty(state);

  // The roster attribute worth a slot of its own. Every other served facet
  // (department, cost centre, job grade, marital status…) is a valid filter the
  // wire model already carries — see `MemberFilterState` — but rendering all of
  // them made the panel longer than the table it filters.
  const entityFacet = facets?.attributes.find((a) => a.key === ENTITY_KEY);

  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-center gap-2">
        <Input
          value={state.q}
          onChange={(e) => onChange({ ...state, q: e.target.value })}
          placeholder="Search staff ID, name or NRIC…"
          aria-label="Search members"
          className="h-8 w-[260px]"
        />
        <Segmented
          value={state.matchStatus}
          onChange={(value) => onChange({ ...state, matchStatus: value })}
          options={MATCH_STATES}
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
            onClick={() => onChange({ ...state, ...CLEARED })}
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
          <MatchSetPicker
            label="Cohort"
            hint="The category roster matching put each member in."
            selected={state.categoryIds}
            options={(facets?.categories ?? []).map((c) => ({
              value: c.id,
              count: c.count,
            }))}
            renderValue={categoryLabel}
            onChange={(categoryIds) => onChange({ ...state, categoryIds })}
            placeholder={facetsLoading ? "Loading cohorts…" : "Any cohort"}
          />
          <MatchSetPicker
            label="Product"
            hint="Members covered by ALL of the products you pick."
            selected={state.productCodes}
            options={(facets?.products ?? []).map((p) => ({
              value: p.code,
              count: p.covered,
            }))}
            onChange={(productCodes) => onChange({ ...state, productCodes })}
            placeholder={facetsLoading ? "Loading products…" : "Any product"}
          />
          {entityFacet && (
            <MatchSetPicker
              label={entityFacet.label}
              hint="The legal entity the roster puts each member under."
              selected={state.attributes[entityFacet.key] ?? []}
              options={entityFacet.values.map((v) => ({
                value: v.value,
                count: v.count,
              }))}
              onChange={(values) => {
                const attributes = { ...state.attributes };
                if (values.length) attributes[entityFacet.key] = values;
                else delete attributes[entityFacet.key];
                onChange({ ...state, attributes });
              }}
              placeholder={`Any ${entityFacet.label.toLowerCase()}`}
              unknownNote={
                entityFacet.truncated ? " · not in the top values" : undefined
              }
            />
          )}

          <label className="flex items-center gap-2 text-sm text-foreground/80 md:col-span-3">
            <input
              type="checkbox"
              className="focus-ring size-4 rounded border-input"
              checked={state.includeTerminated}
              onChange={(e) =>
                onChange({ ...state, includeTerminated: e.target.checked })
              }
            />
            Include leavers
            {facets ? (
              <span className="text-subtle">({facets.terminated_total})</span>
            ) : null}
          </label>
        </div>
      )}
    </div>
  );
}

/** Everything the bar can set, back to its default. Spread rather than
 *  replacing the whole object so a caller-held field can't be dropped here. */
const CLEARED = {
  q: "",
  includeTerminated: false,
  matchStatus: "any",
  categoryIds: [],
  productCodes: [],
  currentPlanCodes: [],
  coverageState: "any",
  attributes: {},
  age: { min: null, max: null },
} satisfies MemberFilterState;
