import { useState } from "react";
import { Plus, Settings2, X } from "lucide-react";
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
import { cn } from "@/lib/cn";
import type {
  BenefitKind,
  BenefitLimit,
  PlanAnswer,
  SobColumn,
  SobItemAnswer,
  SobSchedule,
  SobSubItemAnswer,
} from "@/types";
import {
  COPAY_FIELD_PRESETS,
  NOT_COVERED,
  addColumn,
  addCopayField,
  addItem,
  addSub,
  assignPlan,
  cellValue,
  clearCell,
  copayFields,
  isOverridden,
  removeColumn,
  removeCopayField,
  removeItem,
  removeSub,
  setCell,
  setColumnLabel,
  setColumnProperty,
  setItemField,
  setProperty,
  setSubCell,
  setSubField,
  subCellValue,
} from "@/lib/sob";

interface Props {
  sob: SobSchedule;
  // Selected basis-of-cover plans (for the column → plan mapping + labels).
  plans: PlanAnswer[];
  // Optional second value axis (dental Panel/Non-Panel). When set, value columns
  // are the axis labels and values persist plan-independently in item.properties.
  columnAxis?: string[];
  setSob: (fn: (s: SobSchedule) => SobSchedule) => void;
}

const BOOLEAN_OPTIONS = ["YES", "NO", "NA"];

// Line types a broker can pick when a schedule needs structure the template
// didn't declare — e.g. adding a "TCM" or "A&E" outpatient group (copay) that
// only exists on this client's slip.
const KIND_OPTIONS: { value: BenefitKind; label: string }[] = [
  { value: "amount", label: "Amount" },
  { value: "currency", label: "Currency (S$)" },
  { value: "percent", label: "Percent" },
  { value: "days", label: "Days" },
  { value: "boolean", label: "Yes / No" },
  { value: "text", label: "Text" },
  { value: "copay", label: "Per visit / co-pay group" },
  { value: "list", label: "Condition list" },
  { value: "scale", label: "Scale table" },
  { value: "group", label: "Group" },
];

export function ScheduleOfBenefitsSection({
  sob,
  plans,
  columnAxis = [],
  setSob,
}: Props) {
  const [showColumns, setShowColumns] = useState(false);
  const columns = sob.columns;
  const usesAxis = columnAxis.length > 0;

  if (columns.length === 0) {
    return (
      <p className="text-xs text-muted-foreground">
        No plans for this product yet — add one to edit its Schedule of Benefits.
      </p>
    );
  }

  return (
    <div className="flex flex-col gap-3">
      <div className="flex items-center justify-between gap-2">
        {usesAxis ? (
          <span className="text-[11px] text-muted-foreground">
            Values per {columnAxis.join(" / ")}
          </span>
        ) : (
          <Button
            size="sm"
            variant="outline"
            onClick={() => setShowColumns((s) => !s)}
          >
            <Settings2 className="size-3.5" />
            {columns.length === 1
              ? "Split into benefit columns"
              : `${columns.length} benefit columns`}
          </Button>
        )}
        <Button size="sm" variant="outline" onClick={() => setSob(addItem)}>
          <Plus className="size-3.5" /> Add benefit line
        </Button>
      </div>

      {!usesAxis && showColumns && (
        <ColumnManager
          columns={columns}
          plans={plans}
          onAddColumn={() => setSob(addColumn)}
          onRemoveColumn={(id) => setSob((s) => removeColumn(s, id))}
          onLabel={(id, label) => setSob((s) => setColumnLabel(s, id, label))}
          onAssign={(code, id) => setSob((s) => assignPlan(s, code, id))}
        />
      )}

      {sob.items.map((item, idx) => (
        <BenefitCard
          key={item.uid}
          item={item}
          idx={idx}
          columns={columns}
          axis={columnAxis}
          setSob={setSob}
        />
      ))}
    </div>
  );
}

