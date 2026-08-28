/**
 * Recommended Price Book for flex-funded enrollment.
 *
 * Product -> employee cohort -> tier is the visible hierarchy. Parsed values are
 * recommendations, saved matrix cells are precise broker overrides, and cohorts
 * never fold merely because their current prices happen to match.
 */
import { useMemo, useState, type ReactNode } from "react";
import {
  AlertTriangle,
  ArrowDown,
  CheckCircle2,
  ChevronDown,
  ChevronRight,
  Pencil,
  RotateCcw,
  Search,
} from "lucide-react";
import type {
  FlexPricingBag,
  FlexPricingProduct,
  FlexPricingTier,
  VoluntaryRateBand,
} from "@/api/enrollment";
import { planScalar, priceRowForTier } from "@/lib/flexTiers";
import { fmtMoney } from "@/lib/format";
import { cn } from "@/lib/cn";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import {
  type FlexPricingEditor,
  UnifiedDependantEnrollment,
  UnifiedDependantPricing,
  UnifiedDependantSettings,
} from "@/components/enrollment/FlexPricingCard";
import {
  LifeVoluntaryPanel,
  recommendedRateBandFor,
  voluntaryRateIssues,
} from "@/components/enrollment/LifeVoluntaryPanel";

type ProductStats = { total: number; edited: number; missing: number };
type CohortGroup = { id: string; label: string; tiers: FlexPricingTier[] };

function fixedTiersFor(product: FlexPricingProduct): FlexPricingTier[] {
  return product.tiers.filter((tier) => tier.pricing_mode === "plan_type");
}

function ageBandedTiersFor(product: FlexPricingProduct): FlexPricingTier[] {
  return product.tiers.filter((tier) => tier.pricing_mode === "age_banded");
}

function sameRateBand(
  left: VoluntaryRateBand | undefined,
  right: VoluntaryRateBand | undefined,
): boolean {
  return !!left && !!right &&
    left.label === right.label &&
    left.min === right.min &&
    left.max === right.max &&
    left.rate === right.rate;
}

function cohortsFor(product: FlexPricingProduct): CohortGroup[] {
  const groups = new Map<string, CohortGroup>();
  for (const tier of product.tiers) {
    const current = groups.get(tier.cohort_id);
    if (current) current.tiers.push(tier);
    else {
      groups.set(tier.cohort_id, {
        id: tier.cohort_id,
        label: tier.cohort_label || "All eligible employees",
        tiers: [tier],
      });
    }
  }
  return [...groups.values()];
}

function overrideFor(
  pricing: FlexPricingBag | undefined,
  product: FlexPricingProduct,
  tier: FlexPricingTier,
): number | null {
  const match = priceRowForTier(
    pricing?.products?.[product.product_id]?.price_tags,
    tier,
  );
  return match ? planScalar({ [match.storedKey]: match.row }, match.storedKey) : null;
}

function effectiveFor(
  pricing: FlexPricingBag | undefined,
  product: FlexPricingProduct,
  tier: FlexPricingTier,
): number | null {
  return overrideFor(pricing, product, tier) ?? tier.slip_premium;
}

