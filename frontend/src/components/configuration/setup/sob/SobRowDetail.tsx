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
import type {
  BenefitKind,
  BenefitLimit,
  SobColumn,
  SobItemAnswer,
  SobSchedule,
} from "@/types";
import {
  COPAY_FIELD_PRESETS,
  addCopayField,
  addSub,
  axisValue,
  copayFields,
  isSubOverridden,
  removeCopayField,
  removeSub,
  setColumnProperty,
  setItemField,
  setSubCell,
  setSubField,
  subCellValue,
} from "@/lib/sob";
import { SobCell } from "./SobCell";

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

const STANDARD_COPAY_KEYS = new Set(["per_visit", "co_payment", "per_policy_year"]);

interface Props {
  item: SobItemAnswer;
  idx: number;
  columns: SobColumn[];
  axis: string[];
  colSpan: number;
  setSob: (fn: (s: SobSchedule) => SobSchedule) => void;
}

/**
 * The expanded detail for one benefit row: type, footnote, qualifier limits,
 * sub-benefits, and the kind-specific editors.
 *
 * Everything here used to render inline on EVERY row. Measured across the
 * reference book: 0.8% of rows carry a footnote and 7% carry limits, so the
 * always-on note input and "Add limit" button were pure page length. They now
 * live behind the row's expander, with badges on the row so a populated one is
 * still visible at a glance.
 *
 * This is also where the dental-axis and list/scale rows regain their footnote,
 * limits and sub-benefits — the old layout replaced them wholesale for those
 * kinds, making the fields unreachable rather than merely hidden.
 */
export function SobRowDetail({
  item,
  idx,
  columns,
  axis,
  colSpan,
  setSob,
}: Props) {
  const kind = item.kind ?? "amount";
  const isListLike = kind === "list" || kind === "scale";

  return (
    <tr className="bg-muted/30">
      <td colSpan={colSpan} className="px-3 py-3">
        <div className="flex flex-col gap-3">
          <div className="flex flex-wrap items-end gap-3">
            <div className="flex flex-col gap-1">
              <Label
                htmlFor={`kind-${item.uid}`}
                className="text-[10px] uppercase tracking-wider text-muted-foreground"
              >
                Value type
              </Label>
              <Select
                value={kind}
                onValueChange={(v) =>
                  setSob((s) => setItemField(s, idx, { kind: v as BenefitKind }))
                }
              >
                <SelectTrigger id={`kind-${item.uid}`} className="h-8 w-44 text-xs">
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
            <div className="flex min-w-64 flex-1 flex-col gap-1">
              <Label
                htmlFor={`note-${item.uid}`}
                className="text-[10px] uppercase tracking-wider text-muted-foreground"
              >
                Footnote
              </Label>
              <Input
                id={`note-${item.uid}`}
                value={item.note ?? ""}
                placeholder="e.g. Include Implants"
                onChange={(e) =>
                  setSob((s) => setItemField(s, idx, { note: e.target.value || null }))
                }
                className="h-8 text-xs"
              />
            </div>
          </div>

          {axis.length > 0 && (
            <AxisValues
              item={item}
              idx={idx}
              axis={axis}
              columns={columns}
              setSob={setSob}
            />
          )}

          {kind === "copay" && (
            <CopayGrid item={item} idx={idx} columns={columns} setSob={setSob} />
          )}

          <LimitRows
            uid={item.uid}
            limits={item.limits ?? []}
            onChange={(limits) => setSob((s) => setItemField(s, idx, { limits }))}
          />

          {isListLike ? (
            <ListRows item={item} idx={idx} showKey={kind === "scale"} setSob={setSob} />
          ) : (
            <SubItems
              item={item}
              idx={idx}
              columns={columns}
              kind={kind}
              setSob={setSob}
            />
          )}
        </div>
      </td>
    </tr>
  );
}

