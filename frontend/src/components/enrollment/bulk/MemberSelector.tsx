/**
 * Compose a member selection as a RULE.
 *
 * The tool this replaces offered two ways to pick people: "everyone currently on
 * plan X", or paste every staff ID. Anything in between — a department, a grade,
 * a cohort, that list minus two people — meant keying members one at a time.
 *
 * Three ideas carry the design:
 *
 * - **The headcount is live.** Every filter change re-counts server-side, so a
 *   broker never runs a preview to discover a filter matched nobody.
 * - **Unticking a row is an EXCLUSION, not a smaller list.** It writes to
 *   `exclude_employee_ids`, so the rule survives a later filter change and the
 *   apply request stays small (and provably the same rule that was previewed).
 * - **A pasted list is resolved here**, in the picker, with the unmatched
 *   entries shown immediately — not discovered after a full preview run.
 */
import { useMemo, useState } from "react";
import { Loader2, Plus, Users, X } from "lucide-react";
import { toast } from "sonner";
import {
  type MemberFacets,
  type MemberQuery,
  useMemberQueryCount,
  useResolveMemberList,
} from "@/api/memberQuery";
import { formatError } from "@/lib/errors";
import { useDebouncedValue } from "@/lib/use-debounced-value";
import { cn } from "@/lib/cn";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { NativeSelect } from "@/components/ui/native-select";
import { SectionLabel } from "@/components/ui/section-label";
import { InfoHint } from "@/components/ui/tooltip";
import { MatchSetPicker } from "@/components/configuration/flex/MatchSetPicker";

export type SelectorState = {
  q: string;
  includeTerminated: boolean;
  categoryIds: string[];
  currentPlanCodes: string[];
  coverageState: MemberQuery["coverage_state"];
  attributes: Record<string, string[]>;
  /** Members added by pasted list, on top of whatever the filters match. */
  addedIds: string[];
  /** Staff IDs the ORIGINAL selection named, carried through a re-run. The
   *  pre-redesign page selected members this way, so dropping them silently
   *  re-ran a strictly smaller population — or, on a staff-id-only batch,
   *  reported the selection as empty. Read-only here: they are shown and can be
   *  cleared, but new selections use the picker. */
  staffIds: string[];
  /** Members unticked in the results table. */
  excludedIds: string[];
};

export const EMPTY_SELECTOR: SelectorState = {
  q: "",
  includeTerminated: false,
  categoryIds: [],
  currentPlanCodes: [],
  coverageState: "any",
  attributes: {},
  addedIds: [],
  staffIds: [],
  excludedIds: [],
};

/** The wire shape. One builder, used by the headcount, the preview and the
 *  apply — three readings of "who is selected" is how they drift apart. */
export function toQuery(state: SelectorState): MemberQuery {
  const attributes = Object.entries(state.attributes)
    .filter(([, values]) => values.length > 0)
    .map(([key, values]) => ({ key, values }));
  return {
    q: state.q.trim() || null,
    include_terminated: state.includeTerminated,
    category_ids: state.categoryIds,
    current_plan_codes: state.currentPlanCodes,
    coverage_state: state.coverageState,
    attributes,
    employee_ids: state.addedIds,
    staff_ids: state.staffIds,
    exclude_employee_ids: state.excludedIds,
  };
}

/** The wire shape back into the builder — "re-run this selection" on a past
 *  batch. The inverse of `toQuery`, so a stored rule is editable rather than
 *  being replayed blind. */
export function fromQuery(query: MemberQuery | null | undefined): SelectorState {
  if (!query) return EMPTY_SELECTOR;
  return {
    q: query.q ?? "",
    includeTerminated: query.include_terminated ?? false,
    categoryIds: query.category_ids ?? [],
    currentPlanCodes: query.current_plan_codes ?? [],
    coverageState: query.coverage_state ?? "any",
    attributes: Object.fromEntries(
      (query.attributes ?? []).map((a) => [a.key, a.values]),
    ),
    addedIds: query.employee_ids ?? [],
    staffIds: query.staff_ids ?? [],
    // Exclusions are NOT carried back. They were ticked off a preview of a
    // population that has since moved, so re-applying them would silently drop
    // people the broker never looked at.
    excludedIds: [],
  };
}

export function selectorIsEmpty(state: SelectorState): boolean {
  return (
    !state.q.trim() &&
    !state.categoryIds.length &&
    !state.currentPlanCodes.length &&
    state.coverageState === "any" &&
    !Object.values(state.attributes).some((v) => v.length) &&
    !state.addedIds.length &&
    !state.staffIds.length
  );
}

const COVERAGE_STATES: { value: NonNullable<MemberQuery["coverage_state"]>; label: string }[] = [
  { value: "any", label: "Everyone" },
  { value: "default", label: "On their cohort default" },
  { value: "overridden", label: "Deviating from their cohort" },
  { value: "declined", label: "Currently declined" },
];