function statsFor(
  pricing: FlexPricingBag | undefined,
  product: FlexPricingProduct,
): ProductStats {
  const fixedTiers = fixedTiersFor(product);
  const ageTiers = ageBandedTiersFor(product);
  let edited = 0;
  let missing = 0;
  for (const tier of fixedTiers) {
    if (overrideFor(pricing, product, tier) != null) edited += 1;
    if (effectiveFor(pricing, product, tier) == null) missing += 1;
  }

  let total = fixedTiers.length;
  if (ageTiers.length > 0) {
    const block = pricing?.products?.[product.product_id];
    const countScheduleEdits = (
      override: VoluntaryRateBand[] | undefined,
      recommended: VoluntaryRateBand[],
    ) => {
      if (!override) return 0;
      const matchedRecommendations = new Set<VoluntaryRateBand>();
      let unmatchedRates = 0;
      let changedRates = 0;
      for (const rate of override) {
        const original = recommendedRateBandFor(recommended, rate);
        if (original && !matchedRecommendations.has(original)) {
          matchedRecommendations.add(original);
          if (!sameRateBand(original, rate)) changedRates += 1;
        } else {
          unmatchedRates += 1;
        }
      }
      return changedRates + Math.max(
        unmatchedRates,
        recommended.length - matchedRecommendations.size,
      );
    };

    if (block?.voluntary_rates) {
      edited += countScheduleEdits(
        block.voluntary_rates,
        product.voluntary_rates ?? [],
      );
    }
    for (const tier of ageTiers) {
      const recommended = tier.voluntary_rates ?? product.voluntary_rates ?? [];
      const tierOverride = block?.voluntary_rates_by_tier?.[tier.key];
      const rates = tierOverride ?? block?.voluntary_rates ?? recommended;
      total += rates.length;
      missing += voluntaryRateIssues(rates).length;
      edited += countScheduleEdits(tierOverride, recommended);
      if (overrideFor(pricing, product, tier) != null) edited += 1;
    }
  }
  const dependant = pricing?.products?.[product.product_id]?.dependant;
  for (const tier of product.tiers) {
    const participationOverride = dependant?.participation?.[tier.key];
    const participation =
      participationOverride === "none"
        ? null
        : participationOverride ?? tier.dependant_participation;
    if (participationOverride != null) edited += 1;
    if (!participation) continue;
    total += 1;
    const hasMode =
      Object.prototype.hasOwnProperty.call(dependant?.modes ?? {}, tier.key) ||
      dependant?.mode != null ||
      tier.dependant_pricing?.mode !== "none";
    const mode =
      dependant?.modes?.[tier.key] ??
      dependant?.mode ??
      tier.dependant_pricing?.mode ??
      "none";
    if (!hasMode) missing += 1;
    if (
      mode === "per_pax" &&
      dependant?.per_pax?.[tier.key]?.flat == null &&
      tier.dependant_pricing?.per_pax_rate == null
    ) {
      missing += 1;
    }
    if (
      mode === "family_group" &&
      Object.keys(dependant?.family_tags?.[tier.key] ?? {}).length === 0 &&
      (tier.dependant_pricing?.family ?? []).every((row) => row.amount == null)
    ) {
      missing += 1;
    }
    if (
      Object.prototype.hasOwnProperty.call(dependant?.modes ?? {}, tier.key) ||
      Object.keys(dependant?.family_tags?.[tier.key] ?? {}).length > 0 ||
      dependant?.per_pax?.[tier.key]?.flat != null
    ) {
      edited += 1;
    }
  }
  return { total, edited, missing };
}

function matchesQuery(product: FlexPricingProduct, query: string): boolean {
  if (!query) return true;
  const haystack = [
    product.product_code,
    product.line,
    ...product.tiers.flatMap((tier) => [
      tier.label,
      tier.plan_code ?? "",
      tier.cohort_label ?? "",
    ]),
  ]
    .join(" ")
    .toLowerCase();
  return haystack.includes(query.toLowerCase());
}

function ProductStatus({ stats }: { stats: ProductStats }) {
  if (stats.missing > 0) {
    return (
      <Badge variant="warn" className="gap-1">
        <AlertTriangle className="size-3" aria-hidden="true" />
        {stats.missing} issue{stats.missing === 1 ? "" : "s"}
      </Badge>
    );
  }
  if (stats.edited > 0) {
    return (
      <Badge variant="info" className="gap-1">
        <Pencil className="size-3" aria-hidden="true" /> {stats.edited} edited
      </Badge>
    );
  }
  return (
    <Badge variant="good" className="gap-1">
      <CheckCircle2 className="size-3" aria-hidden="true" /> Complete
    </Badge>
  );
}