// Dental-style second axis (Panel / Non-Panel) crossed with the benefit
// columns, so each PLAN can carry its own panel limits. These used to be a
// single row-level value shared by every plan, which made a two-plan dental
// schedule unrepresentable.
function AxisValues({
  item,
  idx,
  axis,
  columns,
  setSob,
}: {
  item: SobItemAnswer;
  idx: number;
  axis: string[];
  columns: SobColumn[];
  setSob: (fn: (s: SobSchedule) => SobSchedule) => void;
}) {
  return (
    <div className="flex flex-col gap-1.5">
      <Label className="text-[10px] uppercase tracking-wider text-muted-foreground">
        Values per {axis.join(" / ")}
      </Label>
      <div className="overflow-x-auto">
        <table className="text-xs">
          <thead>
            <tr>
              <th className="w-28" />
              {columns.map((col) => (
                <th
                  key={col.id}
                  className="px-1 pb-1 text-left text-[10px] uppercase tracking-wider text-muted-foreground"
                >
                  {col.label}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {axis.map((label) => (
              <tr key={label}>
                <td className="pr-2 text-[11px] text-muted-foreground">{label}</td>
                {columns.map((col) => (
                  <td key={col.id} className="px-1 py-0.5">
                    <Input
                      aria-label={`${item.name || "Benefit"} — ${label} — ${col.label}`}
                      value={axisValue(item, col.id, label)}
                      onChange={(e) =>
                        setSob((s) =>
                          setColumnProperty(s, idx, col.id, label, e.target.value),
                        )
                      }
                      className="h-7 w-32 text-xs"
                    />
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

// Outpatient panel: per-visit / co-payment / per-policy-year structured fields
// PER COLUMN. The field set is dynamic — the standard trio plus whatever
// qualifier variants the slip (or the broker) added.
function CopayGrid({
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
      <Label className="text-[10px] uppercase tracking-wider text-muted-foreground">
        Per-visit / co-payment
      </Label>
      <div className="overflow-x-auto">
        <table className="text-xs">
          <thead>
            <tr>
              <th className="w-40" />
              {columns.map((col) => (
                <th
                  key={col.id}
                  className="px-1 pb-1 text-left text-[10px] uppercase tracking-wider text-muted-foreground"
                >
                  {col.label}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {fields.map((f) => (
              <tr key={f.key}>
                <td className="pr-2 text-[11px] text-muted-foreground">
                  <div className="flex items-center gap-1">
                    {f.label}
                    {!STANDARD_COPAY_KEYS.has(f.key) && (
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
                </td>
                {columns.map((col) => (
                  <td key={col.id} className="px-1 py-0.5">
                    <Input
                      aria-label={`${f.label} — ${col.label}`}
                      value={item.column_properties?.[col.id]?.[f.key] ?? ""}
                      onChange={(e) =>
                        setSob((s) =>
                          setColumnProperty(s, idx, col.id, f.key, e.target.value),
                        )
                      }
                      className="h-7 w-32 text-xs"
                    />
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {addable.length > 0 && (
        <Select value="" onValueChange={(key) => setSob((s) => addCopayField(s, idx, key))}>
          <SelectTrigger className="h-7 w-72 self-start text-[11px] text-muted-foreground">
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

// Qualifier rows beneath a benefit ("Maximum no. of days" → "120 days"). Shared
// across columns — these are structural qualifiers, not per-plan values.
function LimitRows({
  uid,
  limits,
  onChange,
}: {
  uid: string;
  limits: BenefitLimit[];
  onChange: (limits: BenefitLimit[]) => void;
}) {
  const set = (i: number, patch: Partial<BenefitLimit>) =>
    onChange(limits.map((l, j) => (j === i ? { ...l, ...patch } : l)));
  return (
    <div className="flex flex-col gap-1">
      {limits.length > 0 && (
        <Label className="text-[10px] uppercase tracking-wider text-muted-foreground">
          Limits / qualifiers
        </Label>
      )}
      {limits.map((lim, i) => (
        <div key={`${uid}-lim-${i}`} className="flex items-center gap-2">
          <Input
            aria-label={`Limit ${i + 1} label`}
            value={lim.label}
            placeholder="Maximum no. of days"
            onChange={(e) => set(i, { label: e.target.value })}
            className="h-7 max-w-64 text-xs"
          />
          <Input
            aria-label={`Limit ${i + 1} value`}
            value={lim.value ?? ""}
            placeholder="120 days"
            onChange={(e) => set(i, { value: e.target.value || null })}
            className="h-7 max-w-40 text-xs"
          />
          <Button
            size="icon-sm"
            variant="ghost"
            onClick={() => onChange(limits.filter((_, j) => j !== i))}
            aria-label={`Remove limit ${i + 1}`}
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

// Sub-benefits: keyed child rows with their own per-column values.
function SubItems({
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
  return (
    <div className="flex flex-col gap-2">
      {item.sub_items.length > 0 && (
        <Label className="text-[10px] uppercase tracking-wider text-muted-foreground">
          Sub-benefits
        </Label>
      )}
      {item.sub_items.map((sub, subIdx) => (
        <div key={sub.uid} className="flex flex-col gap-1.5 border-t border-border pt-2">
          <div className="flex items-center gap-2">
            <Input
              aria-label="Sub-benefit key"
              value={sub.key}
              placeholder="a"
              onChange={(e) =>
                setSob((s) => setSubField(s, idx, subIdx, { key: e.target.value }))
              }
              className="h-7 w-14 text-xs"
            />
            <Input
              aria-label="Sub-benefit name"
              value={sub.name}
              placeholder="Sub-benefit"
              onChange={(e) =>
                setSob((s) => setSubField(s, idx, subIdx, { name: e.target.value }))
              }
              className="h-7 max-w-72 text-xs"
            />
            <Select
              value={sub.kind ?? kind}
              onValueChange={(v) =>
                setSob((s) => setSubField(s, idx, subIdx, { kind: v as BenefitKind }))
              }
            >
              {/* Sub-items carry their OWN kind. The old editor forced the
                  parent's, so a day cap under a currency parent was prefixed
                  "S$" — and the stored kind was never read back. */}
              <SelectTrigger aria-label="Sub-benefit value type" className="h-7 w-32 text-[11px]">
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
            <Button
              size="icon-sm"
              variant="ghost"
              onClick={() => setSob((s) => removeSub(s, idx, subIdx))}
              aria-label={`Remove sub-benefit ${sub.name || subIdx + 1}`}
              className="ml-auto text-error hover:text-error"
            >
              <X className="size-3.5" />
            </Button>
          </div>
          <div className="flex flex-wrap gap-2">
            {columns.map((col, ci) => (
              <div key={col.id} className="flex w-40 flex-col gap-1">
                {columns.length > 1 && (
                  <span className="truncate text-[10px] uppercase tracking-wider text-muted-foreground">
                    {col.label}
                  </span>
                )}
                <SobCell
                  kind={sub.kind ?? kind}
                  value={subCellValue(sub, col.id)}
                  overridden={ci > 0 && isSubOverridden(sub, col.id)}
                  inherited={ci > 0 && !isSubOverridden(sub, col.id)}
                  ariaLabel={`${sub.name || "Sub-benefit"} — ${col.label}`}
                  onChange={(v) => setSob((s) => setSubCell(s, idx, subIdx, ci, v))}
                  onReset={() =>
                    setSob((s) =>
                      setSubCell(s, idx, subIdx, ci, sub.base_value ?? ""),
                    )
                  }
                />
              </div>
            ))}
          </div>
          <Input
            aria-label="Sub-benefit footnote"
            value={sub.note ?? ""}
            placeholder="Footnote (optional)"
            onChange={(e) =>
              setSob((s) =>
                setSubField(s, idx, subIdx, { note: e.target.value || null }),
              )
            }
            className="h-7 max-w-96 text-xs text-muted-foreground"
          />
          <LimitRows
            uid={sub.uid}
            limits={sub.limits ?? []}
            onChange={(limits) => setSob((s) => setSubField(s, idx, subIdx, { limits }))}
          />
        </div>
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
  );
}

// `list` (covered conditions) and `scale` (event → compensation) render as an
// editable row list backed by sub-items — no per-column values.
function ListRows({
  item,
  idx,
  showKey,
  setSob,
}: {
  item: SobItemAnswer;
  idx: number;
  showKey: boolean;
  setSob: (fn: (s: SobSchedule) => SobSchedule) => void;
}) {
  return (
    <div className="flex flex-col gap-1.5">
      <Label className="text-[10px] uppercase tracking-wider text-muted-foreground">
        {showKey ? "Scale rows" : "Covered conditions"}
      </Label>
      {item.sub_items.map((sub, subIdx) => (
        <div key={sub.uid} className="flex items-center gap-2">
          {showKey && (
            <Input
              aria-label="Event"
              value={sub.key}
              placeholder="Event"
              onChange={(e) =>
                setSob((s) => setSubField(s, idx, subIdx, { key: e.target.value }))
              }
              className="h-7 w-20 text-xs"
            />
          )}
          <Input
            aria-label={showKey ? "Description / % of sum" : "Covered condition"}
            value={sub.name}
            placeholder={showKey ? "Description / % of sum" : "Covered condition"}
            onChange={(e) =>
              setSob((s) => setSubField(s, idx, subIdx, { name: e.target.value }))
            }
            className="h-7 max-w-96 text-xs"
          />
          <Button
            size="icon-sm"
            variant="ghost"
            onClick={() => setSob((s) => removeSub(s, idx, subIdx))}
            aria-label={`Remove row ${subIdx + 1}`}
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
        <Plus className="size-3.5" /> {showKey ? "Add scale row" : "Add item"}
      </Button>
    </div>
  );
}
