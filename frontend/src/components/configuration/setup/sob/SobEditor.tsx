import { Fragment, useCallback, useMemo, useState } from "react";
import { AlertTriangle, ListOrdered, Plus, Search, Settings2, X } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import type { ClaimLimitScope, PlanAnswer, SobSchedule } from "@/types";
import {
  addColumn,
  addItem,
  assignPlan,
  removeColumn,
  renumberItems,
  setColumnLabel,
  unassignedColumns,
} from "@/lib/sob";
import { ColumnManager } from "./ColumnManager";
import { SobRow } from "./SobRow";
import { SobRowDetail } from "./SobRowDetail";
import { ClaimLimitEditor } from "./ClaimLimitEditor";

interface Props {
  sob: SobSchedule;
  // Selected basis-of-cover plans (for the column → plan mapping + labels).
  plans: PlanAnswer[];
  // Optional second value axis (dental Panel/Non-Panel). When set, each row
  // carries an axis value PER benefit column rather than a single per-row value.
  columnAxis?: string[];
  claimScopes?: ClaimLimitScope[];
  setSob: (fn: (s: SobSchedule) => SobSchedule) => void;
}

// Above this many rows the filter box earns its place in the toolbar.
const FILTER_THRESHOLD = 12;

/**
 * Schedule-of-Benefits editor.
 *
 * The SOB is a matrix (benefits × benefit columns), so it renders as ONE table
 * with a sticky header and a sticky benefit column, inside a SINGLE horizontal
 * scroll container. The previous card-per-benefit layout repeated the column
 * headings on every row, gave each row its own horizontal scrollbar (so rows
 * drifted out of alignment with each other) and used three different cell
 * widths depending on the row's kind.
 */
