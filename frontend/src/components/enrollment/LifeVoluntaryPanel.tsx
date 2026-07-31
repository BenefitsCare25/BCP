import { useState } from "react";
import { Users } from "lucide-react";
import {
  type DependantAgeLimits,
  type FlexPricingProduct,
  type VoluntaryRateBand,
} from "@/api/enrollment";
import type { FlexPricingEditor } from "./FlexPricingCard";
import { Badge } from "@/components/ui/badge";
import { Input } from "@/components/ui/input";
import { InfoHint } from "@/components/ui/tooltip";
import { fmtCurrency } from "@/lib/format";

const DIRECTION_VARIANT: Record<string, "good" | "warn" | "outline"> = {
  upgrade: "good",
  downgrade: "warn",
  same: "outline",
};

/** Per-S$1000 rate for an age from a voluntary band table (first match wins).
 *  Mirrors the backend `voluntary_rate_for_age`. */
function rateForAge(bands: VoluntaryRateBand[], age: number): number | null {
  for (const b of bands) {
    const loOk = b.min == null || age >= b.min;
    const hiOk = b.max == null || age <= b.max;
    if (loOk && hiOk) return b.rate;
  }
  return null;
}

function premiumAt(si: number | null, bands: VoluntaryRateBand[], age: number): number | null {
  if (si == null) return null;
  const rate = rateForAge(bands, age);
  return rate == null ? null : Math.round((si / 1000) * rate * 100) / 100;
}

/** The broker's edited bound as a string, or "" when unset. We deliberately do
 *  NOT fall back to the default here: the input must be able to go blank (the
 *  effective default is shown as a placeholder), otherwise clearing a field would
 *  instantly snap back to the default and a bound could never be removed. */
function editedBound(
  edited: DependantAgeLimits | undefined,
  role: "spouse" | "child",
  bound: "min" | "max",
): string {
  const e = edited?.[role]?.[bound];
  return e != null ? String(e) : "";
}

/** The effective default for a bound, shown as the input placeholder. */
function defaultBound(
  fallback: DependantAgeLimits,
  role: "spouse" | "child",
  bound: "min" | "max",
): string {
  const f = fallback?.[role]?.[bound];
  return f != null ? String(f) : bound;
}

/**
 * Life-product voluntary pricing: the age-banded rate table, a live per-member
 * premium preview (employee + dependant tiers price as SI / 1000 x rate[age]),
 * and the per-role dependant eligibility windows. Shown instead of the flat
 * price matrix when a product's insurance line is "life" and it carries a
 * voluntary rate table parsed from the slip.
 */
