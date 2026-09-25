import { memo } from "react";
import {
  AlertTriangle,
  ChevronDown,
  ChevronRight,
  ChevronUp,
  EyeOff,
  ListTree,
  StickyNote,
  Tags,
  X,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Switch } from "@/components/ui/switch";
import { cn } from "@/lib/cn";
import type { SobColumn, SobItemAnswer, SobSchedule } from "@/types";
import {
  axisValue,
  benefitNumberLabel,
  cellValue,
  copayFields,
  copayValue,
  isOverridden,
  moveItemRenumbered,
  pasteColumn,
  removeItem,
  setCell,
  setItemField,
  storedBenefitNumber,
} from "@/lib/sob";
import { isAbsentValue } from "@/lib/sobValues";
import { rowVisibility } from "@/lib/sobAttention";
import { SobCell } from "./SobCell";

// Short labels so a copay group's three figures fit one table cell.
const COPAY_SHORT: Record<string, string> = {
  per_visit: "Visit",
  co_payment: "Co-pay",
  per_policy_year: "Year",
};

function copayShort(key: string, label: string): string {
  if (COPAY_SHORT[key]) return COPAY_SHORT[key];
  return label.replace(/^per visit\s*[—–-]\s*/i, "Visit ").replace(/^co-?payment\s*[—–-]\s*/i, "Co-pay ");
}

/** A copay group's values for one plan column, read-only: edit in details. */
function CopaySummary({ item, columnId }: { item: SobItemAnswer; columnId: string }) {
  const parts = copayFields(item)
    .map((field) => ({ field, value: copayValue(item, columnId, field.key).trim() }))
    .filter(({ value }) => value);
  if (parts.length === 0) return <span className="text-muted-foreground">—</span>;
  return (
    <span className="flex flex-wrap gap-x-2 gap-y-0.5">
      {parts.map(({ field, value }) => (
        <span
          key={field.key}
          className={isAbsentValue(value) ? "text-muted-foreground/70" : "text-foreground"}
        >
          <span className="text-muted-foreground">{copayShort(field.key, field.label)}</span>{" "}
          {value}
        </span>
      ))}
    </span>
  );
}

interface Props {
  item: SobItemAnswer;
  idx: number;
  columns: SobColumn[];
  /** Dental Panel/Non-Panel axis; when set the row has no per-column value. */
  axis: string[];
  /** Total row count, so the row knows when it is last (move-down disabled). */
  rowCount: number;
  /**
   * Reordering is disabled while a filter is active: `idx` addresses the
   * UNFILTERED list, so "move up" would swap with a row the broker cannot see.
   */
  reorderable: boolean;
  expanded: boolean;
  /**
   * Takes the row's uid so the parent can pass ONE stable callback. An inline
   * `() => toggle(uid)` would allocate a new identity every parent render and
   * silently defeat the `memo` below.
   */
  onToggle: (uid: string) => void;
  setSob: (fn: (s: SobSchedule) => SobSchedule) => void;
  /** Review problems, joined — a primitive so `memo` survives re-renders. */
  issues?: string;
}

/**
 * One benefit as a TABLE ROW: number, name, then one value cell per benefit
 * column. Everything optional lives behind the expander, summarised by badges,
 * so row height stays ~40px instead of the ~250px the old card cost.
 *
 * Memoised: the editor rebuilds the whole schedule object on every keystroke,
 * which previously re-rendered all 69 rows of a GBT schedule per character.
 */