function relationshipLabel(tier: FlexPricingTier): string {
  if (tier.is_baseline) return "Default";
  if (tier.direction === "upgrade") return "Upgrade";
  if (tier.direction === "downgrade") return "Downgrade";
  if (tier.direction === "same") return "Alternative";
  return tier.participation === "voluntary" ? "Voluntary" : "Alternative";
}

/** @deprecated Replaced by the unified employee-and-dependant table. */
export function CategoryPriceTable({
  product,
  pricing,
  editor,
  editable,
}: {
  product: FlexPricingProduct;
  pricing: FlexPricingBag | undefined;
  editor: FlexPricingEditor;
  editable: boolean;
}) {
  const cohorts = cohortsFor(product);

  return (
    <div
      className="overflow-x-auto border-t border-border"
      role="region"
      aria-label={`${product.product_code} employee-category price tags`}
      tabIndex={0}
    >
      <table className="w-full min-w-[980px] table-fixed text-sm">
        <thead className="bg-muted/40 text-xs text-muted-foreground">
          <tr className="border-b border-border">
            <th scope="col" className="sticky left-0 z-10 w-[24%] bg-muted px-4 py-2 text-left font-medium">
              Employee category
            </th>
            <th scope="col" className="w-[14%] px-3 py-2 text-left font-medium">
              Plan or option
            </th>
            <th scope="col" className="w-[11%] px-3 py-2 text-left font-medium">
              Participation
            </th>
            <th scope="col" className="w-[11%] px-3 py-2 text-left font-medium">
              Relationship
            </th>
            <th scope="col" className="w-[13%] px-3 py-2 text-left font-medium">
              Pricing method
            </th>
            <th scope="col" className="w-[12%] px-3 py-2 text-right font-medium">
              Recommended
            </th>
            <th scope="col" className="w-[15%] px-3 py-2 text-left font-medium">
              Price tag
            </th>
          </tr>
        </thead>
        {cohorts.map((cohort) => (
          <tbody key={cohort.id} className="border-b border-border last:border-b-0">
            {cohort.tiers.map((tier, index) => {
              const ageBanded = tier.pricing_mode === "age_banded";
              const override = ageBanded ? null : overrideFor(pricing, product, tier);
              const effective = ageBanded ? null : override ?? tier.slip_premium;
              const tierRateBands = ageBanded
                ? editor.voluntaryRatesFor(product, tier.key)
                : [];
              return (
                <tr key={tier.key} className="align-top hover:bg-muted/20">
                  {index === 0 && (
                    <th
                      scope="rowgroup"
                      rowSpan={cohort.tiers.length}
                      className="sticky left-0 z-[5] bg-card px-4 py-2 text-left font-medium text-foreground"
                    >
                      <span className="block max-w-md leading-snug">{cohort.label}</span>
                    </th>
                  )}
                  <td className="px-3 py-2">
                    <span className="block font-medium text-foreground">{tier.label}</span>
                    {tier.plan_code && !tier.label.includes(tier.plan_code) && (
                      <span className="text-2xs text-muted-foreground">
                        {tier.plan_code}
                      </span>
                    )}
                  </td>
                  <td className="px-3 py-2">
                    <Badge
                      variant={tier.participation === "compulsory" ? "good" : "info"}
                      className="capitalize"
                    >
                      {tier.participation ?? "Not classified"}
                    </Badge>
                  </td>
                  <td className="px-3 py-2">
                    <span className={cn("text-xs", tier.is_baseline && "font-medium")}>
                      {relationshipLabel(tier)}
                    </span>
                  </td>
                  <td className="px-3 py-2">
                    {ageBanded ? (
                      <span className="text-xs font-medium text-foreground">Age-banded rate</span>
                    ) : (
                      <span className="text-xs text-foreground">Fixed amount</span>
                    )}
                  </td>
                  <td className="px-3 py-2 text-right tabular-nums">
                    {ageBanded ? (
                      <span className="font-medium text-foreground">
                        {tierRateBands.length} band{tierRateBands.length === 1 ? "" : "s"}
                        {tier.sum_insured == null ? " · Missing sum assured" : ` · ${fmtMoney(tier.sum_insured)}`}
                      </span>
                    ) : tier.slip_premium == null ? (
                      <span className="text-warn">Not detected</span>
                    ) : (
                      fmtMoney(tier.slip_premium)
                    )}
                  </td>
                  <td className="px-3 py-2">
                    {ageBanded ? (
                      <a
                        href={`#${product.product_id}-age-rates`}
                        className="inline-flex h-8 items-center gap-1.5 rounded-md px-2 text-xs font-medium text-info outline-none hover:bg-info-soft focus-visible:ring-2 focus-visible:ring-ring/50"
                      >
                        <ArrowDown className="size-3.5" aria-hidden="true" />
                        {editable ? "Edit rate table" : "View rate table"}
                      </a>
                    ) : (
                      <div className="flex items-center gap-1">
                        <Input
                          type="number"
                          min="0"
                          step="0.01"
                          value={effective ?? ""}
                          disabled={!editable}
                          onChange={(event) =>
                            editor.setPlanPrice(
                              product.product_id,
                              [tier.key],
                              event.target.value,
                            )
                          }
                          aria-label={`${product.product_code} ${cohort.label} ${tier.label} price tag`}
                          className="h-8 w-24 tabular-nums"
                          placeholder="Required"
                        />
                        {editable && override != null && (
                          <Button
                            variant="ghost"
                            size="icon-sm"
                            onClick={() =>
                              editor.setPlanPrice(product.product_id, [tier.key], "")
                            }
                            title="Reset to recommendation"
                            aria-label={`Reset ${tier.label} to its recommendation`}
                          >
                            <RotateCcw className="size-3.5" aria-hidden="true" />
                          </Button>
                        )}
                      </div>
                    )}
                  </td>
                </tr>
              );
            })}
          </tbody>
        ))}
      </table>
    </div>
  );
}

