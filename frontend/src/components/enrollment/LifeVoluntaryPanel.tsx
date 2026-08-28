import {
  AlertTriangle,
  CheckCircle2,
  Pencil,
  Plus,
  RotateCcw,
  Trash2,
  Users,
} from "lucide-react";
import type {
  DependantAgeLimits,
  FlexPricingProduct,
  FlexPricingProductBlock,
  FlexPricingTier,
  VoluntaryRateBand,
} from "@/api/enrollment";
import type { FlexPricingEditor } from "./FlexPricingCard";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { InfoHint } from "@/components/ui/tooltip";
import { fmtMoney } from "@/lib/format";
import { priceRowForTier } from "@/lib/flexTiers";

const DIRECTION_VARIANT: Record<string, "good" | "warn" | "outline"> = {
  upgrade: "good",
  downgrade: "warn",
  same: "outline",
};

function rateForAge(bands: VoluntaryRateBand[], age: number): number | null {
  for (const band of bands) {
    const loOk = band.min == null || age >= band.min;
    const hiOk = band.max == null || age <= band.max;
    if (loOk && hiOk && Number.isFinite(band.rate)) return band.rate;
  }
  return null;
}

function validAge(value: number | null): boolean {
  return value == null || (Number.isInteger(value) && value >= 0);
}

/** Client-side mirror of the save-boundary topology checks. Open ends are
 * allowed, but adjacent bands may neither overlap nor leave an internal gap. */
export function voluntaryRateIssues(bands: VoluntaryRateBand[]): string[] {
  if (bands.length === 0) return ["Add at least one age band."];
  const issues: string[] = [];
  const labels = new Set<string>();
  for (const [index, band] of bands.entries()) {
    const label = band.label.trim();
    if (!label) issues.push(`Band ${index + 1} needs a label.`);
    else if (labels.has(label)) issues.push(`“${label}” is duplicated.`);
    labels.add(label);
    if (!validAge(band.min) || !validAge(band.max)) {
      issues.push(`${label || `Band ${index + 1}`} has an invalid age.`);
    } else if (band.min != null && band.max != null && band.min > band.max) {
      issues.push(`${label || `Band ${index + 1}`} starts after it ends.`);
    }
    if (!Number.isFinite(band.rate) || band.rate < 0) {
      issues.push(`${label || `Band ${index + 1}`} needs a non-negative rate.`);
    }
  }

  const validRanges = [...bands]
    .filter(
      (band) =>
        validAge(band.min) &&
        validAge(band.max) &&
        (band.min == null || band.max == null || band.min <= band.max),
    )
    .sort((left, right) => (left.min ?? -1) - (right.min ?? -1));
  for (let index = 1; index < validRanges.length; index += 1) {
    const previous = validRanges[index - 1];
    const current = validRanges[index];
    if (
      previous.max == null ||
      current.min == null ||
      current.min <= previous.max
    ) {
      issues.push(`“${previous.label}” and “${current.label}” overlap.`);
    } else if (current.min > previous.max + 1) {
      issues.push(
        `Ages ${previous.max + 1}–${current.min - 1} are not covered by a band.`,
      );
    }
  }
  return issues;
}

function premiumAt(
  sumAssured: number | null,
  bands: VoluntaryRateBand[],
  age: number,
): number | null {
  if (sumAssured == null) return null;
  const rate = rateForAge(bands, age);
  return rate == null ? null : Math.round((sumAssured / 1000) * rate * 100) / 100;
}

type TierOverride = {
  storedKey: string;
  amountAtAge: number | null;
  cellCount: number;
};

/** Frontend mirror of the resolver's exact-key then unambiguous-plan fallback. */
function tierOverrideAtAge(
  block: FlexPricingProductBlock,
  tier: FlexPricingTier,
  age: number,
): TierOverride | null {
  const match = priceRowForTier(block.price_tags, tier);
  if (!match) return null;
  const { storedKey, row } = match;
  const cellCount = Object.values(row).filter(
    (amount) => typeof amount === "number" && Number.isFinite(amount),
  ).length;
  const band = block.age_bands.find(
    (candidate) =>
      (candidate.min == null || age >= candidate.min) &&
      (candidate.max == null || age <= candidate.max),
  );
  const amount = band ? row[band.label] : null;
  return {
    storedKey,
    amountAtAge:
      typeof amount === "number" && Number.isFinite(amount) ? amount : null,
    cellCount,
  };
}