export const SobRow = memo(function SobRow({
  item,
  idx,
  columns,
  axis,
  rowCount,
  reorderable,
  expanded,
  onToggle,
  setSob,
  issues = "",
}: Props) {
  const kind = item.kind ?? "amount";
  const isListLike = kind === "list" || kind === "scale";
  const usesAxis = axis.length > 0;
  const noteCount = item.note ? 1 : 0;
  const limitCount = item.limits?.length ?? 0;
  const subCount = item.sub_items?.length ?? 0;
  const visibility = rowVisibility(item, columns);

  return (
    <tr
      className={cn(
        "group/row border-b border-border last:border-0 hover:bg-muted/20",
        // A hidden row reads as set aside — but its Visible switch stays at
        // full strength, since that is the control that brings it back.
        visibility.hidden && "[&>td:not([data-visibility])]:opacity-60",
      )}
    >
      <td className="sticky left-0 z-10 bg-card px-2 py-1 align-middle">
        <div className="flex items-center gap-1.5">
          <button
            type="button"
            onClick={() => onToggle(item.uid)}
            aria-expanded={expanded}
            aria-label={`${expanded ? "Collapse" : "Expand"} details for ${item.name || "benefit"}`}
            className="rounded p-0.5 text-muted-foreground hover:bg-muted hover:text-foreground"
          >
            {expanded ? (
              <ChevronDown className="size-3.5" />
            ) : (
              <ChevronRight className="size-3.5" />
            )}
          </button>
          <Input
            aria-label={`Benefit ${idx + 1} number`}
            value={benefitNumberLabel(item)}
            onChange={(e) =>
              setSob((s) =>
                setItemField(s, idx, {
                  number: storedBenefitNumber(e.target.value, kind),
                }),
              )
            }
            title={kind === "copay" ? "Outpatient benefit group" : undefined}
            className={cn(
              "h-7 shrink-0 px-1.5 text-center text-xs tabular-nums",
              kind === "copay" ? "w-20" : "w-12",
            )}
          />
          <Input
            aria-label={`Benefit ${idx + 1} name`}
            value={item.name}
            placeholder="Benefit name"
            onChange={(e) =>
              setSob((s) => setItemField(s, idx, { name: e.target.value }))
            }
            className="h-7 min-w-40 text-sm"
          />
          <div className="flex shrink-0 items-center gap-1 text-muted-foreground">
            {issues && (
              <span title={issues} aria-label={`Needs attention: ${issues}`} className="text-warn">
                <AlertTriangle className="size-3.5" />
              </span>
            )}
            {noteCount > 0 && (
              <span title={item.note ?? ""} aria-label="Has a footnote">
                <StickyNote className="size-3" />
              </span>
            )}
            {limitCount > 0 && (
              <span
                className="flex items-center gap-0.5 text-2xs"
                title={`${limitCount} limit / qualifier`}
              >
                <Tags className="size-3" />
                {limitCount}
              </span>
            )}
            {subCount > 0 && (
              <span
                className="flex items-center gap-0.5 text-2xs"
                title={`${subCount} sub-benefit`}
              >
                <ListTree className="size-3" />
                {subCount}
              </span>
            )}
          </div>
        </div>
      </td>

      {/* Whether employees see this row, stated on EVERY row so the portal
          outcome reads straight down the table. A row with nothing to show
          gets no switch: the portal drops it whatever is chosen here. */}
      <td data-visibility="" className="px-2 py-1 align-middle" title={visibility.reason}>
        {visibility.toggleable ? (
          <label className="flex cursor-pointer items-center gap-2">
            <Switch
              checked={!visibility.hidden}
              onCheckedChange={(shown) =>
                setSob((s) => setItemField(s, idx, { member_hidden: !shown }))
              }
              aria-label={`Employees see ${item.name || `benefit ${idx + 1}`}`}
              aria-describedby={`visibility-${item.uid}`}
            />
            <span
              id={`visibility-${item.uid}`}
              className={cn(
                "text-2xs leading-tight",
                visibility.hidden ? "text-muted-foreground" : "text-foreground",
              )}
            >
              {visibility.label}
            </span>
          </label>
        ) : (
          <span className="flex items-center gap-2 text-2xs leading-tight text-muted-foreground">
            <EyeOff className="size-3.5 shrink-0" aria-hidden />
            <span>{visibility.label}</span>
          </span>
        )}
      </td>

      {usesAxis ? (
        // Axis values are per-column (Panel/Non-Panel × plan), so preview each
        // column's pair in its own cell; the details panel edits the grid.
        columns.map((col) => (
          <td key={col.id} className="px-2 py-1 text-xs text-muted-foreground">
            {axis.map((a) => `${a}: ${axisValue(item, col.id, a) || "—"}`).join("  ·  ")}
          </td>
        ))
      ) : isListLike ? (
        <td
          colSpan={columns.length}
          className="px-3 py-1 text-xs italic text-muted-foreground"
        >
          {subCount} {kind === "scale" ? "scale rows" : "covered conditions"} — edit
          in details
        </td>
      ) : kind === "copay" ? (
        // One cell per plan column, like every other row: the figures are the
        // point of an outpatient group, so they must be readable without
        // opening each row. Editing stays in details (several fields per cell).
        columns.map((col) => (
          <td key={col.id} className="px-2 py-1 text-xs">
            <button
              type="button"
              onClick={() => onToggle(item.uid)}
              className="w-full rounded px-1 py-0.5 text-left hover:bg-muted"
              aria-label={`Edit ${item.name || "benefit"} values for ${col.label}`}
            >
              <CopaySummary item={item} columnId={col.id} />
            </button>
          </td>
        ))
      ) : (
        columns.map((col, ci) => {
          const overridden = ci > 0 && isOverridden(item, col.id);
          return (
            <td key={col.id} className="px-1 py-1">
              <SobCell
                kind={kind}
                value={cellValue(item, col.id)}
                overridden={overridden}
                inherited={ci > 0 && !overridden}
                ariaLabel={`${item.name || `Benefit ${idx + 1}`} — ${col.label}`}
                onChange={(v) => setSob((s) => setCell(s, idx, ci, v))}
                onReset={() => setSob((s) => setCell(s, idx, ci, item.base_value ?? ""))}
                // Only offered when the full list is showing: `idx` addresses
                // the UNFILTERED items, so a fill-down under an active filter
                // would write into rows the broker cannot see.
                onPasteColumn={
                  reorderable
                    ? (values) => setSob((s) => pasteColumn(s, idx, ci, values))
                    : undefined
                }
              />
            </td>
          );
        })
      )}

      <td className="px-1 py-1">
        <div className="flex items-center">
          {reorderable && (
          <div className="flex flex-col opacity-40 transition-opacity focus-within:opacity-100 group-hover/row:opacity-100">
            <button
              type="button"
              disabled={idx === 0}
              onClick={() => setSob((s) => moveItemRenumbered(s, idx, -1))}
              aria-label={`Move ${item.name || `benefit ${idx + 1}`} up`}
              className="rounded px-0.5 text-muted-foreground hover:bg-muted hover:text-foreground disabled:opacity-30"
            >
              <ChevronUp className="size-3" />
            </button>
            <button
              type="button"
              disabled={idx >= rowCount - 1}
              onClick={() => setSob((s) => moveItemRenumbered(s, idx, 1))}
              aria-label={`Move ${item.name || `benefit ${idx + 1}`} down`}
              className="rounded px-0.5 text-muted-foreground hover:bg-muted hover:text-foreground disabled:opacity-30"
            >
              <ChevronDown className="size-3" />
            </button>
          </div>
          )}
          <Button
            size="icon-sm"
            variant="ghost"
            onClick={() => setSob((s) => removeItem(s, idx))}
            aria-label={`Remove benefit ${item.name || idx + 1}`}
            className={cn("text-error hover:text-error")}
          >
            <X className="size-3.5" />
          </Button>
        </div>
      </td>
    </tr>
  );
});