function UnifiedCategoryPriceTable({
  product,
  pricing,
  editor,
  editable,
}: {
  product: FlexPricingProduct;
  pricing: FlexPricingBag | undefined;
  editor: FlexPricingEditor;
  editable: boolean;
}) {
  const cohorts = cohortsFor(product);
  return (
    <div
      className="overflow-x-auto border-t border-border"
      role="region"
      aria-label={`${product.product_code} unified price and dependant setup`}
      tabIndex={0}
    >
      <table className="w-full min-w-[1120px] table-fixed text-sm">
        <thead className="bg-muted/40 text-xs text-muted-foreground">
          <tr className="border-b border-border">
            <th scope="col" className="sticky left-0 z-10 w-[20%] bg-muted px-4 py-2 text-left font-medium">
              Employee category
            </th>
            <th scope="col" className="w-[10%] px-3 py-2 text-left font-medium">
              Plan or option
            </th>
            <th scope="col" className="w-[12%] px-3 py-2 text-left font-medium">
              Employee cover
            </th>
            <th scope="col" className="w-[20%] px-3 py-2 text-left font-medium">
              Employee price
            </th>
            <th scope="col" className="w-[14%] px-3 py-2 text-left font-medium">
              Dependant enrolment
            </th>
            <th scope="col" className="w-[24%] px-3 py-2 text-left font-medium">
              Dependant pricing
            </th>
          </tr>
        </thead>
        {cohorts.map((cohort) => (
          <tbody key={cohort.id} className="border-b border-border last:border-b-0">
            {cohort.tiers.map((tier, index) => {
              const ageBanded = tier.pricing_mode === "age_banded";
              const override = ageBanded ? null : overrideFor(pricing, product, tier);
              const effective = ageBanded ? null : override ?? tier.slip_premium;
              const rates = ageBanded
                ? editor.voluntaryRatesFor(product, tier.key)
                : [];
              return (
                <tr key={tier.key} className="align-top hover:bg-muted/20">
                  {index === 0 && (
                    <th
                      scope="rowgroup"
                      rowSpan={cohort.tiers.length}
                      className="sticky left-0 z-[5] bg-card px-4 py-2 text-left font-medium leading-snug text-foreground"
                    >
                      {cohort.label}
                    </th>
                  )}
                  <td className="px-3 py-2">
                    <span className="block font-medium text-foreground">{tier.label}</span>
                    {tier.plan_code && !tier.label.includes(tier.plan_code) && (
                      <span className="text-2xs text-muted-foreground">{tier.plan_code}</span>
                    )}
                  </td>
                  <td className="px-3 py-2">
                    <div className="flex flex-col items-start gap-1">
                      <Badge
                        variant={tier.participation === "compulsory" ? "good" : "info"}
                        className="capitalize"
                      >
                        {tier.participation ?? "Not classified"}
                      </Badge>
                      <span className={cn("text-xs", tier.is_baseline && "font-medium")}>
                        {relationshipLabel(tier)}
                      </span>
                    </div>
                  </td>
                  <td className="px-3 py-2">
                    {ageBanded ? (
                      <div className="space-y-1">
                        <span className="block text-2xs font-medium text-muted-foreground">
                          Age-banded · {rates.length} band{rates.length === 1 ? "" : "s"}
                        </span>
                        <div className="flex items-center gap-2">
                          <span className={cn(
                            "text-xs tabular-nums",
                            tier.sum_insured == null ? "text-warn" : "text-foreground",
                          )}>
                            {tier.sum_insured == null ? "Missing sum assured" : fmtMoney(tier.sum_insured)}
                          </span>
                          <a
                            href={`#${product.product_id}-age-rates`}
                            className="inline-flex h-8 items-center gap-1 rounded-md px-2 text-xs font-medium text-info outline-none hover:bg-info-soft focus-visible:ring-2 focus-visible:ring-ring/50"
                          >
                            <ArrowDown className="size-3.5" aria-hidden="true" />
                            Rates
                          </a>
                        </div>
                      </div>
                    ) : (
                      <div className="space-y-1">
                        <span className="block text-2xs font-medium text-muted-foreground">Fixed</span>
                        <div className="flex items-center gap-1">
                          <span className={cn(
                            "w-[4.75rem] text-right text-xs tabular-nums",
                            tier.slip_premium == null ? "text-warn" : "text-muted-foreground",
                          )}>
                            {tier.slip_premium == null ? "Missing" : fmtMoney(tier.slip_premium)}
                          </span>
                          <span className="text-xs text-muted-foreground" aria-hidden="true">→</span>
                          <Input
                            type="number"
                            min="0"
                            step="0.01"
                            value={effective ?? ""}
                            disabled={!editable}
                            onChange={(event) =>
                              editor.setPlanPrice(
                                product.product_id,
                                [tier.key],
                                event.target.value,
                              )
                            }
                            aria-label={`${product.product_code} ${cohort.label} ${tier.label} price tag`}
                            className="h-8 w-24 tabular-nums"
                            placeholder="Required"
                          />
                          {editable && override != null && (
                            <Button
                              variant="ghost"
                              size="icon-sm"
                              onClick={() =>
                                editor.setPlanPrice(product.product_id, [tier.key], "")
                              }
                              title="Reset to recommendation"
                              aria-label={`Reset ${tier.label} to its recommendation`}
                            >
                              <RotateCcw className="size-3.5" aria-hidden="true" />
                            </Button>
                          )}
                        </div>
                      </div>
                    )}
                  </td>
                  <td className="px-3 py-2">
                    <UnifiedDependantEnrollment
                      product={product}
                      tier={tier}
                      editor={editor}
                      editable={editable}
                    />
                  </td>
                  <td className="px-3 py-2">
                    <UnifiedDependantPricing
                      product={product}
                      tier={tier}
                      editor={editor}
                      editable={editable}
                    />
                  </td>
                </tr>
              );
            })}
          </tbody>
        ))}
      </table>
    </div>
  );
}

