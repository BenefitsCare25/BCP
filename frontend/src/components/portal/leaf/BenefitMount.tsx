/** One insured product, as a mount on the member's leaf.
 *
 * Everything here is something the member HOLDS. Matching internals
 * (method / confidence / rule text) and per-member premium are already nulled
 * server-side by `member_statement.py`, and the slip's own category wording
 * ("Managers (Option 1)") is deliberately not rendered either — it is the
 * broker's filing vocabulary, not an answer to "am I covered for this?".
 *
 * The mount never appears for a product the member does not hold: the leaf
 * shows only what was issued. */
import { useId, useMemo } from "react";
import { Users } from "lucide-react";
import type { CoverageLine, Utilization } from "@/types";
import { Mount, MountRule } from "./Mount";
import { Money } from "./Figure";
import { FillRule } from "./FillRule";
import { ScheduleLeaf } from "./ScheduleLeaf";
import { glossBeside } from "./glossary";

function dependantLabel(d: {
  name: string | null;
  relationship: string | null;
}): string {
  if (d.name && d.relationship) return `${d.name} (${d.relationship})`;
  return d.name ?? d.relationship ?? "Dependant";
}

export function BenefitMount({
  line,
  utilization,
}: {
  line: CoverageLine;
  utilization?: Utilization | null;
}) {
  const titleId = useId();

  // This product's buckets, keyed by the lowercased benefit NAME — the same
  // join key `utilization.py` buckets on. Memoised because a fully-covered
  // member renders eleven of these, and a fresh Map identity each render would
  // also defeat any memo downstream.
  const { usageByBenefit, productUsage } = useMemo(() => {
    const mine = (utilization?.insured ?? []).filter(
      (b) => b.product_code === line.product_code,
    );
    return {
      usageByBenefit: new Map(
        mine
          .filter((b) => b.benefit_key)
          .map((b) => [b.benefit_key!.trim().toLowerCase(), b]),
      ),
      productUsage: mine.find((b) => !b.benefit_key) ?? null,
    };
  }, [utilization, line.product_code]);

  const gloss = glossBeside(
    line.product_name ?? line.product_code,
    line.product_code,
    line.product_name,
  );
  const covered = line.covers_dependants ? line.covered_dependants : [];

  return (
    <Mount
      as="article"
      labelId={titleId}
      label={
        <>
          {line.product_name ?? line.product_code}
          {line.plan_code && (
            <>
              {" "}
              <span className="ml-1 whitespace-nowrap font-normal text-label">
                Plan {line.plan_code}
              </span>
            </>
          )}
        </>
      }
      gloss={gloss}
    >
      {/* Product-level fullness — utilisation, so an empty bar means an unused
          limit and never a benefit the member lacks.
          Rendered only when there is something to report: a product with no
          yearly cap and nothing claimed against it would otherwise print a bare
          "Nothing claimed yet" under its own name, which reads like a verdict
          on the cover rather than a fact about this member's year. The
          "what's left" tab is where an untouched benefit is worth stating. */}
      {productUsage &&
        (productUsage.limit !== null ||
          productUsage.approved > 0 ||
          productUsage.pending > 0) && (
          <div className="mb-3">
            <FillRule
              limit={productUsage.limit}
              approved={productUsage.approved}
              pending={productUsage.pending}
              remaining={productUsage.remaining}
            />
          </div>
        )}

      {/* Amount covered is present only where the surface is allowed it —
          `financials` is nulled for members by design. Rendered when it
          survives so the broker preview and the member stay identical. */}
      {line.financials?.sum_insured != null && (
        <div className="mb-3 flex items-baseline justify-between gap-4">
          <span className="text-row text-label">
            Amount you're covered for
          </span>
          <Money value={line.financials.sum_insured} emphasis="strong" />
        </div>
      )}

      {covered.length > 0 && (
        <div className="mb-3 flex flex-wrap items-baseline gap-x-2 gap-y-1">
          <span className="inline-flex items-center gap-1.5 text-row text-label">
            <Users className="size-3.5" aria-hidden />
            Also covers
          </span>
          <span className="text-row text-record">
            {covered.map(dependantLabel).join(", ")}
          </span>
        </div>
      )}

      <MountRule className="mb-1" />

      <ScheduleLeaf
        schedule={line.benefit_schedule}
        annualPolicyLimit={line.annual_policy_limit}
        coverDescription={line.cover_description}
        usageByBenefit={usageByBenefit}
        titleId={titleId}
      />
    </Mount>
  );
}
