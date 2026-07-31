import { useEffect, useMemo, useState } from "react";
import { useLeaveRateOptions, type LeaveRates } from "@/api/enrollment";
import { fmtAmount } from "@/lib/format";
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
 * Per-tier buy/sell-leave terms: pick ONE grade/designation attribute, then set a
 * per-day rate AND the day caps for each distinct roster value. Buying spends the
 * rate per day from the member's flex wallet; selling credits it.
 *
 * The caps are a SPARSE override — a blank cell inherits the policy-level default
 * shown as its placeholder, so a company with one uniform limit configures nothing
 * here and a company that grades leave by seniority overrides only the tiers that
 * differ. Each row also states what its terms are WORTH: a rate per day and a day
 * limit are two halves of one number, and the broker is really setting "how much
 * flex can this grade move".
 */
export function LeaveRatesEditor({
  policyYearId,
  value,
  maxBuyDays,
  maxSellDays,
  onChange,
}: {
  policyYearId: string;
  value: LeaveRates;
  /** Live policy-level caps from the form — the default each tier inherits. */
  maxBuyDays: number;
  maxSellDays: number;
  onChange: (next: LeaveRates) => void;
}) {
  const { data: options } = useLeaveRateOptions(policyYearId);
  const [attribute, setAttribute] = useState<string>(value.attribute ?? "");
  // Rates + caps kept as strings while editing so partial input doesn't coerce to
  // 0/NaN, and so "" stays distinguishable from 0 (inherit vs "no days allowed").
  const [rates, setRates] = useState<Record<string, string>>(() =>
    Object.fromEntries(
      Object.entries(value.rates ?? {}).map(([k, v]) => [k, v == null ? "" : String(v)]),
    ),
  );
  const [buyCaps, setBuyCaps] = useState<Record<string, string>>(() =>
    seedCaps(value, "max_buy_days"),
  );
  const [sellCaps, setSellCaps] = useState<Record<string, string>>(() =>
    seedCaps(value, "max_sell_days"),
  );

  const distinctValues = useMemo(
    () => (attribute && options ? options.values[attribute] ?? [] : []),
    [attribute, options],
  );

  // Bubble the normalized bag up whenever the attribute, a rate or a cap changes.
  // A blank cap is OMITTED (not sent as 0) — that is what makes it inherit.
  useEffect(() => {
    const outRates: Record<string, number | null> = {};
    for (const [k, v] of Object.entries(rates)) {
      const n = numeric(v);
      if (n !== null) outRates[k] = n;
    }
    const outLimits: Record<string, { max_buy_days?: number; max_sell_days?: number }> =
      {};
    for (const key of new Set([...Object.keys(buyCaps), ...Object.keys(sellCaps)])) {
      const buy = numeric(buyCaps[key] ?? "");
      const sell = numeric(sellCaps[key] ?? "");
      if (buy === null && sell === null) continue;
      outLimits[key] = {
        ...(buy !== null ? { max_buy_days: buy } : {}),
        ...(sell !== null ? { max_sell_days: sell } : {}),
      };
    }
    onChange({
      attribute: attribute || null,
      rates: outRates,
      limits: outLimits,
    });
    // onChange identity is stable enough; intentionally value-driven.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [attribute, rates, buyCaps, sellCaps]);

  function resetTiers(next: string) {
    setAttribute(next);
    // A different attribute keys different values — old rates/caps don't apply.
    setRates({});
    setBuyCaps({});
    setSellCaps({});
  }

  return (
    <div className="mt-3 rounded-md border border-border bg-muted/30 p-3">
      <div className="flex flex-wrap items-end gap-3">
        <div className="min-w-48">
          <FieldLabel
            htmlFor="lr-attr"
            hint="Leave terms are set per grade / designation. Each tier gets its own per-day rate and its own buy/sell day limit; leave a limit blank to use the company default above."
          >
            Tier by
          </FieldLabel>
          <Select value={attribute} onValueChange={(v) => v !== attribute && resetTiers(v)}>
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
        <>
          <p className="mt-3 text-2xs text-muted-foreground">
            Blank limits inherit the company default ({maxBuyDays} buy /{" "}
            {maxSellDays} sell).
          </p>
          <div className="mt-2 overflow-x-auto">
            <table className="w-full min-w-[560px] border-separate border-spacing-y-1 text-sm">
              <thead>
                <tr className="text-2xs uppercase tracking-wider text-muted-foreground">
                  <th className="px-2 pb-1 text-left font-medium">Tier · members</th>
                  <th className="w-[110px] px-2 pb-1 text-right font-medium">
                    Rate / day
                  </th>
                  <th className="w-[100px] px-2 pb-1 text-right font-medium">
                    Max buy (d)
                  </th>
                  <th className="w-[100px] px-2 pb-1 text-right font-medium">
                    Max sell (d)
                  </th>
                  <th className="w-[190px] px-2 pb-1 text-right font-medium">
                    At the limit
                  </th>
                </tr>
              </thead>
              <tbody>
                {distinctValues.map((dv) => {
                  const rate = numeric(rates[dv.value] ?? "") ?? 0;
                  const buy = numeric(buyCaps[dv.value] ?? "") ?? maxBuyDays;
                  const sell = numeric(sellCaps[dv.value] ?? "") ?? maxSellDays;
                  return (
                    <tr key={dv.value} className="bg-card">
                      <td className="rounded-l-md border-y border-l border-border px-2.5 py-1.5">
                        <span className="block truncate text-foreground">
                          {dv.value}
                          <span className="ml-1 text-xs text-muted-foreground">
                            ({dv.count})
                          </span>
                        </span>
                      </td>
                      <td className="border-y border-border px-1 py-1.5">
                        <div className="flex items-center justify-end gap-1 text-xs text-muted-foreground">
                          $
                          <Input
                            type="number"
                            min={0}
                            step={1}
                            placeholder="0"
                            aria-label={`Rate per day for ${dv.value}`}
                            className="h-8 w-20"
                            value={rates[dv.value] ?? ""}
                            onChange={(e) =>
                              setRates((p) => ({ ...p, [dv.value]: e.target.value }))
                            }
                          />
                        </div>
                      </td>
                      <td className="border-y border-border px-1 py-1.5">
                        <Input
                          type="number"
                          min={0}
                          step={0.5}
                          placeholder={String(maxBuyDays)}
                          aria-label={`Max buy days for ${dv.value}`}
                          className="ml-auto h-8 w-20"
                          value={buyCaps[dv.value] ?? ""}
                          onChange={(e) =>
                            setBuyCaps((p) => ({ ...p, [dv.value]: e.target.value }))
                          }
                        />
                      </td>
                      <td className="border-y border-border px-1 py-1.5">
                        <Input
                          type="number"
                          min={0}
                          step={0.5}
                          placeholder={String(maxSellDays)}
                          aria-label={`Max sell days for ${dv.value}`}
                          className="ml-auto h-8 w-20"
                          value={sellCaps[dv.value] ?? ""}
                          onChange={(e) =>
                            setSellCaps((p) => ({ ...p, [dv.value]: e.target.value }))
                          }
                        />
                      </td>
                      <td className="rounded-r-md border-y border-r border-border px-2.5 py-1.5 text-right text-2xs">
                        {rate > 0 ? (
                          <>
                            <span className="text-error">
                              -${fmtAmount(rate * buy)}
                            </span>
                            <span className="text-muted-foreground"> / </span>
                            <span className="text-good">
                              +${fmtAmount(rate * sell)}
                            </span>
                          </>
                        ) : (
                          <span className="text-warn">Unpriced — draws $0</span>
                        )}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
          {maxBuyDays <= 0 && maxSellDays <= 0 && (
            <p className="mt-2 text-xs text-warn">
              Both company defaults are 0 — only tiers with their own limit above
              can trade leave.
            </p>
          )}
        </>
      )}
    </div>
  );
}

/** "" / non-numeric → null (inherit), else the number. */
function numeric(raw: string): number | null {
  const t = raw.trim();
  if (t === "" || !Number.isFinite(Number(t))) return null;
  return Number(t);
}

function seedCaps(
  value: LeaveRates,
  field: "max_buy_days" | "max_sell_days",
): Record<string, string> {
  const out: Record<string, string> = {};
  for (const [tier, entry] of Object.entries(value.limits ?? {})) {
    const days = entry?.[field];
    if (days != null) out[tier] = String(days);
  }
  return out;
}