export function FlexProductList({
  products,
  pricing,
  editor,
  editable,
  openEditor,
  onToggleEditor,
  emptyHint,
}: {
  products: FlexPricingProduct[];
  pricing: FlexPricingBag | undefined;
  editor: FlexPricingEditor;
  editable: boolean;
  openEditor: Record<string, boolean>;
  onToggleEditor: (productId: string) => void;
  emptyHint?: ReactNode;
}) {
  const [query, setQuery] = useState("");
  const visible = useMemo(
    () => products.filter((product) => matchesQuery(product, query)),
    [products, query],
  );
  const allStats = products.map((product) => statsFor(pricing, product));
  const complete = allStats.filter((stats) => stats.missing === 0).length;
  const needsAttention = allStats.filter((stats) => stats.missing > 0).length;
  const edited = allStats.filter((stats) => stats.edited > 0).length;

  if (!products.length) return <>{emptyHint ?? null}</>;

  return (
    <div className="space-y-3">
      <div className="flex flex-col gap-3 rounded-lg bg-muted/35 p-3 md:flex-row md:items-center md:justify-between">
        <div className="flex flex-wrap items-center gap-x-3 gap-y-1 text-xs">
            <span className="font-medium text-foreground">
              {products.length} product{products.length === 1 ? "" : "s"}
            </span>
            <span className="text-good">{complete} complete</span>
            <span className={cn(needsAttention ? "text-warn" : "text-muted-foreground")}>
              {needsAttention} need attention
            </span>
            <span className="text-info">{edited} edited</span>
        </div>
        <label className="relative block md:w-72">
          <span className="sr-only">Search products, categories, or plans</span>
          <Search
            className="pointer-events-none absolute left-2.5 top-1/2 size-3.5 -translate-y-1/2 text-muted-foreground"
            aria-hidden="true"
          />
          <Input
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            placeholder="Search product, category, or plan"
            className="pl-8"
          />
        </label>
      </div>

      {visible.length === 0 ? (
        <div className="rounded-lg border border-dashed border-border px-4 py-8 text-center text-sm text-muted-foreground">
          No pricing rows match “{query}”.
        </div>
      ) : (
        <div className="space-y-2">
          {visible.map((product) => {
            const stats = statsFor(pricing, product);
            const isOpen = !!openEditor[product.product_id];
            const ageTiers = ageBandedTiersFor(product);
            return (
              <section
                key={product.product_id}
                className="overflow-hidden rounded-lg border border-border bg-card"
              >
                <button
                  type="button"
                  onClick={() => onToggleEditor(product.product_id)}
                  aria-expanded={isOpen}
                  aria-label={`${isOpen ? "Collapse" : "Expand"} ${product.product_code}`}
                  className="flex w-full items-center gap-3 px-4 py-2.5 text-left outline-none transition-colors hover:bg-muted/30 focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-ring/40"
                >
                  {isOpen ? (
                    <ChevronDown className="mt-0.5 size-4 shrink-0" aria-hidden="true" />
                  ) : (
                    <ChevronRight className="mt-0.5 size-4 shrink-0" aria-hidden="true" />
                  )}
                  <div className="min-w-0 flex-1">
                    <div className="flex flex-wrap items-center gap-2">
                      <h3 className="text-sm font-semibold text-foreground">
                        {product.product_code}
                      </h3>
                      <Badge variant="outline" className="capitalize">
                        {product.line}
                      </Badge>
                      <ProductStatus stats={stats} />
                    </div>
                  </div>
                </button>

                {isOpen && (
                  <div>
                    <UnifiedCategoryPriceTable
                      product={product}
                      pricing={pricing}
                      editor={editor}
                      editable={editable}
                    />
                    {ageTiers.length > 0 && (
                      <div id={`${product.product_id}-age-rates`}>
                        <LifeVoluntaryPanel
                          product={product}
                          editor={editor}
                          editable={editable}
                        />
                      </div>
                    )}
                    <UnifiedDependantSettings
                      product={product}
                      editor={editor}
                      editable={editable}
                    />
                  </div>
                )}
              </section>
            );
          })}
        </div>
      )}
    </div>
  );
}
