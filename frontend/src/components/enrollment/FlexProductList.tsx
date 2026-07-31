/** The per-product flex price-tag list — one row per flex product with its plan
 * price tags, shared by BOTH surfaces so they can't disagree about what a plan
 * costs the wallet:
 *
 *  - the enrollment **Flex tab** (`editable`) owns the PRICES: the year-level
 *    matrix, dependant pricing and age-banded voluntary rates.
 *  - the **window form** (not `editable`) owns the per-window price-tag SOURCE
 *    (slip vs manual matrix — a column on EnrollmentWindow), and only previews
 *    the resulting tags.
 *
 * Splitting it that way is deliberate: the matrix is per policy YEAR, so burying
 * its editor inside a create-window form made year-level prices unreachable
 * unless you were opening a window.
 */
import { ChevronDown, ChevronRight } from "lucide-react";
import type { ReactNode } from "react";
import {
  type FlexPriceSource,
  type FlexPricingBag,
  type FlexPricingProduct,
} from "@/api/enrollment";
import { type PlanRow, planRows, planScalar } from "@/lib/flexTiers";
import { fmtCurrency } from "@/lib/format";
import { cn } from "@/lib/cn";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Segmented } from "@/components/ui/segmented";
import {
  type FlexPricingEditor,
  ProductFlexEditor,
} from "@/components/enrollment/FlexPricingCard";
import { LifeVoluntaryPanel } from "@/components/enrollment/LifeVoluntaryPanel";

/** The collapsed price-tag chips, one per plan. Cohort tiers that share a plan and
 * price identically fold into a single chip (mirroring the editor); a plan whose
 * cohorts genuinely differ stays split (each carrying its cohort label) so nothing
 * is silently merged. Folds off the SAVED pricing so the chip can't disagree with
 * the values it shows. */
export function planPreviewRows(
  product: FlexPricingProduct,
  bag: FlexPricingBag | undefined,
): PlanRow[] {
  const tags = bag?.products?.[product.product_id]?.price_tags;
  return planRows(product.tiers, (t) => [t.slip_premium, planScalar(tags, t.key)]);
}

/** One plan's exact price tag: a broker-set matrix value is a sparse OVERRIDE that
 * wins; otherwise the "slip" source falls back to the slip premium. A single
 * number, or a range only when the row varies by age band. "—" when unpriced.
 * Scans every key in the row so a folded plan reads its (consistent) value. */
export function planTag(
  bag: FlexPricingBag | undefined,
  product: FlexPricingProduct,
  row: PlanRow,
  source: FlexPriceSource,
): string {
  for (const key of row.keys) {
    const cell = bag?.products?.[product.product_id]?.price_tags?.[key];
    const vals = cell
      ? Object.values(cell).filter((v): v is number => typeof v === "number")
      : [];
    if (vals.length) {
      const min = Math.min(...vals);
      const max = Math.max(...vals);
      return min === max ? fmtCurrency(min) : `${fmtCurrency(min)}–${fmtCurrency(max)}`;
    }
  }
  if (source === "slip" && row.rep.slip_premium != null)
    return fmtCurrency(row.rep.slip_premium);
  return "—";
}

/** Products publishing a slip voluntary rate table price by age band —
 * shape-driven (not line-gated), so any product with the table gets the
 * age-banded panel + live preview instead of the matrix. */
function isLifeVoluntary(p: FlexPricingProduct): boolean {
  return (p.voluntary_rates?.length ?? 0) > 0;
}