export function SobEditor({
  sob,
  plans,
  columnAxis = [],
  claimScopes = [],
  setSob,
}: Props) {
  const [showColumns, setShowColumns] = useState(false);
  const [query, setQuery] = useState("");
  const [expanded, setExpanded] = useState<Set<string>>(new Set());
  const columns = sob.columns;
  const usesAxis = columnAxis.length > 0;

  const unassigned = useMemo(() => unassignedColumns(sob), [sob]);

  const visible = useMemo(() => {
    const q = query.trim().toLowerCase();
    // Index is carried alongside because every edit helper addresses rows by
    // their position in the unfiltered list.
    const rows = sob.items.map((item, idx) => ({ item, idx }));
    if (!q) return rows;
    return rows.filter(
      ({ item }) =>
        item.name.toLowerCase().includes(q) ||
        item.number.toLowerCase().includes(q) ||
        (item.sub_items ?? []).some((s) => s.name.toLowerCase().includes(q)),
    );
  }, [sob.items, query]);

  // Stable identity: `SobRow` is memoised and `sob.columns` / unedited `item`
  // objects already survive an edit by reference, so this callback is the only
  // thing standing between a keystroke and re-rendering all 69 rows.
  const toggle = useCallback((uid: string) => {
    setExpanded((prev) => {
      const next = new Set(prev);
      if (next.has(uid)) next.delete(uid);
      else next.add(uid);
      return next;
    });
  }, []);

  if (columns.length === 0) {
    return (
      <p className="text-xs text-muted-foreground">
        No plans for this product yet — add one to edit its Schedule of Benefits.
      </p>
    );
  }

  // Axis rows now render one cell per benefit column too, so the value
  // column count is the same either way.
  const valueColCount = columns.length;

  return (
    <div className="flex flex-col gap-3">
      <div className="flex flex-wrap items-center gap-2">
        <Button size="sm" variant="outline" onClick={() => setShowColumns((s) => !s)}>
          <Settings2 className="size-3.5" />
          {columns.length === 1
            ? "Split into benefit columns"
            : `${columns.length} benefit columns`}
        </Button>
        {usesAxis && (
          <span className="text-2xs text-muted-foreground">
            {columnAxis.join(" / ")} values per plan — edit in each row's details
          </span>
        )}

        {sob.items.length > FILTER_THRESHOLD && (
          <div className="relative">
            <Search className="pointer-events-none absolute left-2 top-1/2 size-3.5 -translate-y-1/2 text-muted-foreground" />
            <Input
              aria-label="Filter benefits"
              value={query}
              placeholder={`Filter ${sob.items.length} benefits…`}
              onChange={(e) => setQuery(e.target.value)}
              className="h-8 w-56 pl-7 text-xs"
            />
            {query && (
              <button
                type="button"
                onClick={() => setQuery("")}
                aria-label="Clear filter"
                className="absolute right-2 top-1/2 -translate-y-1/2 text-muted-foreground hover:text-foreground"
              >
                <X className="size-3.5" />
              </button>
            )}
          </div>
        )}

        <span className="text-2xs text-muted-foreground">
          {query
            ? `${visible.length} of ${sob.items.length} benefits`
            : `${sob.items.length} benefit${sob.items.length === 1 ? "" : "s"}`}
        </span>

        <Button
          size="sm"
          variant="ghost"
          className="ml-auto"
          title="Renumber numeric rows and outpatient groups in their current order; letter labels are preserved"
          onClick={() => setSob(renumberItems)}
        >
          <ListOrdered className="size-3.5" /> Renumber
        </Button>
        <Button size="sm" variant="outline" onClick={() => setSob(addItem)}>
          <Plus className="size-3.5" /> Add benefit line
        </Button>
      </div>

      {unassigned.length > 0 && (
        <div className="flex items-start gap-2 rounded-md border border-warn/40 bg-warn/10 p-2 text-2xs text-foreground">
          <AlertTriangle className="mt-0.5 size-3.5 shrink-0 text-warn" />
          <span>
            {unassigned.map((c) => c.label).join(", ")}{" "}
            {unassigned.length === 1 ? "has" : "have"} no basis-of-cover plan assigned.
            Values entered there are saved but reach no employee — assign a plan in{" "}
            <button
              type="button"
              className="underline"
              onClick={() => setShowColumns(true)}
            >
              benefit columns
            </button>
            .
          </span>
        </div>
      )}

      {showColumns && (
        <ColumnManager
          columns={columns}
          plans={plans}
          onAddColumn={() => setSob(addColumn)}
          onRemoveColumn={(id) => setSob((s) => removeColumn(s, id))}
          onLabel={(id, label) => setSob((s) => setColumnLabel(s, id, label))}
          onAssign={(code, id) => setSob((s) => assignPlan(s, code, id))}
        />
      )}

      {claimScopes.length > 0 && (
        <ClaimLimitEditor
          sob={sob}
          plans={plans}
          claimScopes={claimScopes}
          setSob={setSob}
        />
      )}

      <div className="overflow-x-auto rounded-md border border-border">
        <table className="w-full border-collapse text-sm">
          <thead className="sticky top-0 z-20 bg-muted">
            <tr className="border-b border-border">
              <th className="sticky left-0 z-30 bg-muted px-2 py-1.5 text-left text-2xs uppercase tracking-wider text-muted-foreground">
                Benefit
              </th>
              {columns.map((col) => (
                <th
                  key={col.id}
                  className="min-w-36 px-2 py-1.5 text-left text-2xs uppercase tracking-wider text-muted-foreground"
                >
                  <span className="block truncate" title={col.label}>
                    {col.label}
                  </span>
                  {usesAxis && (
                    <span className="block truncate font-normal normal-case text-subtle">
                      {columnAxis.join(" / ")}
                    </span>
                  )}
                </th>
              ))}
              <th className="w-8" />
            </tr>
          </thead>
          <tbody>
            {visible.length === 0 ? (
              <tr>
                <td
                  colSpan={valueColCount + 2}
                  className="px-3 py-6 text-center text-xs text-muted-foreground"
                >
                  No benefit matches “{query}”.
                </td>
              </tr>
            ) : (
              visible.map(({ item, idx }) => (
                <Fragment key={item.uid}>
                  <SobRow
                    item={item}
                    idx={idx}
                    columns={columns}
                    axis={columnAxis}
                    rowCount={sob.items.length}
                    reorderable={!query.trim()}
                    expanded={expanded.has(item.uid)}
                    onToggle={toggle}
                    setSob={setSob}
                  />
                  {expanded.has(item.uid) && (
                    <SobRowDetail
                      item={item}
                      idx={idx}
                      columns={columns}
                      axis={columnAxis}
                      colSpan={valueColCount + 2}
                      setSob={setSob}
                    />
                  )}
                </Fragment>
              ))
            )}
          </tbody>
        </table>
      </div>
    </div>
  );
}
