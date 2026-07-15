import { useEffect, useMemo, useState } from "react";
import { useLeaveRateOptions, type LeaveRates } from "@/api/enrollment";
import { Input } from "@/components/ui/input";
import { FieldLabel } from "@/components/ui/tooltip";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";

/**
 * Buy/sell-leave rate editor: pick a grade/designation attribute, then set a
 * per-day rate for each distinct roster value. Buying spends the rate per day from
 * the member's flex wallet; selling credits it. Emits the rates bag on every change.
 */
export function LeaveRatesEditor({
  policyYearId,
  value,
  onChange,
}: {
  policyYearId: string;
  value: LeaveRates;
  onChange: (next: LeaveRates) => void;
}) {
  const { data: options } = useLeaveRateOptions(policyYearId);
  const [attribute, setAttribute] = useState<string>(value.attribute ?? "");
  // Rates kept as strings while editing so partial input doesn't coerce to 0/NaN.
  const [rates, setRates] = useState<Record<string, string>>(() =>
    Object.fromEntries(
      Object.entries(value.rates ?? {}).map(([k, v]) => [k, v == null ? "" : String(v)]),
    ),
  );

  const distinctValues = useMemo(
    () => (attribute && options ? options.values[attribute] ?? [] : []),
    [attribute, options],
  );

  // Bubble the normalized bag up whenever the attribute or any rate changes.
  useEffect(() => {
    const out: Record<string, number | null> = {};
    for (const [k, v] of Object.entries(rates)) {
      const t = v.trim();
      if (t !== "" && Number.isFinite(Number(t))) out[k] = Number(t);
    }
    onChange({ attribute: attribute || null, rates: out });
    // onChange identity is stable enough; intentionally rate/attribute-driven.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [attribute, rates]);

  return (
    <div className="mt-3 rounded-md border border-border bg-muted/30 p-3">
      <div className="flex flex-wrap items-end gap-3">
        <div className="min-w-48">
          <FieldLabel
            htmlFor="lr-attr"
            hint="Set a per-day rate for each grade / designation. Buying leave spends the rate per day from the flex wallet; selling credits it back."
          >
            Rate by
          </FieldLabel>
          <Select
            value={attribute}
            onValueChange={(v) => {
              if (v === attribute) return; // re-selecting must not wipe typed rates
              setAttribute(v);
              setRates({}); // a different attribute keys different values
            }}
          >
            <SelectTrigger id="lr-attr">
              <SelectValue placeholder="Select grade / designation…" />
            </SelectTrigger>
            <SelectContent>
              {(options?.attributes ?? []).map((a) => (
                <SelectItem key={a} value={a}>
                  {a}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>
      </div>

      {attribute && distinctValues.length === 0 && (
        <p className="mt-2 text-xs text-muted-foreground">
          No roster values found for “{attribute}”.
        </p>
      )}

      {distinctValues.length > 0 && (
        <div className="mt-3 grid gap-2 sm:grid-cols-2">
          {distinctValues.map((dv) => (
            <label
              key={dv.value}
              className="flex items-center justify-between gap-2 rounded-md border border-border bg-card px-2.5 py-1.5"
            >
              <span className="min-w-0 truncate text-sm text-foreground">
                {dv.value}
                <span className="ml-1 text-xs text-muted-foreground">
                  ({dv.count})
                </span>
              </span>
              <Input
                type="number"
                min={0}
                step={1}
                placeholder="0"
                className="h-8 w-28"
                value={rates[dv.value] ?? ""}
                onChange={(e) =>
                  setRates((prev) => ({ ...prev, [dv.value]: e.target.value }))
                }
              />
            </label>
          ))}
        </div>
      )}
    </div>
  );
}