export function MemberSelector({
  policyYearId,
  facets,
  facetsLoading,
  productCode,
  productId,
  state,
  onChange,
}: {
  policyYearId: string | undefined;
  facets: MemberFacets | undefined;
  facetsLoading: boolean;
  productCode: string | undefined;
  productId: string | undefined;
  state: SelectorState;
  onChange: (next: SelectorState) => void;
}) {
  const [pasteOpen, setPasteOpen] = useState(false);
  const [pasteText, setPasteText] = useState("");
  const resolveList = useResolveMemberList(policyYearId);

  // The count is a live readout, so the SEARCH BOX is debounced before it
  // reaches the query: every keystroke is otherwise a full roster resolution
  // server-side (one load + defaults + overrides for the whole benefit year),
  // so typing a ten-character name costs ten roster scans. The chip filters are
  // discrete choices and stay immediate.
  const debouncedQ = useDebouncedValue(state.q, 300);
  const query = useMemo(
    () => toQuery({ ...state, q: debouncedQ }),
    [state, debouncedQ],
  );
  const count = useMemoisedCount(policyYearId, query, productCode, state);

  const set = (patch: Partial<SelectorState>) => onChange({ ...state, ...patch });

  // Cohorts of the product being changed, then everything else — a broker
  // filtering a GHS move cares about GHS cohorts first.
  const categories = useMemo(() => {
    const rows = facets?.categories ?? [];
    if (!productCode) return rows;
    return [...rows].sort((a, b) => {
      const aOwn = a.product_code === productCode ? 0 : 1;
      const bOwn = b.product_code === productCode ? 0 : 1;
      return aOwn - bOwn || b.count - a.count;
    });
  }, [facets, productCode]);

  const product = facets?.products.find((p) => p.id === productId);

  function applyPaste() {
    if (!pasteText.trim()) return;
    resolveList.mutate(
      { text: pasteText, include_terminated: state.includeTerminated },
      {
        onSuccess: (res) => {
          const ids = res.matched.map((m) => m.id);
          set({
            addedIds: [...new Set([...state.addedIds, ...ids])],
            // A member named explicitly is wanted — clear any earlier exclusion
            // of them, or the paste would appear to do nothing.
            excludedIds: state.excludedIds.filter((id) => !ids.includes(id)),
          });
          setPasteText("");
          setPasteOpen(false);
          const parts = [`${res.matched.length} matched`];
          if (res.duplicates) parts.push(`${res.duplicates} duplicate`);
          if (res.unmatched.length) {
            parts.push(`${res.unmatched.length} not found: ${res.unmatched.slice(0, 5).join(", ")}`);
          }
          if (res.unmatched.length) toast.warning(parts.join(" · "));
          else toast.success(parts.join(" · "));
        },
        onError: (e) => toast.error(formatError(e)),
      },
    );
  }

  return (
    <div className="space-y-4">
      <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
        <div>
          <Label htmlFor="member-search">Search</Label>
          <Input
            id="member-search"
            value={state.q}
            onChange={(e) => set({ q: e.target.value })}
            placeholder="Name or staff ID"
          />
        </div>

        <div>
          <Label htmlFor="coverage-state">Coverage</Label>
          <NativeSelect
            id="coverage-state"
            className="w-full"
            value={state.coverageState ?? "any"}
            onChange={(e) =>
              set({ coverageState: e.target.value as SelectorState["coverageState"] })
            }
            disabled={!productCode}
          >
            {COVERAGE_STATES.map((o) => (
              <option key={o.value} value={o.value}>
                {o.label}
              </option>
            ))}
          </NativeSelect>
        </div>

        <div>
          <div className="flex items-center gap-1">
            <Label htmlFor="current-plan">Currently on</Label>
            <InfoHint>
              Matches the member&apos;s EFFECTIVE plan today, including any
              existing override — the same resolution the benefit statement uses.
            </InfoHint>
          </div>
          <NativeSelect
            id="current-plan"
            className="w-full"
            value={state.currentPlanCodes[0] ?? ""}
            onChange={(e) =>
              set({ currentPlanCodes: e.target.value ? [e.target.value] : [] })
            }
            disabled={!product?.plans.length}
          >
            <option value="">
              {/* Say WHY it is inert. An empty list here means nobody is
                  matched to the product, which is a matching gap worth
                  noticing — not an empty dropdown to shrug at. */}
              {!productCode
                ? "Any plan"
                : !product
                  ? "No members matched to this product"
                  : !product.plans.length
                    ? "No members on any plan yet"
                    : "Any plan"}
            </option>
            {(product?.plans ?? []).map((p) => (
              <option key={p.code} value={p.code}>
                {p.code} — {p.count} {p.count === 1 ? "member" : "members"}
              </option>
            ))}
          </NativeSelect>
        </div>
      </div>

      <div className="grid gap-3 lg:grid-cols-2">
        <MatchSetPicker
          label="Cohort"
          hint="The matched category a member sits in — their baseline tier."
          selected={state.categoryIds}
          options={categories.map((c) => ({
            value: c.id,
            count: c.count,
            claimed: false,
          }))}
          onChange={(next) => set({ categoryIds: next })}
          placeholder="Filter by cohort"
          emptyHint="No cohorts configured for this benefit year yet."
          renderValue={(id) =>
            categories.find((c) => c.id === id)?.label ?? "Unknown cohort"
          }
        />
        {/* Every attribute the server offers — it has already dropped
            identifiers, dates and PII. Truncating here would hide a working
            filter with nothing to say it exists. */}
        {(facets?.attributes ?? []).map((attr) => (
          <MatchSetPicker
            key={attr.key}
            label={attr.label}
            selected={state.attributes[attr.key] ?? []}
            options={attr.values.map((v) => ({
              value: v.value,
              count: v.count,
              claimed: false,
            }))}
            onChange={(next) =>
              set({ attributes: { ...state.attributes, [attr.key]: next } })
            }
            placeholder={`Filter by ${attr.label.toLowerCase()}`}
            emptyHint="No values on the current roster."
          />
        ))}
        {facetsLoading && (
          <p className="flex items-center gap-2 text-xs text-muted-foreground">
            <Loader2 className="size-3.5 animate-spin" /> Loading roster filters…
          </p>
        )}
      </div>

      <div className="flex flex-wrap items-center gap-3 border-t border-border pt-3">
        <span className="flex items-center gap-2 text-sm text-foreground">
          <Users className="size-4 text-muted-foreground" />
          {selectorIsEmpty(state) ? (
            <span className="text-muted-foreground">
              Add a filter, or paste a list, to select members.
            </span>
          ) : (
            <>
              <strong className="tabular-nums">{count.data?.total ?? "—"}</strong>
              {count.isFetching && <Loader2 className="size-3.5 animate-spin" />}
              <span className="text-muted-foreground">
                {count.data?.total === 1 ? "member selected" : "members selected"}
              </span>
            </>
          )}
        </span>

        {state.excludedIds.length > 0 && (
          <button
            type="button"
            className="text-xs font-medium text-primary hover:underline"
            onClick={() => set({ excludedIds: [] })}
          >
            {state.excludedIds.length} excluded — undo
          </button>
        )}
        {state.staffIds.length > 0 && (
          <Badge variant="outline">
            {state.staffIds.length} staff ID
            {state.staffIds.length === 1 ? "" : "s"} from the original selection
            <button
              type="button"
              aria-label="Drop the staff IDs carried from the original selection"
              className="ml-1"
              onClick={() => set({ staffIds: [] })}
            >
              <X className="size-3" />
            </button>
          </Badge>
        )}
        {state.addedIds.length > 0 && (
          <Badge variant="outline">
            {state.addedIds.length} added by list
            <button
              type="button"
              aria-label="Clear the pasted list"
              className="ml-1"
              onClick={() => set({ addedIds: [] })}
            >
              <X className="size-3" />
            </button>
          </Badge>
        )}

        <label className="ml-auto flex items-center gap-2 text-xs text-muted-foreground">
          <input
            type="checkbox"
            className="size-3.5 accent-[var(--color-primary)]"
            checked={state.includeTerminated}
            onChange={(e) => set({ includeTerminated: e.target.checked })}
          />
          Include leavers
        </label>
        <Button variant="outline" size="sm" onClick={() => setPasteOpen((v) => !v)}>
          <Plus className="size-4" /> Add by list
        </Button>
      </div>

      {(count.data?.unresolved?.length ?? 0) > 0 && (
        <p className="text-xs text-warn">
          Not resolved:{" "}
          {count.data?.unresolved.slice(0, 5).map((u) => u.value).join(", ")}
          {(count.data?.unresolved.length ?? 0) > 5 &&
            ` (+${(count.data?.unresolved.length ?? 0) - 5} more)`}
          {" — "}
          {count.data?.unresolved[0]?.reason}
        </p>
      )}

      {pasteOpen && (
        <div className="rounded-lg border border-border bg-muted/40 p-3">
          <SectionLabel>Paste staff IDs or NRICs</SectionLabel>
          <textarea
            value={pasteText}
            onChange={(e) => setPasteText(e.target.value)}
            placeholder={"Paste a column copied from Excel, or a comma-separated list"}
            className={cn(
              "mt-1.5 min-h-[96px] w-full rounded-md border border-input bg-background px-3 py-2",
              "text-sm text-foreground placeholder:text-muted-foreground focus-ring",
            )}
          />
          <div className="mt-2 flex items-center gap-2">
            <Button size="sm" onClick={applyPaste} disabled={resolveList.isPending}>
              {resolveList.isPending && <Loader2 className="size-4 animate-spin" />}
              Add to selection
            </Button>
            <Button size="sm" variant="ghost" onClick={() => setPasteOpen(false)}>
              Cancel
            </Button>
            <span className="text-2xs text-muted-foreground">
              Members that can&apos;t be found are reported here, not after a preview.
            </span>
          </div>
        </div>
      )}
    </div>
  );
}

/** The count query, skipped entirely while the selection is empty (the server
 *  rejects an empty rule, and a 422 per keystroke is noise, not feedback). */
function useMemoisedCount(
  policyYearId: string | undefined,
  query: MemberQuery,
  productCode: string | undefined,
  state: SelectorState,
) {
  const empty = selectorIsEmpty(state);
  return useMemberQueryCount(
    empty ? undefined : policyYearId,
    query,
    productCode,
  );
}
