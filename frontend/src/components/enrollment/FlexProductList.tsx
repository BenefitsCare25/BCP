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
  CheckCircle2,
  ChevronDown,
  ChevronRight,
  Copy,
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
  ProductDependantEditor,
} from "@/components/enrollment/FlexPricingCard";
import {
  LifeVoluntaryPanel,
  recommendedRateBandFor,
  voluntaryRateIssues,
} from "@/components/enrollment/LifeVoluntaryPanel";

type ProductStats = { total: number; edited: number; missing: number };
type CohortGroup = { id: string; label: string; tiers: FlexPricingTier[] };

function isAgeBanded(product: FlexPricingProduct): boolean {
  return product.tiers.some((tier) => tier.pricing_mode === "age_banded");
}

function fixedTiersFor(product: FlexPricingProduct): FlexPricingTier[] {
  return product.tiers.filter((tier) => tier.pricing_mode === "plan_type");
}

function ageBandedTiersFor(product: FlexPricingProduct): FlexPricingTier[] {
  return product.tiers.filter((tier) => tier.pricing_mode === "age_banded");
}

function optionIdentity(tier: FlexPricingTier): string {
  return tier.option_id;
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
    const override = pricing?.products?.[product.product_id]?.voluntary_rates;
    const recommended = product.voluntary_rates ?? [];
    const rates = override ?? recommended;
    total += rates.length;
    if (override) {
      const matchedRecommendations = new Set<VoluntaryRateBand>();
      let unmatchedRates = 0;
      for (const rate of rates) {
        const original = recommendedRateBandFor(recommended, rate);
        if (original && !matchedRecommendations.has(original)) {
          matchedRecommendations.add(original);
          if (!sameRateBand(original, rate)) edited += 1;
        } else {
          unmatchedRates += 1;
        }
      }
      edited += Math.max(
        unmatchedRates,
        recommended.length - matchedRecommendations.size,
      );
    }
    missing += voluntaryRateIssues(rates).length;
    for (const tier of ageTiers) {
      if (overrideFor(pricing, product, tier) != null) edited += 1;
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

function CategoryPriceTable({
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
  const optionCounts = new Map<string, number>();
  for (const tier of product.tiers) {
    const identity = optionIdentity(tier);
    optionCounts.set(identity, (optionCounts.get(identity) ?? 0) + 1);
  }

  return (
    <>
      <p className="border-t border-border px-4 py-2 text-2xs text-muted-foreground sm:hidden">
        Swipe horizontally to compare categories, recommendations, and price tags.
      </p>
      <div
        className="overflow-x-auto border-t border-border"
        role="region"
        aria-label={`${product.product_code} employee-category price tags`}
        tabIndex={0}
      >
      <table className="w-full min-w-[860px] text-sm">
        <thead className="bg-muted/40 text-xs text-muted-foreground">
          <tr className="border-b border-border">
            <th scope="col" className="sticky left-0 z-10 w-[28%] bg-muted px-4 py-2.5 text-left font-medium">
              Employee category
            </th>
            <th scope="col" className="w-[17%] px-3 py-2.5 text-left font-medium">
              Plan or option
            </th>
            <th scope="col" className="w-[12%] px-3 py-2.5 text-left font-medium">
              Relationship
            </th>
            <th scope="col" className="w-[14%] px-3 py-2.5 text-right font-medium">
              Recommended
            </th>
            <th scope="col" className="w-[18%] px-3 py-2.5 text-left font-medium">
              Price tag
            </th>
            <th scope="col" className="px-3 py-2.5 text-left font-medium">
              State
            </th>
          </tr>
        </thead>
        {cohorts.map((cohort) => (
          <tbody key={cohort.id} className="border-b border-border last:border-b-0">
            {cohort.tiers.map((tier, index) => {
              const override = overrideFor(pricing, product, tier);
              const effective = override ?? tier.slip_premium;
              const identity = optionIdentity(tier);
              const sameOptionKeys = product.tiers
                .filter((candidate) => optionIdentity(candidate) === identity)
                .map((candidate) => candidate.key);
              return (
                <tr key={tier.key} className="align-top hover:bg-muted/20">
                  {index === 0 && (
                    <th
                      scope="rowgroup"
                      rowSpan={cohort.tiers.length}
                      className="sticky left-0 z-[5] bg-card px-4 py-3 text-left font-medium text-foreground"
                    >
                      <span className="block max-w-md leading-snug">{cohort.label}</span>
                      <span className="mt-1 block text-2xs font-normal text-muted-foreground">
                        {cohort.tiers.length} pricing option
                        {cohort.tiers.length === 1 ? "" : "s"}
                      </span>
                    </th>
                  )}
                  <td className="px-3 py-3">
                    <span className="block font-medium text-foreground">{tier.label}</span>
                    {tier.plan_code && !tier.label.includes(tier.plan_code) && (
                      <span className="text-2xs text-muted-foreground">
                        Plan code {tier.plan_code}
                      </span>
                    )}
                  </td>
                  <td className="px-3 py-3">
                    <span className={cn("text-xs", tier.is_baseline && "font-medium")}>
                      {relationshipLabel(tier)}
                    </span>
                  </td>
                  <td className="px-3 py-3 text-right tabular-nums">
                    {tier.slip_premium == null ? (
                      <span className="text-warn">Not detected</span>
                    ) : (
                      fmtMoney(tier.slip_premium)
                    )}
                  </td>
                  <td className="px-3 py-2">
                    <div className="flex items-center gap-1.5">
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
                        className="h-8 w-28 tabular-nums"
                        placeholder="Required"
                      />
                      {editable &&
                        tier.plan_code &&
                        (optionCounts.get(identity) ?? 0) > 1 &&
                        effective != null && (
                          <Button
                            variant="ghost"
                            size="sm"
                            className="h-8 gap-1 px-2 text-2xs"
                            onClick={() =>
                              editor.setPlanPrice(
                                product.product_id,
                                sameOptionKeys,
                                String(effective),
                              )
                            }
                            aria-label={`Apply ${tier.label} price to every employee category`}
                          >
                            <Copy className="size-3.5" aria-hidden="true" />
                            Apply to {sameOptionKeys.length}
                          </Button>
                        )}
                    </div>
                  </td>
                  <td className="px-3 py-3">
                    {override != null ? (
                      <div className="flex items-center gap-1.5">
                        <span className="flex items-center gap-1 text-xs font-medium text-info">
                          <Pencil className="size-3" aria-hidden="true" /> Edited
                        </span>
                        {editable && (
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
                    ) : effective != null ? (
                      <span className="flex items-center gap-1 text-xs text-muted-foreground">
                        <CheckCircle2 className="size-3" aria-hidden="true" />
                        Recommended
                      </span>
                    ) : (
                      <span className="flex items-center gap-1 text-xs font-medium text-warn">
                        <AlertTriangle className="size-3" aria-hidden="true" />
                        Needs pricing
                      </span>
                    )}
                  </td>
                </tr>
              );
            })}
          </tbody>
        ))}
        </table>
      </div>
    </>
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
        <div>
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
          <p className="mt-1 text-2xs text-muted-foreground">
            Recommendations are populated automatically. Fixed-price edits affect
            only the category and plan shown; age-band edits affect that product.
          </p>
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
            const cohorts = cohortsFor(product);
            const isOpen = !!openEditor[product.product_id];
            const ageTiers = ageBandedTiersFor(product);
            const fixedTiers = fixedTiersFor(product);
            const hasAgeBandedTiers = isAgeBanded(product);
            const rateBandCount =
              pricing?.products?.[product.product_id]?.voluntary_rates?.length ??
              product.voluntary_rates?.length ??
              0;
            const fixedProduct = { ...product, tiers: fixedTiers };
            const pricingSummary = hasAgeBandedTiers
              ? fixedTiers.length > 0
                ? `Mixed pricing · ${fixedTiers.length} fixed assignments · ${rateBandCount} rate bands · ${cohorts.length} employee categories`
                : `Age-banded pricing · ${rateBandCount} rate bands · ${cohorts.length} employee categories`
              : `Fixed pricing · ${cohorts.length} employee categories · ${fixedTiers.length} plan assignments`;
            return (
              <section
                key={product.product_id}
                className="overflow-hidden rounded-lg border border-border bg-card"
              >
                <button
                  type="button"
                  onClick={() => onToggleEditor(product.product_id)}
                  aria-expanded={isOpen}
                  className="flex w-full items-start gap-3 px-4 py-3 text-left outline-none transition-colors hover:bg-muted/30 focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-ring/40"
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
                    <p className="mt-1 text-xs text-muted-foreground">
                      {pricingSummary}
                    </p>
                  </div>
                  <span className="hidden pt-1 text-xs font-medium text-muted-foreground sm:block">
                    {isOpen ? "Hide details" : editable ? "Review and edit" : "Review details"}
                  </span>
                </button>

                {isOpen && (
                  <div>
                    {hasAgeBandedTiers ? (
                      <>
                        {fixedTiers.length > 0 && (
                          <div>
                            <div className="border-t border-border bg-muted/25 px-4 py-3">
                              <h4 className="text-sm font-medium text-foreground">
                                Fixed-price tiers
                              </h4>
                              <p className="mt-0.5 text-2xs text-muted-foreground">
                                Compulsory and flat tiers use a direct annual price,
                                independently of the voluntary rates below.
                              </p>
                            </div>
                            <CategoryPriceTable
                              product={fixedProduct}
                              pricing={pricing}
                              editor={editor}
                              editable={editable}
                            />
                          </div>
                        )}
                        {ageTiers.length > 0 && (
                          <LifeVoluntaryPanel
                            product={product}
                            editor={editor}
                            editable={editable}
                          />
                        )}
                      </>
                    ) : (
                      <>
                        <CategoryPriceTable
                          product={product}
                          pricing={pricing}
                          editor={editor}
                          editable={editable}
                        />
                        {editable && (
                          <div className="border-t border-border p-3">
                            <ProductDependantEditor product={product} editor={editor} />
                          </div>
                        )}
                      </>
                    )}
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
