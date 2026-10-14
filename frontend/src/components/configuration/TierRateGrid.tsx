import { X } from "lucide-react";
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
import type { CategoryHeadcounts, RateTier, TemplateTier } from "@/types";
import { RemoveGuard } from "./setup/savedEntries";

/** Tiers an employee's household puts them in; the listing counts these. */
const EMPLOYEE_TIERS = new Set(["EO", "ES", "EC", "EF"]);

export type TierHeadcounts =
  | { state: "loading" | "error" | "no-listing" }
  | { state: "ready"; byTier: Record<string, number> };

/** One plan assignment's employees per tier, from the uploaded listing. */
export function tierHeadcounts(
  data: CategoryHeadcounts | undefined,
  isError: boolean,
  categoryId: string,
): TierHeadcounts {
  if (isError) return { state: "error" };
  if (!data) return { state: "loading" };
  if (data.employees_total === 0) return { state: "no-listing" };
  const row = data.categories.find((c) => c.category_id === categoryId);
  if (!row) return { state: "ready", byTier: {} };
  // A plan covering no dependants has no tier split: everyone is EO.
  const byTier = Object.keys(row.tier_counts).length
    ? row.tier_counts
    : { EO: row.employees };
  return { state: "ready", byTier };
}

const amount = (n: number) => n.toLocaleString(undefined, { maximumFractionDigits: 2 });

// Tiered medical rate editor (EO/ES/EC/EF). The slip supplies each tier's
// rate; the premium is that rate × the plan's headcount in the tier from the
// uploaded Employee Listing, never the slip's own premium, which stays only as
// a grey figure to compare against. Tiers can be added (from the canonical
// vocabulary) and removed per category, so a plan priced for tiers the
// template doesn't declare is configurable without re-uploading the slip.
export function TierRateGrid({
  tiers,
  tierRates,
  headcounts,
  addable,
  onRate,
  onCommit,
  onAdd,
  onRemove,
  savedTiers,
  flagMissing = false,
}: {
  tiers: TemplateTier[];
  tierRates: Record<string, RateTier>;
  headcounts: TierHeadcounts;
  addable: { code: string; label: string }[];
  onRate: (code: string, value: number) => void;
  onCommit: () => void;
  onAdd: (code: string) => void;
  onRemove: (code: string) => void;
  savedTiers: ReadonlySet<string>;
  /** Outline the tiers that still have no rate. */
  flagMissing?: boolean;
}) {
  const safe = (v: string) => {
    const n = Number(v);
    return Number.isFinite(n) ? n : 0;
  };
  const counted = headcounts.state === "ready" ? headcounts.byTier : null;
  const headcountFor = (code: string) =>
    counted && EMPLOYEE_TIERS.has(code) ? counted[code] ?? 0 : null;
  const premiumFor = (code: string) => {
    const heads = headcountFor(code);
    return heads === null ? null : (tierRates[code]?.rate ?? 0) * heads;
  };
  const annual = tiers.reduce((sum, t) => sum + (premiumFor(t.code) ?? 0), 0);
  const slipAnnual = tiers.reduce((sum, t) => sum + (tierRates[t.code]?.premium ?? 0), 0);
  const uncounted = tiers
    .filter((t) => !EMPLOYEE_TIERS.has(t.code) && (tierRates[t.code]?.rate ?? 0) > 0)
    .map((t) => t.code);
  const heading = "text-2xs uppercase tracking-wider text-muted-foreground";

  return (
    <div className="mt-3 border-t border-border pt-3">
      <div className="grid grid-cols-[1.4fr_1fr_0.6fr_1fr_auto] items-center gap-2">
        <Label className={heading}>Tier</Label>
        <Label className={heading}>Rate</Label>
        <Label className={cn(heading, "text-right")}>Headcount</Label>
        <Label className={cn(heading, "text-right")}>Premium</Label>
        <span />
        {tiers.map((t) => {
          const cell = tierRates[t.code] ?? { rate: 0, premium: 0 };
          const rateMissing = flagMissing && !((cell.rate ?? 0) > 0);
          const heads = headcountFor(t.code);
          const premium = premiumFor(t.code);
          return (
            <div key={t.code} className="contents">
              <div className="flex items-baseline gap-1 text-sm text-foreground">
                {t.code}
                <span className="text-2xs text-muted-foreground">{t.label}</span>
              </div>
              <Input
                type="number"
                value={cell.rate || ""}
                onChange={(e) => onRate(t.code, safe(e.target.value))}
                onBlur={onCommit}
                aria-label={`${t.code} rate`}
                aria-invalid={rateMissing || undefined}
                placeholder={rateMissing ? "Missing" : undefined}
                className={cn("h-8 text-sm", rateMissing && "border-warn")}
              />
              <div className="text-right text-sm tabular-nums text-foreground">
                {heads ?? "—"}
              </div>
              <div className="text-right text-sm tabular-nums text-foreground">
                {premium === null ? "—" : amount(premium)}
                {(cell.premium ?? 0) > 0 && (
                  <span className="block text-2xs text-muted-foreground">
                    Slip {amount(cell.premium ?? 0)}
                  </span>
                )}
              </div>
              <RemoveGuard saved={savedTiers.has(t.code)}><Button
                size="icon-sm"
                variant="ghost"
                aria-label={`Remove tier ${t.code}`}
                title={`Remove tier ${t.code}`}
                className="text-error hover:text-error"
                onClick={() => onRemove(t.code)}
              >
                <X className="size-3.5" />
              </Button></RemoveGuard>
            </div>
          );
        })}
      </div>
      <div className="mt-2 flex items-center justify-between gap-3">
        <div className="text-2xs text-muted-foreground" aria-live="polite">
          Annual premium (rate × listing headcount):{" "}
          {headcounts.state === "ready" ? (
            <span className="font-medium text-foreground">{amount(annual)}</span>
          ) : headcounts.state === "loading" ? (
            "Calculating…"
          ) : headcounts.state === "error" ? (
            "Headcount unavailable"
          ) : (
            <>
              <span className="font-medium text-foreground">—</span> · Upload the employee
              listing to calculate
            </>
          )}
          {slipAnnual > 0 && <> · Slip {amount(slipAnnual)}</>}
          {headcounts.state === "ready" && uncounted.length > 0 && (
            <> · Excludes {uncounted.join(", ")} (no employee headcount)</>
          )}
        </div>
        {addable.length > 0 && (
          <Select value="" onValueChange={onAdd}>
            <SelectTrigger className="h-7 w-48 text-2xs text-muted-foreground">
              <SelectValue placeholder="+ Add tier" />
            </SelectTrigger>
            <SelectContent>
              {addable.map((t) => (
                <SelectItem key={t.code} value={t.code}>
                  {t.code} — {t.label}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        )}
      </div>
    </div>
  );
}