function editedBound(
  edited: DependantAgeLimits | undefined,
  role: "spouse" | "child",
  bound: "min" | "max",
): string {
  const value = edited?.[role]?.[bound];
  return value != null ? String(value) : "";
}

function defaultBound(
  fallback: DependantAgeLimits,
  role: "spouse" | "child",
  bound: "min" | "max",
): string {
  const value = fallback?.[role]?.[bound];
  return value != null ? String(value) : bound;
}

function sameBand(a: VoluntaryRateBand | undefined, b: VoluntaryRateBand): boolean {
  return !!a &&
    a.label === b.label &&
    a.min === b.min &&
    a.max === b.max &&
    a.rate === b.rate;
}

/** Match a working row back to its recommendation without relying on position.
 * Labels survive range edits and ranges survive label edits, so deleting a row
 * cannot shift every later row onto the wrong recommendation. */
export function recommendedRateBandFor(
  recommended: VoluntaryRateBand[],
  band: VoluntaryRateBand,
): VoluntaryRateBand | undefined {
  const byLabel = recommended.filter((candidate) => candidate.label === band.label);
  if (byLabel.length === 1) return byLabel[0];
  const byRange = recommended.filter(
    (candidate) => candidate.min === band.min && candidate.max === band.max,
  );
  return byRange.length === 1 ? byRange[0] : undefined;
}