// Column → plan mapping: relabel columns, add/remove them, and assign each
// basis-of-cover plan to the benefit level it receives.
function ColumnManager({
  columns,
  plans,
  onAddColumn,
  onRemoveColumn,
  onLabel,
  onAssign,
}: {
  columns: SobColumn[];
  plans: PlanAnswer[];
  onAddColumn: () => void;
  onRemoveColumn: (id: string) => void;
  onLabel: (id: string, label: string) => void;
  onAssign: (planCode: string, columnId: string) => void;
}) {
  const columnOf = (code: string) =>
    columns.find((c) => c.plan_codes.includes(code))?.id ?? columns[0]?.id ?? "";
  return (
    <div className="flex flex-col gap-3 rounded-md border border-border bg-muted/40 p-3">
      <div className="flex items-center justify-between">
        <Label className="text-[11px] uppercase tracking-wider text-muted-foreground">
          Benefit columns
        </Label>
        <Button size="sm" variant="ghost" onClick={onAddColumn}>
          <Plus className="size-3.5" /> Add column
        </Button>
      </div>
      <div className="flex flex-col gap-1.5">
        {columns.map((col) => (
          <div key={col.id} className="flex items-center gap-2">
            <Input
              value={col.label}
              onChange={(e) => onLabel(col.id, e.target.value)}
              className="h-7 text-xs"
            />
            <span className="shrink-0 text-[10px] text-muted-foreground">
              {col.plan_codes.length} plan
              {col.plan_codes.length === 1 ? "" : "s"}
            </span>
            <Button
              size="icon-sm"
              variant="ghost"
              disabled={columns.length <= 1}
              onClick={() => onRemoveColumn(col.id)}
              aria-label="Remove column"
              className="text-error hover:text-error"
            >
              <X className="size-3.5" />
            </Button>
          </div>
        ))}
      </div>
      {plans.length > 0 && (
        <div className="flex flex-col gap-1.5 border-t border-border pt-2">
          <Label className="text-[10px] uppercase tracking-wider text-muted-foreground">
            Plan → column
          </Label>
          {plans.map((p) => (
            <div
              key={p.code}
              className="grid grid-cols-[1fr_auto] items-center gap-2"
            >
              <span className="truncate text-xs text-foreground">{p.label}</span>
              <Select
                value={columnOf(p.code)}
                onValueChange={(v) => onAssign(p.code, v)}
              >
                <SelectTrigger className="h-7 w-44 text-xs">
                  <SelectValue />
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

function BenefitCard({
  item,
  idx,
  columns,
  axis,
  setSob,
}: {
  item: SobItemAnswer;
  idx: number;
  columns: SobColumn[];
  axis: string[];
  setSob: (fn: (s: SobSchedule) => SobSchedule) => void;
}) {
  const kind = item.kind ?? "amount";
  const isListLike = kind === "list" || kind === "scale";
  return (
    <div className="rounded-md border border-border p-3">
      <div className="mb-2 grid grid-cols-[64px_1fr_auto_auto] items-end gap-2">
        <div className="flex flex-col gap-1">
          <Label className="text-[10px] uppercase tracking-wider text-muted-foreground">
            No.
          </Label>
          <Input
            value={item.number}
            onChange={(e) => setSob((s) => setItemField(s, idx, { number: e.target.value }))}
            className="h-8 text-sm"
          />
        </div>
        <div className="flex flex-col gap-1">
          <Label className="text-[10px] uppercase tracking-wider text-muted-foreground">
            Benefit
          </Label>
          <Input
            value={item.name}
            onChange={(e) => setSob((s) => setItemField(s, idx, { name: e.target.value }))}
            className="h-8 text-sm"
          />
        </div>
        <div className="flex flex-col gap-1">
          <Label className="text-[10px] uppercase tracking-wider text-muted-foreground">
            Type
          </Label>
          <Select
            value={kind}
            onValueChange={(v) =>
              setSob((s) => setItemField(s, idx, { kind: v as BenefitKind }))
            }
          >
            <SelectTrigger className="h-8 w-36 text-xs">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              {KIND_OPTIONS.map((o) => (
                <SelectItem key={o.value} value={o.value}>
                  {o.label}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>
        <Button
          size="icon"
          variant="ghost"
          onClick={() => setSob((s) => removeItem(s, idx))}
          aria-label="Remove benefit"
          className="text-error hover:text-error"
        >
          <X className="size-4" />
        </Button>
      </div>

      {isListLike ? (
        <ListEditor
          item={item}
          showKey={kind === "scale"}
          addLabel={kind === "scale" ? "Add scale row" : "Add item"}
          setSob={setSob}
          idx={idx}
        />
      ) : axis.length > 0 ? (
        <AxisColumns
          item={item}
          axis={axis}
          onProperty={(key, value) => setSob((s) => setProperty(s, idx, key, value))}
        />
      ) : (
        <>
          {kind === "copay" ? (
            <CopayColumns item={item} idx={idx} columns={columns} setSob={setSob} />
          ) : (
            <ValueGrid item={item} idx={idx} columns={columns} kind={kind} setSob={setSob} />
          )}

          <ItemNote
            value={item.note ?? ""}
            onChange={(v) => setSob((s) => setItemField(s, idx, { note: v || null }))}
          />
          <LimitRows
            limits={item.limits ?? []}
            onChange={(limits) => setSob((s) => setItemField(s, idx, { limits }))}
          />

          <div className="mt-2 ml-3 flex flex-col gap-3 pl-3">
            {item.sub_items.map((sub, subIdx) => (
              <SubItemRow
                key={sub.uid}
                sub={sub}
                idx={idx}
                subIdx={subIdx}
                columns={columns}
                kind={kind}
                setSob={setSob}
              />
            ))}
            <Button
              size="sm"
              variant="ghost"
              onClick={() => setSob((s) => addSub(s, idx))}
              className="self-start text-muted-foreground"
            >
              <Plus className="size-3.5" /> Add sub-benefit
            </Button>
          </div>
        </>
      )}
    </div>
  );
}

// Horizontally-scrollable strip of per-column value cells (rows × columns). A
// single column ("All plans") drops the header; ≥2 columns label each cell. The
// first column edits the row's base value; the rest write a sparse override.
function ValueGrid({
  item,
  idx,
  columns,
  kind,
  setSob,
}: {
  item: SobItemAnswer;
  idx: number;
  columns: SobColumn[];
  kind: string;
  setSob: (fn: (s: SobSchedule) => SobSchedule) => void;
}) {
  const single = columns.length === 1;
  return (
    <div className="overflow-x-auto">
      <div
        className="grid gap-2"
        style={{ gridTemplateColumns: `repeat(${columns.length}, minmax(150px, 1fr))` }}
      >
        {columns.map((col, ci) => {
          const value = cellValue(item, col.id);
          const modified = ci > 0 && isOverridden(item, col.id);
          return (
            <div key={col.id} className="flex flex-col gap-1">
              {!single && (
                <div className="flex items-center justify-between">
                  <span className="truncate text-[10px] uppercase tracking-wider text-muted-foreground">
                    {col.label}
                  </span>
                  {modified && (
                    <button
                      type="button"
                      onClick={() => setSob((s) => clearCell(s, idx, col.id))}
                      className="text-[10px] font-medium text-warn hover:underline"
                    >
                      reset
                    </button>
                  )}
                </div>
              )}
              <TypedCell
                kind={kind}
                value={value}
                modified={modified}
                onChange={(v) => setSob((s) => setCell(s, idx, ci, v))}
              />
            </div>
          );
        })}
      </div>
    </div>
  );
}

// A single typed value input keyed by benefit kind, plus a "Not covered" toggle
// that writes the NOT_COVERED sentinel (a per-column exclusion).
function TypedCell({
  kind,
  value,
  modified,
  onChange,
}: {
  kind: string;
  value: string;
  modified?: boolean;
  onChange: (value: string) => void;
}) {
  const notCovered = value === NOT_COVERED;
  return (
    <div className="flex flex-col gap-0.5">
      {notCovered ? (
        <div className="flex h-8 items-center justify-between rounded-md border border-dashed border-border px-2">
          <span className="text-xs italic text-muted-foreground">Not covered</span>
        </div>
      ) : kind === "boolean" ? (
        <Select value={value} onValueChange={onChange}>
          <SelectTrigger className={cn("h-8 text-sm", modified && "border-warn")}>
            <SelectValue placeholder="—" />
          </SelectTrigger>
          <SelectContent>
            {BOOLEAN_OPTIONS.map((o) => (
              <SelectItem key={o} value={o}>
                {o}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
      ) : (
        <AffixInput
          kind={kind}
          value={value}
          modified={modified}
          onChange={onChange}
        />
      )}
      <button
        type="button"
        onClick={() => onChange(notCovered ? "" : NOT_COVERED)}
        className="self-start text-[10px] text-muted-foreground hover:text-foreground hover:underline"
      >
        {notCovered ? "Mark covered" : "Not covered"}
      </button>
    </div>
  );
}

// Currency / percent / days inputs get an inline affix; everything else is plain.
function AffixInput({
  kind,
  value,
  modified,
  onChange,
}: {
  kind: string;
  value: string;
  modified?: boolean;
  onChange: (value: string) => void;
}) {
  const prefix = kind === "currency" ? "S$" : null;
  const suffix = kind === "days" ? "days" : kind === "percent" ? "%" : null;
  return (
    <div className="relative">
      {prefix && (
        <span className="pointer-events-none absolute left-2 top-1/2 -translate-y-1/2 text-xs text-muted-foreground">
          {prefix}
        </span>
      )}
      <Input
        value={value}
        inputMode={kind === "currency" || kind === "days" || kind === "percent" ? "decimal" : undefined}
        onChange={(e) => onChange(e.target.value)}
        className={cn(
          "h-8 text-sm",
          prefix && "pl-7",
          suffix && "pr-10",
          modified && "border-warn",
        )}
      />
      {suffix && (
        <span className="pointer-events-none absolute right-2 top-1/2 -translate-y-1/2 text-xs text-muted-foreground">
          {suffix}
        </span>
      )}
    </div>
  );
}

// One sub-benefit row: shared key/name structure + per-column value cells.
function SubItemRow({
  sub,
  idx,
  subIdx,
  columns,
  kind,
  setSob,
}: {
  sub: SobSubItemAnswer;
  idx: number;
  subIdx: number;
  columns: SobColumn[];
  kind: string;
  setSob: (fn: (s: SobSchedule) => SobSchedule) => void;
}) {
  const single = columns.length === 1;
  return (
    <div className="flex flex-col gap-1.5">
      <div className="grid grid-cols-[48px_1fr_auto] items-center gap-2">
        <Input
          value={sub.key}
          placeholder="a"
          onChange={(e) => setSob((s) => setSubField(s, idx, subIdx, { key: e.target.value }))}
          className="h-7 text-xs"
        />
        <Input
          value={sub.name}
          placeholder="Sub-benefit"
          onChange={(e) => setSob((s) => setSubField(s, idx, subIdx, { name: e.target.value }))}
          className="h-7 text-xs"
        />
        <Button
          size="icon-sm"
          variant="ghost"
          onClick={() => setSob((s) => removeSub(s, idx, subIdx))}
          aria-label="Remove sub-benefit"
          className="text-error hover:text-error"
        >
          <X className="size-3.5" />
        </Button>
      </div>
      <div className="overflow-x-auto">
        <div
          className="grid gap-2"
          style={{ gridTemplateColumns: `repeat(${columns.length}, minmax(150px, 1fr))` }}
        >
          {columns.map((col, ci) => (
            <div key={col.id} className="flex flex-col gap-1">
              {!single && (
                <span className="truncate text-[10px] uppercase tracking-wider text-muted-foreground">
                  {col.label}
                </span>
              )}
              <AffixInput
                kind={kind}
                value={subCellValue(sub, col.id)}
                onChange={(v) => setSob((s) => setSubCell(s, idx, subIdx, ci, v))}
              />
            </div>
          ))}
        </div>
      </div>
      <ItemNote
        value={sub.note ?? ""}
        onChange={(v) => setSob((s) => setSubField(s, idx, subIdx, { note: v || null }))}
      />
      <LimitRows
        limits={sub.limits ?? []}
        onChange={(limits) => setSob((s) => setSubField(s, idx, subIdx, { limits }))}
      />
    </div>
  );
}

// Footnote field for an item or sub-item — keeps qualifier text ("Include
// Implants", "Surgical schedule applies…") out of the value cell.
function ItemNote({
  value,
  onChange,
}: {
  value: string;
  onChange: (value: string) => void;
}) {
  return (
    <Input
      value={value}
      placeholder="Footnote (optional) — e.g. Include Implants"
      onChange={(e) => onChange(e.target.value)}
      className="mt-1 h-7 text-xs text-muted-foreground"
    />
  );
}

// Qualifier rows beneath a benefit ("Maximum no. of days" → "120 days"). Shared
// across columns — these are structural qualifiers, not per-plan values.
function LimitRows({
  limits,
  onChange,
}: {
  limits: BenefitLimit[];
  onChange: (limits: BenefitLimit[]) => void;
}) {
  const set = (i: number, patch: Partial<BenefitLimit>) =>
    onChange(limits.map((l, j) => (j === i ? { ...l, ...patch } : l)));
  return (
    <div className="flex flex-col gap-1">
      {limits.map((lim, i) => (
        <div key={i} className="grid grid-cols-[1fr_1fr_auto] items-center gap-2">
          <Input
            value={lim.label}
            placeholder="Maximum no. of days"
            onChange={(e) => set(i, { label: e.target.value })}
            className="h-7 text-xs"
          />
          <Input
            value={lim.value ?? ""}
            placeholder="120 days"
            onChange={(e) => set(i, { value: e.target.value || null })}
            className="h-7 text-xs"
          />
          <Button
            size="icon-sm"
            variant="ghost"
            onClick={() => onChange(limits.filter((_, j) => j !== i))}
            aria-label="Remove limit"
            className="text-error hover:text-error"
          >
            <X className="size-3.5" />
          </Button>
        </div>
      ))}
      <Button
        size="sm"
        variant="ghost"
        onClick={() => onChange([...limits, { label: "", value: "" }])}
        className="self-start text-[11px] text-muted-foreground"
      >
        <Plus className="size-3.5" /> Add limit / qualifier
      </Button>
    </div>
  );
}

// Dental-style second axis: one value column per axis label (Panel/Non-Panel),
// stored plan-independently on the item's properties.
function AxisColumns({
  item,
  axis,
  onProperty,
}: {
  item: SobItemAnswer;
  axis: string[];
  onProperty: (key: string, value: string) => void;
}) {
  return (
    <div
      className="grid gap-2"
      style={{ gridTemplateColumns: `repeat(${axis.length}, minmax(0, 1fr))` }}
    >
      {axis.map((label) => (
        <div key={label} className="flex flex-col gap-1">
          <span className="text-[10px] uppercase tracking-wider text-muted-foreground">
            {label}
          </span>
          <Input
            value={item.properties[label] ?? ""}
            onChange={(e) => onProperty(label, e.target.value)}
            className="h-8 text-sm"
          />
        </div>
      ))}
    </div>
  );
}

// Outpatient panel: per-visit / co-payment / per-policy-year structured fields
// PER COLUMN (stored in that column's column_properties entry). The field set
// is dynamic: the standard trio plus whatever qualifier variants the slip (or
// the broker) added — A&E-style Restructured/Private splits, per-disability.
const STANDARD_COPAY_KEYS = new Set(["per_visit", "co_payment", "per_policy_year"]);

function CopayColumns({
  item,
  idx,
  columns,
  setSob,
}: {
  item: SobItemAnswer;
  idx: number;
  columns: SobColumn[];
  setSob: (fn: (s: SobSchedule) => SobSchedule) => void;
}) {
  const fields = copayFields(item);
  const present = new Set(fields.map((f) => f.key));
  const addable = COPAY_FIELD_PRESETS.filter((p) => !present.has(p.key));
  return (
    <div className="flex flex-col gap-1.5">
      <div className="overflow-x-auto">
        <div
          className="grid gap-2"
          style={{ gridTemplateColumns: `repeat(${columns.length}, minmax(170px, 1fr))` }}
        >
          {columns.map((col, colIdx) => {
            const props = item.column_properties?.[col.id] ?? {};
            return (
              <div key={col.id} className="flex flex-col gap-1.5">
                <span className="text-[10px] uppercase tracking-wider text-muted-foreground">
                  {col.label}
                </span>
                {fields.map((f) => (
                  <div key={f.key} className="flex items-center gap-2">
                    <span className="w-24 shrink-0 text-[10px] text-muted-foreground">
                      {f.label}
                    </span>
                    <Input
                      value={props[f.key] ?? ""}
                      onChange={(e) =>
                        setSob((s) => setColumnProperty(s, idx, col.id, f.key, e.target.value))
                      }
                      className="h-7 text-xs"
                    />
                    {colIdx === 0 && !STANDARD_COPAY_KEYS.has(f.key) && (
                      <Button
                        size="icon-sm"
                        variant="ghost"
                        onClick={() => setSob((s) => removeCopayField(s, idx, f.key))}
                        aria-label={`Remove ${f.label}`}
                        className="text-error hover:text-error"
                      >
                        <X className="size-3" />
                      </Button>
                    )}
                  </div>
                ))}
              </div>
            );
          })}
        </div>
      </div>
      {addable.length > 0 && (
        <Select
          value=""
          onValueChange={(key) => setSob((s) => addCopayField(s, idx, key))}
        >
          <SelectTrigger className="h-7 w-64 self-start text-[11px] text-muted-foreground">
            <SelectValue placeholder="+ Add qualifier (per visit / co-payment variant)" />
          </SelectTrigger>
          <SelectContent>
            {addable.map((p) => (
              <SelectItem key={p.key} value={p.key}>
                {p.label}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
      )}
    </div>
  );
}

// `list` (covered conditions) and `scale` (event → compensation) both render as
// an editable row list backed by the item's sub-items — no per-column values.
function ListEditor({
  item,
  showKey,
  addLabel,
  setSob,
  idx,
}: {
  item: SobItemAnswer;
  showKey: boolean;
  addLabel: string;
  setSob: (fn: (s: SobSchedule) => SobSchedule) => void;
  idx: number;
}) {
  return (
    <div className="flex flex-col gap-1.5">
      {item.sub_items.map((sub, subIdx) => (
        <div
          key={sub.uid}
          className={cn(
            "grid items-center gap-2",
            showKey ? "grid-cols-[64px_1fr_auto]" : "grid-cols-[1fr_auto]",
          )}
        >
          {showKey && (
            <Input
              value={sub.key}
              placeholder="Event"
              onChange={(e) => setSob((s) => setSubField(s, idx, subIdx, { key: e.target.value }))}
              className="h-7 text-xs"
            />
          )}
          <Input
            value={sub.name}
            placeholder={showKey ? "Description / % of sum" : "Covered condition"}
            onChange={(e) => setSob((s) => setSubField(s, idx, subIdx, { name: e.target.value }))}
            className="h-7 text-xs"
          />
          <Button
            size="icon-sm"
            variant="ghost"
            onClick={() => setSob((s) => removeSub(s, idx, subIdx))}
            aria-label="Remove row"
            className="text-error hover:text-error"
          >
            <X className="size-3.5" />
          </Button>
        </div>
      ))}
      <Button
        size="sm"
        variant="ghost"
        onClick={() => setSob((s) => addSub(s, idx))}
        className="self-start text-muted-foreground"
      >
        <Plus className="size-3.5" /> {addLabel}
      </Button>
    </div>
  );
}
