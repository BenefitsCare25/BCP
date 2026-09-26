/** Everything about one coverage row that isn't a column: why this cover
 * applies, who else it reaches, the rate detail behind the premium, the
 * schedule itself — and where each of those is edited. */
import { Fragment } from "react";
import { SectionLabel } from "@/components/ui/section-label";
import { BenefitScheduleView } from "@/components/configuration/BenefitScheduleView";
import { fmtMoney } from "@/lib/format";
import type { CoverageLine, UtilizationBucket } from "@/types";
import { EnrolmentControls } from "./EnrolmentControls";
import { SourceLinks } from "./SourceLink";
import type { ProductUsage } from "./usage";

const METHOD_LABEL: Record<string, string> = {
  exact_name: "Exact match",
  fuzzy_name: "Name match",
  rule: "Rule match",
  manual_override: "Manual override",
};

/** Benefits this product has claims against that the schedule doesn't name.
 * The schedule merges usage by benefit NAME, so an unmatched key has no row
 * to merge into and would otherwise vanish. */
function unmatchedBuckets(
  line: CoverageLine,
  usage: ProductUsage | undefined,
): UtilizationBucket[] {
  if (!usage || usage.byBenefit.size === 0) return [];
  const named = new Set(
    (line.benefit_schedule?.items ?? []).map((i) =>
      (i.name ?? "").trim().toLowerCase(),
    ),
  );
  return [...usage.byBenefit].filter(([k]) => !named.has(k)).map(([, b]) => b);
}

function MetaLine({ parts }: { parts: (string | null | undefined)[] }) {
  const shown = parts.filter((p): p is string => Boolean(p && p.trim()));
  if (shown.length === 0) return null;
  return (
    <p className="text-xs text-muted-foreground">
      {shown.map((p, i) => (
        <Fragment key={i}>
          {i > 0 && <span aria-hidden className="mx-1.5 text-subtle">·</span>}
          {p}
        </Fragment>
      ))}
    </p>
  );
}

export function CoverageDetail({
  line,
  usage,
  employeeId,
  canEdit,
}: {
  line: CoverageLine;
  usage: ProductUsage | undefined;
  employeeId?: string;
  canEdit?: boolean;
}) {
  const fin = line.financials;
  const voluntary =
    fin?.rate_basis === "age_banded" || (fin?.voluntary_rates?.length ?? 0) > 0;
  const confidence =
    line.match_confidence != null && line.match_method === "fuzzy_name"
      ? `${Math.round(line.match_confidence * 100)}%`
      : null;
  const orphanRows = unmatchedBuckets(line, usage);
  const hasSchedule = Boolean(line.benefit_schedule?.items?.length);

  return (
    <div className="flex flex-col gap-3 border-t border-border bg-muted/25 px-3 py-3.5">
      <MetaLine
        parts={[
          line.match_method
            ? `${METHOD_LABEL[line.match_method] ?? line.match_method}${
                confidence ? ` · ${confidence}` : ""
              }`
            : null,
          line.rule_human_readable ? `Rule: ${line.rule_human_readable}` : null,
          voluntary && fin?.premium_rate != null
            ? `Rate ${fin.premium_rate} per $1,000 of cover (age band)`
            : null,
          line.premium_note && fin?.annual_premium != null
            ? `Premium: ${line.premium_note}`
            : null,
        ]}
      />

      <EnrolmentControls line={line} employeeId={employeeId} canEdit={canEdit} />

      {hasSchedule ? (
        <BenefitScheduleView
          schedule={line.benefit_schedule!}
          annualPolicyLimit={line.annual_policy_limit}
          usageByBenefit={usage?.byBenefit}
        />
      ) : (
        <p className="text-xs text-muted-foreground">
          No schedule of benefits recorded for this plan.
        </p>
      )}

      {orphanRows.length > 0 && (
        <div className="flex flex-col gap-1 border-t border-border pt-2.5">
          <SectionLabel as="h4">Claimed against benefits not in this schedule</SectionLabel>
          {orphanRows.map((b) => (
            <div
              key={b.benefit_key}
              className="flex items-baseline justify-between gap-3 text-xs"
            >
              <span className="min-w-0 break-words text-foreground">
                {b.benefit_key}
              </span>
              <span className="shrink-0 tabular-nums text-muted-foreground">
                {fmtMoney(b.approved)} approved
                {b.pending > 0 ? ` · ${fmtMoney(b.pending)} pending` : ""}
                {/* Absent from `pending` by design — see BenefitStatement. */}
                {b.pending_unconverted > 0 ? (
                  <span className="text-warn">
                    {" · "}
                    {b.pending_unconverted} awaiting conversion
                  </span>
                ) : null}
              </span>
            </div>
          ))}
        </div>
      )}

      <div className="border-t border-border pt-2.5">
        <SourceLinks
          productCode={line.product_code}
          categoryId={line.category_id}
          hasSchedule={hasSchedule}
        />
      </div>
    </div>
  );
}