export function LifeVoluntaryPanel({
  product,
  editor,
  editable,
}: {
  product: FlexPricingProduct;
  editor: FlexPricingEditor;
  editable: boolean;
}) {
  const recommended = product.voluntary_rates ?? [];
  const bands = editor.voluntaryRatesFor(product);
  const ratesEdited = editor.voluntaryRatesEdited(product);
  const rateIssues = voluntaryRateIssues(bands);
  const pid = product.product_id;
  const block = editor.blockFor(pid);
  const editedLimits = block.dependant?.age_limits;
  const previewAge = 40;
  const voluntaryTiers = product.tiers.filter(
    (tier) => tier.pricing_mode === "age_banded",
  );
  const legacyOverrideKeys = [
    ...new Set(
      voluntaryTiers
        .map((tier) => tierOverrideAtAge(block, tier, previewAge))
        .filter((override) => override && override.cellCount > 0)
        .map((override) => override!.storedKey),
    ),
  ];

  const setBand = (index: number, patch: Partial<VoluntaryRateBand>) =>
    editor.setVoluntaryRates(
      pid,
      bands.map((band, current) =>
        current === index ? { ...band, ...patch } : band,
      ),
    );

  const removeBand = (index: number) =>
    editor.setVoluntaryRates(
      pid,
      bands.filter((_band, current) => current !== index),
    );

  const addBand = () => {
    const previousMax = bands.at(-1)?.max;
    const min = previousMax == null ? null : previousMax + 1;
    editor.setVoluntaryRates(pid, [
      ...bands,
      {
        label: min == null ? `Band ${bands.length + 1}` : `${min}+`,
        min,
        max: null,
        rate: Number.NaN,
      },
    ]);
  };

  return (
    <div className="border-t border-border">
      <div className="flex flex-wrap items-center justify-between gap-2 border-b border-border bg-muted/25 px-4 py-3">
        <div>
          <div className="flex items-center gap-1.5 text-sm font-medium text-foreground">
            Voluntary rates per S$1,000 sum assured
            <InfoHint>
              The system calculates a member&apos;s annual price tag as sum assured
              divided by 1,000, multiplied by the rate for their age band.
            </InfoHint>
          </div>
          <p className="mt-0.5 text-2xs text-muted-foreground">
            Age last birthday · each correction applies to this product&apos;s voluntary tiers.
          </p>
        </div>
        {editable && (
          <div className="flex items-center gap-1.5">
            {ratesEdited && (
              <Button
                variant="ghost"
                size="sm"
                onClick={() => editor.setVoluntaryRates(pid, null)}
              >
                <RotateCcw className="size-3.5" aria-hidden="true" />
                Reset recommendations
              </Button>
            )}
            <Button variant="outline" size="sm" onClick={addBand}>
              <Plus className="size-3.5" aria-hidden="true" /> Age band
            </Button>
          </div>
        )}
      </div>

      {legacyOverrideKeys.length > 0 && (
        <div
          className="flex flex-wrap items-start justify-between gap-3 border-b border-warn/30 bg-warn-soft/30 px-4 py-3 text-xs"
          role="status"
        >
          <div className="flex min-w-0 items-start gap-2">
            <AlertTriangle
              className="mt-0.5 size-4 shrink-0 text-warn"
              aria-hidden="true"
            />
            <div>
              <p className="font-medium text-foreground">
                Saved tier overrides take priority over the rate table.
              </p>
              <p className="mt-0.5 text-muted-foreground">
                The calculated prices below show which value members actually use.
                Reset these overrides to price every age-banded tier from its rate.
              </p>
            </div>
          </div>
          {editable && (
            <Button
              variant="outline"
              size="sm"
              onClick={() =>
                editor.clearTierPriceOverrides(pid, legacyOverrideKeys)
              }
            >
              <RotateCcw className="size-3.5" aria-hidden="true" />
              Use rates for {legacyOverrideKeys.length} tier
              {legacyOverrideKeys.length === 1 ? "" : "s"}
            </Button>
          )}
        </div>
      )}

      {bands.length > 0 && rateIssues.length > 0 && (
        <div className="flex items-start gap-2 border-b border-warn/30 bg-warn-soft/30 px-4 py-3 text-xs text-foreground" role="alert">
          <AlertTriangle className="mt-0.5 size-4 shrink-0 text-warn" aria-hidden="true" />
          <div>
            <p className="font-medium">Review the age bands before saving.</p>
            <ul className="mt-1 list-disc space-y-0.5 pl-4 text-muted-foreground">
              {rateIssues.map((issue) => <li key={issue}>{issue}</li>)}
            </ul>
          </div>
        </div>
      )}

      {bands.length === 0 ? (
        <div className="px-4 py-6 text-sm text-warn">
          No age-band recommendation was detected. Add the required bands before
          opening enrollment.
        </div>
      ) : (
        <>
          <p className="border-b border-border px-4 py-2 text-2xs text-muted-foreground sm:hidden">
            Swipe horizontally to compare the recommendation, rate used, and state.
          </p>
          <div
            className="overflow-x-auto"
            role="region"
            aria-label={`${product.product_code} voluntary age-band rates`}
            tabIndex={0}
          >
            <table className="w-full min-w-[760px] text-sm">
            <thead className="bg-muted/40 text-xs text-muted-foreground">
              <tr className="border-b border-border">
                <th scope="col" className="sticky left-0 z-10 bg-muted px-4 py-2.5 text-left font-medium">Age band</th>
                <th scope="col" className="px-3 py-2.5 text-left font-medium">Minimum age</th>
                <th scope="col" className="px-3 py-2.5 text-left font-medium">Maximum age</th>
                <th scope="col" className="px-3 py-2.5 text-right font-medium">Recommended rate</th>
                <th scope="col" className="px-3 py-2.5 text-left font-medium">Rate used</th>
                <th scope="col" className="px-3 py-2.5 text-left font-medium">State</th>
                {editable && <th scope="col" className="w-10 px-2 py-2.5"><span className="sr-only">Actions</span></th>}
              </tr>
            </thead>
            <tbody>
              {bands.map((band, index) => {
                const original = recommendedRateBandFor(recommended, band);
                const changed = ratesEdited && !sameBand(original, band);
                return (
                  <tr key={index} className="border-b border-border last:border-0 hover:bg-muted/20">
                    <td className="sticky left-0 z-[5] bg-card px-4 py-2">
                      {editable ? (
                        <Input
                          value={band.label}
                          onChange={(event) => setBand(index, { label: event.target.value })}
                          aria-label={`Age band ${index + 1} label`}
                          className="h-8 w-32"
                        />
                      ) : (
                        <span className="font-medium text-foreground">{band.label}</span>
                      )}
                    </td>
                    <td className="px-3 py-2">
                      {editable ? (
                        <Input
                          type="number"
                          min="0"
                          value={band.min ?? ""}
                          onChange={(event) =>
                            setBand(index, {
                              min: event.target.value === "" ? null : Number(event.target.value),
                            })
                          }
                          aria-label={`${band.label} minimum age`}
                          placeholder="No minimum"
                          className="h-8 w-28 tabular-nums"
                        />
                      ) : band.min ?? "No minimum"}
                    </td>
                    <td className="px-3 py-2">
                      {editable ? (
                        <Input
                          type="number"
                          min="0"
                          value={band.max ?? ""}
                          onChange={(event) =>
                            setBand(index, {
                              max: event.target.value === "" ? null : Number(event.target.value),
                            })
                          }
                          aria-label={`${band.label} maximum age`}
                          placeholder="No maximum"
                          className="h-8 w-28 tabular-nums"
                        />
                      ) : band.max ?? "No maximum"}
                    </td>
                    <td className="px-3 py-2 text-right tabular-nums">
                      {original ? original.rate : <span className="text-warn">Not detected</span>}
                    </td>
                    <td className="px-3 py-2">
                      {editable ? (
                        <Input
                          type="number"
                          min="0"
                          step="0.001"
                          value={Number.isFinite(band.rate) ? band.rate : ""}
                          onChange={(event) =>
                            setBand(index, {
                              rate:
                                event.target.value === ""
                                  ? Number.NaN
                                  : Number(event.target.value),
                            })
                          }
                          aria-label={`${band.label} rate per S$1,000`}
                          className="h-8 w-28 tabular-nums"
                        />
                      ) : (
                        <span className="tabular-nums font-medium">
                          {Number.isFinite(band.rate) ? band.rate : "—"}
                        </span>
                      )}
                    </td>
                    <td className="px-3 py-2 text-xs">
                      {changed ? (
                        <span className="flex items-center gap-1 font-medium text-info">
                          <Pencil className="size-3" aria-hidden="true" /> Edited
                        </span>
                      ) : (
                        <span className="flex items-center gap-1 text-muted-foreground">
                          <CheckCircle2 className="size-3" aria-hidden="true" />
                          Recommended
                        </span>
                      )}
                    </td>
                    {editable && (
                      <td className="px-2 py-2">
                        <Button
                          variant="ghost"
                          size="icon-sm"
                          onClick={() => removeBand(index)}
                          aria-label={`Remove ${band.label} age band`}
                        >
                          <Trash2 className="size-3.5" aria-hidden="true" />
                        </Button>
                      </td>
                    )}
                  </tr>
                );
              })}
            </tbody>
            </table>
          </div>
        </>
      )}

      {voluntaryTiers.length > 0 && bands.length > 0 && rateIssues.length === 0 && (
        <div className="border-t border-border">
          <div className="flex items-center justify-between gap-2 px-4 py-2.5">
            <div>
              <h4 className="text-xs font-medium text-foreground">Calculated plan prices</h4>
              <p className="text-2xs text-muted-foreground">
                Example at age {previewAge}; each member uses their own age band.
              </p>
            </div>
          </div>
          <div
            className="overflow-x-auto"
            role="region"
            aria-label={`${product.product_code} calculated plan prices`}
            tabIndex={0}
          >
            <table className="w-full min-w-[920px] text-sm">
              <thead className="bg-muted/40 text-xs text-muted-foreground">
                <tr className="border-y border-border">
                  <th scope="col" className="px-4 py-2 text-left font-medium">Employee category</th>
                  <th scope="col" className="px-3 py-2 text-left font-medium">Plan or option</th>
                  <th scope="col" className="px-3 py-2 text-left font-medium">Relationship</th>
                  <th scope="col" className="px-3 py-2 text-right font-medium">Sum assured</th>
                  <th scope="col" className="px-3 py-2 text-right font-medium">Rate-table price</th>
                  <th scope="col" className="px-3 py-2 text-right font-medium">Price used</th>
                  <th scope="col" className="px-4 py-2 text-left font-medium">State</th>
                </tr>
              </thead>
              <tbody>
                {voluntaryTiers.map((tier) => {
                  const ratePremium = premiumAt(tier.sum_insured, bands, previewAge);
                  const override = tierOverrideAtAge(block, tier, previewAge);
                  const hasStoredOverride = (override?.cellCount ?? 0) > 0;
                  const premium = override?.amountAtAge ?? ratePremium;
                  return (
                    <tr key={tier.key} className="border-b border-border last:border-0 hover:bg-muted/20">
                      <td className="px-4 py-2.5">{tier.cohort_label || "All eligible employees"}</td>
                      <td className="px-3 py-2.5 font-medium">{tier.label}</td>
                      <td className="px-3 py-2.5">
                        <Badge variant={DIRECTION_VARIANT[tier.direction] ?? "outline"}>
                          {tier.direction === "same" ? "alternative" : tier.direction}
                        </Badge>
                      </td>
                      <td className="px-3 py-2.5 text-right tabular-nums">
                        {tier.sum_insured == null ? "—" : fmtMoney(tier.sum_insured)}
                      </td>
                      <td className="px-3 py-2.5 text-right tabular-nums">
                        {ratePremium == null ? "—" : fmtMoney(ratePremium)}
                      </td>
                      <td className="px-3 py-2.5 text-right tabular-nums font-medium">
                        {premium == null ? "—" : fmtMoney(premium)}
                      </td>
                      <td className="px-4 py-2.5">
                        <div className="flex items-center gap-1.5">
                          {override?.amountAtAge != null ? (
                            <span className="flex items-center gap-1 text-xs font-medium text-info">
                              <Pencil className="size-3" aria-hidden="true" />
                              Tier override
                            </span>
                          ) : tier.sum_insured == null ? (
                            <span className="flex items-center gap-1 text-xs font-medium text-warn">
                              <AlertTriangle className="size-3" aria-hidden="true" />
                              Needs sum assured
                            </span>
                          ) : hasStoredOverride ? (
                            <span className="flex items-center gap-1 text-xs font-medium text-warn">
                              <AlertTriangle className="size-3" aria-hidden="true" />
                              Override at other ages
                            </span>
                          ) : (
                            <span className="flex items-center gap-1 text-xs text-muted-foreground">
                              <CheckCircle2 className="size-3" aria-hidden="true" />
                              Rate table
                            </span>
                          )}
                          {editable && hasStoredOverride && override && (
                            <Button
                              variant="ghost"
                              size="icon-sm"
                              onClick={() =>
                                editor.clearTierPriceOverrides(pid, [override.storedKey])
                              }
                              title="Use rate table for this tier"
                              aria-label={`Use rate table for ${tier.label}`}
                            >
                              <RotateCcw className="size-3.5" aria-hidden="true" />
                            </Button>
                          )}
                        </div>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        </div>
      )}

      <div className="border-t border-border bg-muted/15 px-4 py-3">
        <div className="flex items-center gap-1.5 text-xs font-medium text-foreground">
          <Users className="size-3.5 text-muted-foreground" aria-hidden="true" />
          Dependant eligibility
          <InfoHint>
            A dependant outside their configured age window is not covered and
            does not draw flex. Their own age selects the applicable rate band.
          </InfoHint>
        </div>
        <div className="mt-2 grid gap-3 sm:grid-cols-2">
          {(["spouse", "child"] as const).map((role) => (
            <fieldset key={role} className="flex items-center gap-2">
              <legend className="sr-only">{role} age eligibility</legend>
              <span className="w-14 text-xs font-medium capitalize">{role}</span>
              <Input
                type="number"
                value={editedBound(editedLimits, role, "min")}
                onChange={(event) =>
                  editor.setDepAgeLimit(pid, role, "min", event.target.value)
                }
                disabled={!editable}
                className="h-8 w-24 tabular-nums"
                placeholder={defaultBound(product.dependant_age_limits, role, "min")}
                aria-label={`${role} minimum eligible age`}
              />
              <span className="text-muted-foreground">to</span>
              <Input
                type="number"
                value={editedBound(editedLimits, role, "max")}
                onChange={(event) =>
                  editor.setDepAgeLimit(pid, role, "max", event.target.value)
                }
                disabled={!editable}
                className="h-8 w-24 tabular-nums"
                placeholder={defaultBound(product.dependant_age_limits, role, "max")}
                aria-label={`${role} maximum eligible age`}
              />
            </fieldset>
          ))}
        </div>
      </div>
    </div>
  );
}
