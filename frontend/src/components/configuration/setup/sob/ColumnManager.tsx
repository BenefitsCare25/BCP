import { Plus, X } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import type { PlanAnswer, SobColumn } from "@/types";

interface Props {
  columns: SobColumn[];
  plans: PlanAnswer[];
  onAddColumn: () => void;
  onRemoveColumn: (id: string) => void;
  onLabel: (id: string, label: string) => void;
  onAssign: (planCode: string, columnId: string) => void;
}

/**
 * Column → plan mapping: relabel columns, add/remove them, and assign each
 * basis-of-cover plan to the benefit level it receives.
 */
export function ColumnManager({
  columns,
  plans,
  onAddColumn,
  onRemoveColumn,
  onLabel,
  onAssign,
}: Props) {
  // Mirror of `sob_columns._column_id_for_plan`: a lone column covers the whole
  // product, so an unlisted plan genuinely belongs to it. With several columns
  // there is no safe guess — defaulting to the first one showed the broker a
  // confident assignment the data never made (and the first column is usually
  // the richest benefit level), hiding the plan that still needs assigning.
  const columnOf = (code: string) =>
    columns.find((c) => c.plan_codes.includes(code))?.id ??
    (columns.length === 1 ? columns[0].id : "");
  return (
    <div className="flex flex-col gap-3 rounded-md border border-border bg-muted/40 p-3">
      <div className="flex items-center justify-between">
        <Label className="text-2xs uppercase tracking-wider text-muted-foreground">
          Benefit columns
        </Label>
        <Button size="sm" variant="ghost" onClick={onAddColumn}>
          <Plus className="size-3.5" /> Add column
        </Button>
      </div>
      <div className="flex flex-col gap-1.5">
        {columns.map((col, i) => (
          <div key={col.id} className="flex items-center gap-2">
            <Input
              aria-label={`Column ${i + 1} label`}
              value={col.label}
              onChange={(e) => onLabel(col.id, e.target.value)}
              className="h-7 max-w-64 text-xs"
            />
            <span
              className={
                col.plan_codes.length === 0
                  ? "shrink-0 text-2xs font-medium text-warn"
                  : "shrink-0 text-2xs text-muted-foreground"
              }
            >
              {col.plan_codes.length === 0
                ? "no plans — unreachable"
                : `${col.plan_codes.length} plan${col.plan_codes.length === 1 ? "" : "s"}`}
            </span>
            <Button
              size="icon-sm"
              variant="ghost"
              disabled={columns.length <= 1}
              onClick={() => onRemoveColumn(col.id)}
              aria-label={`Remove column ${col.label}`}
              className="text-error hover:text-error"
            >
              <X className="size-3.5" />
            </Button>
          </div>
        ))}
      </div>
      {plans.length > 0 && (
        <div className="flex flex-col gap-1.5 border-t border-border pt-2">
          <Label className="text-2xs uppercase tracking-wider text-muted-foreground">
            Plan → column
          </Label>
          {plans.map((p) => (
            <div key={p.code} className="grid grid-cols-[1fr_auto] items-center gap-2">
              <span className="truncate text-xs text-foreground">{p.label}</span>
              <Select value={columnOf(p.code)} onValueChange={(v) => onAssign(p.code, v)}>
                <SelectTrigger
                  aria-label={`Column for ${p.label}`}
                  className={
                    columnOf(p.code)
                      ? "h-7 w-44 text-xs"
                      : "h-7 w-44 border-warn text-xs"
                  }
                >
                  <SelectValue placeholder="Not assigned" />
                </SelectTrigger>
                <SelectContent>
                  {columns.map((c) => (
                    <SelectItem key={c.id} value={c.id}>
                      {c.label}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
