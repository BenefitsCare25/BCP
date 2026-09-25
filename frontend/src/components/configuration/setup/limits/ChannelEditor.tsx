import { useMemo, useState, type ReactNode } from "react";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import { Input } from "@/components/ui/input";
import type { ClaimLimitScope, SobColumn, SobItemAnswer } from "@/types";
import { copayFields, copayValue } from "@/lib/sob";
import { TRACKED_BASES } from "@/lib/claimLimits";
import { hiddenFromMembers } from "@/lib/sobValues";
import {
  channelFacts,
  formatYearly,
  money,
  parseYearly,
  sameChannelValues,
  type Counting,
  type YearlyUnit,
} from "@/lib/claimChannels";
import { DRAWDOWN_OPTIONS, EditorShell, Field, Notice, Preview, Segmented, type SegmentOption } from "./FormParts";
import { ScopePicker } from "./ScopePicker";

export interface ChannelDecision {
  /** Field key → stored value, applied to every target column. */
  values: Record<string, string>;
  counting: Counting;
  scopeCodes: string[];
  columnIds: string[];
}

interface Props {
  title: ReactNode;
  item: SobItemAnswer;
  column: SobColumn;
  columns: SobColumn[];
  claimScopes: ClaimLimitScope[];
  suggested: string[];
  onSave: (decision: ChannelDecision) => void;
  onCancel: () => void;
}

type UnitChoice = YearlyUnit | "none";

/**
 * One channel on one plan: its slip terms, and how its annual cap reaches
 * employees. A cap either allows DRAWDOWN (approved claims of the linked claim
 * types use it up, and approving past it needs an acknowledgement) or is
 * DISPLAYED only — for usage that never becomes a claim here, like a cashless
 * teleconsult app, where a balance would read "5 of 5 left" all year.
 */