export function LifeVoluntaryPanel({
  product,
  editor,
}: {
  product: FlexPricingProduct;
  editor: FlexPricingEditor;
}) {
  const bands = product.voluntary_rates ?? [];
  const [empAge, setEmpAge] = useState(45);
  const [depAge, setDepAge] = useState(40);
  const pid = product.product_id;
  const editedLimits = editor.blockFor(pid).dependant?.age_limits;

  // Voluntary tiers (the alternatives a member can elect); the compulsory
  // baseline is priced flat and excluded from the age-banded preview.
  const voluntaryTiers = product.tiers.filter(
    (t) => !t.is_baseline && t.sum_insured != null,
  );

  return (
    <div className="space-y-3 rounded-md border border-border bg-muted/10 p-2.5">
      {/* ── Age-band rate table ─────────────────────────────────────────── */}
      <div className="rounded-lg border border-border">
        <div className="flex items-center gap-1 border-b border-border bg-muted/30 px-3 py-1.5 text-xs font-medium text-foreground">
          Voluntary rates · per S$1000 sum assured (age last birthday)
          <InfoHint>
            Voluntary cover is priced off the member's age band: premium = sum
            assured ÷ 1000 × rate. Compulsory cover keeps its flat rate.
          </InfoHint>
        </div>
        {bands.length === 0 ? (
          <p className="px-3 py-2 text-xs text-muted-foreground">
            No voluntary rate table on the slip for this product.
          </p>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b border-border text-xs text-muted-foreground">
                  {bands.map((b) => (
                    <th key={b.label} className="px-2 py-1.5 text-left font-medium">
                      {b.label}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                <tr>
                  {bands.map((b) => (
                    <td key={b.label} className="px-2 py-1.5 font-medium text-foreground">
                      {b.rate}
                    </td>
                  ))}
                </tr>
              </tbody>
            </table>
          </div>
        )}
      </div>

      {/* ── Employee live preview ───────────────────────────────────────── */}
      {voluntaryTiers.length > 0 && bands.length > 0 && (
        <div className="rounded-lg border border-border">
          <div className="flex items-center justify-between gap-2 border-b border-border bg-muted/30 px-3 py-1.5">
            <span className="text-xs font-medium text-foreground">
              Premium preview
            </span>
            <label className="flex items-center gap-1.5 text-xs text-muted-foreground">
              Member age
              <Input
                type="number"
                value={empAge}
                onChange={(e) => setEmpAge(Number(e.target.value) || 0)}
                className="h-7 w-16 text-xs"
              />
            </label>
          </div>
          <table className="w-full text-sm">
            <tbody>
              {voluntaryTiers.map((t) => (
                <tr key={t.key} className="border-b border-border last:border-0">
                  <td className="px-3 py-1.5">
                    <span className="flex items-center gap-1.5">
                      <span className="text-foreground">{t.label}</span>
                      <Badge
                        variant={DIRECTION_VARIANT[t.direction] ?? "outline"}
                        className="text-2xs"
                      >
                        {t.direction}
                      </Badge>
                    </span>
                  </td>
                  <td className="px-3 py-1.5 text-right text-muted-foreground">
                    {t.sum_insured != null ? `SI ${fmtCurrency(t.sum_insured)}` : "—"}
                  </td>
                  <td className="px-3 py-1.5 text-right font-medium text-foreground">
                    {(() => {
                      const p = premiumAt(t.sum_insured, bands, empAge);
                      return p == null ? "—" : fmtCurrency(p);
                    })()}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {/* ── Dependant eligibility + preview ─────────────────────────────── */}
      <div className="rounded-lg border border-dashed border-border bg-muted/20 px-3 py-2.5">
        <div className="flex items-center justify-between gap-2">
          <span className="flex items-center gap-1 text-xs font-medium text-foreground">
            <span className="flex items-center gap-1.5">
              <Users className="size-3.5 text-muted-foreground" /> Dependant eligibility
            </span>
            <InfoHint>
              A dependant outside their age window is not covered (no premium, no
              flex drawn). Each covered dependant's voluntary premium uses the rate
              table above at the dependant's own age.
            </InfoHint>
          </span>
          <label className="flex items-center gap-1.5 text-xs text-muted-foreground">
            Dependant age
            <Input
              type="number"
              value={depAge}
              onChange={(e) => setDepAge(Number(e.target.value) || 0)}
              className="h-7 w-16 text-xs"
            />
          </label>
        </div>
        <div className="mt-2 grid grid-cols-2 gap-3">
          {(["spouse", "child"] as const).map((role) => (
            <div key={role} className="rounded-md border border-border bg-card px-2.5 py-2">
              <div className="text-2xs font-medium capitalize text-foreground">{role}</div>
              <div className="mt-1 flex items-center gap-1.5">
                <Input
                  type="number"
                  value={editedBound(editedLimits, role, "min")}
                  onChange={(e) => editor.setDepAgeLimit(pid, role, "min", e.target.value)}
                  className="h-7 w-14 text-xs"
                  placeholder={defaultBound(product.dependant_age_limits, role, "min")}
                />
                <span className="text-muted-foreground">–</span>
                <Input
                  type="number"
                  value={editedBound(editedLimits, role, "max")}
                  onChange={(e) => editor.setDepAgeLimit(pid, role, "max", e.target.value)}
                  className="h-7 w-14 text-xs"
                  placeholder={defaultBound(product.dependant_age_limits, role, "max")}
                />
              </div>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}