export function FlexProductList({
  products,
  pricing,
  editor,
  sourceFor,
  onSourceChange,
  openEditor,
  onToggleEditor,
  emptyHint,
}: {
  products: FlexPricingProduct[];
  pricing: FlexPricingBag | undefined;
  editor: FlexPricingEditor;
  sourceFor: (productId: string) => FlexPriceSource;
  /** Omit to hide the source toggle — the source is a WINDOW property, so only
   *  the window form may change it. */
  onSourceChange?: (productId: string, next: FlexPriceSource) => void;
  /** Omit both to render preview-only rows (no expandable editors). */
  openEditor?: Record<string, boolean>;
  onToggleEditor?: (productId: string) => void;
  emptyHint?: ReactNode;
}) {
  const editable = !!openEditor && !!onToggleEditor;
  if (!products.length) return <>{emptyHint ?? null}</>;

  return (
    <div className="divide-y divide-border">
      {products.map((p) => {
        const source = sourceFor(p.product_id);
        const tags = planPreviewRows(p, pricing).map((row) => ({
          row,
          tag: planTag(pricing, p, row, source),
        }));
        const anyPriced = tags.some((x) => x.tag !== "—");
        const lifeVoluntary = isLifeVoluntary(p);
        const isOpen = editable && (openEditor[p.product_id] ?? source === "manual");
        return (
          <div key={p.product_id} className="py-2">
            <div className="flex flex-wrap items-start justify-between gap-x-3 gap-y-1.5">
              <div className="min-w-0">
                <div className="flex items-center gap-1.5 text-sm font-medium text-foreground">
                  {p.product_code}
                  <Badge variant="outline" className="text-[10px] capitalize">
                    {p.line}
                  </Badge>
                  {!onSourceChange && !lifeVoluntary && (
                    <Badge variant="outline" className="text-[10px] font-normal">
                      {source === "slip" ? "From slip" : "Manual matrix"}
                    </Badge>
                  )}
                </div>
                {!isOpen &&
                  (lifeVoluntary ? (
                    <div className="mt-0.5 text-[11px] text-muted-foreground">
                      Age-banded voluntary rates ({p.voluntary_rates?.length ?? 0}{" "}
                      bands){editable ? " — expand to preview premiums." : "."}
                    </div>
                  ) : anyPriced ? (
                    <div className="mt-1 flex flex-wrap gap-1">
                      {tags.map(({ row, tag }) => (
                        <span
                          key={row.rep.key}
                          title={row.rep.is_baseline ? "Default plan" : undefined}
                          className={cn(
                            "inline-flex items-center gap-1 rounded border px-1.5 py-0.5 text-[11px]",
                            row.rep.is_baseline
                              ? "border-transparent bg-sidebar-active text-sidebar-active-foreground"
                              : "border-border bg-card",
                          )}
                        >
                          <span
                            className={cn(
                              !row.rep.is_baseline && "text-muted-foreground",
                            )}
                          >
                            {row.rep.label}
                            {row.cohortLabel && (
                              <span className="ml-1 opacity-70">
                                · {row.cohortLabel}
                              </span>
                            )}
                          </span>
                          <span
                            className={cn(
                              "font-medium",
                              !row.rep.is_baseline && "text-foreground",
                            )}
                          >
                            {tag}
                          </span>
                        </span>
                      ))}
                    </div>
                  ) : (
                    <div className="mt-0.5 text-[11px] text-warn">
                      {source === "slip"
                        ? "No slip premiums for this product."
                        : editable
                          ? "No matrix prices yet — set them below."
                          : "No matrix prices yet — set them on the Flex tab."}
                    </div>
                  ))}
              </div>
              <div className="flex items-center gap-1.5">
                {/* Life-voluntary products price off the rate table, not the
                    slip-vs-matrix source, so the toggle would be a no-op. */}
                {onSourceChange && !lifeVoluntary && (
                  <Segmented
                    value={source}
                    onChange={(v) => onSourceChange(p.product_id, v)}
                    options={[
                      { value: "slip", label: "From slip" },
                      { value: "manual", label: "Manual matrix" },
                    ]}
                  />
                )}
                {editable && (
                  <Button
                    variant="ghost"
                    size="icon"
                    className="size-7"
                    onClick={() => onToggleEditor(p.product_id)}
                    aria-label={isOpen ? "Hide price tags" : "Edit price tags"}
                  >
                    {isOpen ? (
                      <ChevronDown className="size-4" />
                    ) : (
                      <ChevronRight className="size-4" />
                    )}
                  </Button>
                )}
              </div>
            </div>
            {isOpen && (
              <div className="mt-2">
                {lifeVoluntary ? (
                  <LifeVoluntaryPanel product={p} editor={editor} />
                ) : (
                  <ProductFlexEditor product={p} editor={editor} source={source} />
                )}
              </div>
            )}
          </div>
        );
      })}
    </div>
  );
}