export function ChannelEditor({ title, item, column, columns, claimScopes, suggested, onSave, onCancel }: Props) {
  const id = `channel-${item.uid}-${column.id}`;
  const setting = item.claim_limits?.[column.id] ?? null;
  const fields = copayFields(item).filter((f) => f.key !== "per_policy_year");
  const initialYearly = parseYearly(copayValue(item, column.id, "per_policy_year"));

  const [values, setValues] = useState<Record<string, string>>(() =>
    Object.fromEntries(fields.map((f) => [f.key, copayValue(item, column.id, f.key)])),
  );
  const [unit, setUnit] = useState<UnitChoice | null>(initialYearly ? initialYearly.unit : "none");
  const [amount, setAmount] = useState(initialYearly?.amount != null ? String(initialYearly.amount) : "");
  const wording = initialYearly?.unit === "wording" ? initialYearly.raw : "";
  const [counting, setCounting] = useState<Counting>(() => {
    if (setting?.display_only) return "shown";
    if (setting?.status === "verified" && TRACKED_BASES.has(setting.basis)) return "counted";
    return suggested.length > 0 ? "counted" : "shown";
  });
  // A detected guess's claim type is only as good as the rules that made it.
  const [scopeCodes, setScopeCodes] = useState<string[]>(
    setting && setting.source !== "detected" && setting.claim_scope_codes.length ? setting.claim_scope_codes : suggested,
  );
  const twins = columns.filter((c) => c.id !== column.id && sameChannelValues(item, column.id, c.id));
  const [applyToTwins, setApplyToTwins] = useState(twins.length > 0);

  const parsed = amount.trim() ? Number(amount) : NaN;
  const capped = unit === "money" || unit === "visits";
  const amountValid = !capped || (unit === "visits" ? Number.isInteger(parsed) && parsed >= 1 : parsed > 0);
  const hidden = hiddenFromMembers(item);
  const canCount = claimScopes.length > 0 && !hidden;
  const countedNow = capped && counting === "counted" && canCount;
  const needsScope = countedNow && scopeCodes.length === 0;
  const unitMissing = unit === null;
  const units: SegmentOption<UnitChoice>[] = [
    { value: "none", label: "No annual cap" },
    { value: "money", label: "S$ amount" },
    { value: "visits", label: "Visits" },
    ...(wording ? [{ value: "wording" as const, label: "Policy wording" }] : []),
  ];

  const nextValues = useMemo(() => {
    const yearly = unit === null ? copayValue(item, column.id, "per_policy_year") : formatYearly(unit, amountValid && capped ? parsed : null, wording);
    return { ...values, per_policy_year: yearly };
  }, [values, unit, parsed, amountValid, capped, wording, item, column.id]);

  const preview = useMemo(() => {
    const draft: SobItemAnswer = { ...item, column_properties: { ...(item.column_properties ?? {}), [column.id]: nextValues } };
    const facts = channelFacts(draft, column.id);
    if (facts.notCovered || facts.empty) return "Not covered on this plan";
    const parts = [facts.cover, facts.copay].filter(Boolean).join(" · ");
    if (!capped || !amountValid) return parts || "—";
    const cap = unit === "visits" ? `${parsed} visit${parsed === 1 ? "" : "s"}` : money(parsed);
    const limit = countedNow ? `${cap} left of ${cap} this year` : `Up to ${cap} a year`;
    return parts ? `${parts} · ${limit}` : limit;
  }, [item, column.id, nextValues, capped, amountValid, unit, parsed, countedNow]);

  const save = () =>
    onSave({
      values: nextValues,
      counting: countedNow ? "counted" : "shown",
      scopeCodes,
      columnIds: [column.id, ...(applyToTwins ? twins.map((c) => c.id) : [])],
    });

  const footer = (
    <>
      <Button type="button" size="sm" disabled={unitMissing || !amountValid || (capped && !amount.trim()) || needsScope} onClick={save}>
        Apply
      </Button>
      <Button type="button" size="sm" variant="ghost" onClick={onCancel}>
        Cancel
      </Button>
      {needsScope && <span className="text-xs text-warn">Select a claim type to allow drawdown</span>}
    </>
  );

  return (
    <EditorShell title={title} onClose={onCancel} footer={footer}>
      <Field label="Slip terms" labelId={`${id}-terms`}>
        <datalist id={`${id}-hints`}>
          <option value="As charged" />
          <option value="NA" />
        </datalist>
        <div role="group" aria-labelledby={`${id}-terms`} className="grid gap-3 sm:grid-cols-2">
          {fields.map((f) => (
            <div key={f.key} className="space-y-1">
              <label htmlFor={`${id}-${f.key}`} className="text-2xs text-muted-foreground">
                {f.label}
              </label>
              <Input
                id={`${id}-${f.key}`}
                list={`${id}-hints`}
                value={values[f.key] ?? ""}
                placeholder="NA"
                onChange={(e) => setValues((current) => ({ ...current, [f.key]: e.target.value }))}
              />
            </div>
          ))}
        </div>
      </Field>

      {hidden && capped && <Notice>Hidden in SOB. Show it there to allow drawdown.</Notice>}
      {unitMissing && <Notice>Slip states “{initialYearly?.raw}” a year with no unit. Choose S$ or visits.</Notice>}
      <Field label="Annual cap" labelId={`${id}-yearly`}>
        <Segmented name={`${id}-unit`} labelledBy={`${id}-yearly`} value={unit} options={units} onChange={setUnit} />
      </Field>
      {capped && (
        <>
          <Field label={unit === "visits" ? "Visits per year" : "Limit (S$)"} htmlFor={`${id}-amount`}>
            <Input
              id={`${id}-amount`}
              type="number"
              inputMode={unit === "visits" ? "numeric" : "decimal"}
              min={unit === "visits" ? 1 : 0.01}
              step={unit === "visits" ? 1 : 0.01}
              value={amount}
              aria-invalid={!amountValid && amount.trim() !== ""}
              onChange={(e) => setAmount(e.target.value)}
              className="max-w-48"
            />
          </Field>
          <Field label="Employee portal" labelId={`${id}-portal`}>
            <Segmented
              name={`${id}-counting`}
              labelledBy={`${id}-portal`}
              value={countedNow ? "drawdown" : "display"}
              options={DRAWDOWN_OPTIONS(canCount)}
              onChange={(v) => setCounting(v === "drawdown" ? "counted" : "shown")}
            />
          </Field>
        </>
      )}
      {wording && unit === "wording" && (
        <Field label="Shown as">
          <p className="text-sm text-foreground sm:pt-1">{wording}</p>
        </Field>
      )}

      {claimScopes.length > 0 && (
        <Field label="Claim types" labelId={`${id}-scopes`}>
          <ScopePicker
            idPrefix={id}
            labelledBy={`${id}-scopes`}
            selected={scopeCodes}
            scopes={claimScopes}
            suggested={suggested}
            onToggle={(code, checked) =>
              setScopeCodes((current) =>
                checked ? [...current.filter((c) => c !== code), code] : current.filter((c) => c !== code),
              )
            }
          />
        </Field>
      )}

      <Field label="Portal preview">
        <Preview>{preview}</Preview>
      </Field>

      {twins.length > 0 && (
        <label className="flex items-center gap-2 text-xs text-foreground sm:pl-[10rem]">
          <Checkbox checked={applyToTwins} onCheckedChange={(v) => setApplyToTwins(v === true)} />
          Also apply to {twins.map((c) => c.label).join(", ")}
        </label>
      )}
    </EditorShell>
  );
}
