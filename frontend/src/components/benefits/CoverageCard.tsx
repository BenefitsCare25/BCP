import { useMemo, type ReactNode } from "react";
import { Users } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { InfoHint } from "@/components/ui/tooltip";
import { BenefitScheduleView } from "@/components/configuration/BenefitScheduleView";
import { fmtAmount } from "@/lib/format";
import type { CoverageLine, PlanFinancials, Utilization } from "@/types";

const METHOD_LABEL: Record<string, string> = {
  exact_name: "Exact match",
  fuzzy_name: "Fuzzy match",
  rule: "Rule match",
  manual_override: "Manual override",
};

function depLabel(d: { name: string | null; relationship: string | null }): string {
  if (d.name && d.relationship) return `${d.name} (${d.relationship})`;
  return d.name ?? d.relationship ?? "Dependant";
}

// Per-employee Amount Covered + premium. For a voluntary life tier the premium is
// age-banded to the member; if their age isn't known the premium can't be pinned.
function CoverageFinancials({ fin }: { fin: PlanFinancials }) {
  const voluntary =
    fin.rate_basis === "age_banded" || (fin.voluntary_rates?.length ?? 0) > 0;
  const stats: { label: string; value: string; hint?: ReactNode }[] = [];
  if (fin.sum_insured != null)
    stats.push({ label: "Amount covered", value: fmtAmount(fin.sum_insured) });
  const premiumLabel = fin.gst_included
    ? "Premium (annual, incl. GST)"
    : "Premium (annual)";
  const premiumHint = fin.gst_included
    ? "The yearly insurance cost for this plan, with 9% GST already added."
    : "The yearly insurance cost for this plan (GST-exclusive).";
  if (fin.annual_premium != null)
    stats.push({
      label: premiumLabel,
      value: fmtAmount(fin.annual_premium),
      hint: premiumHint,
    });
  else if (voluntary)
    stats.push({ label: premiumLabel, value: "—", hint: premiumHint });
  if (voluntary && fin.premium_rate != null)
    stats.push({
      label: "Rate (per S$1k, age band)",
      value: String(fin.premium_rate),
      hint: "Premium rate per S$1,000 of the amount covered, set by the member's age band.",
    });
  if (!stats.length) return null;
  return (
    <div className="mt-3 flex flex-wrap gap-x-6 gap-y-1.5 rounded-md border border-border bg-muted/40 p-2.5">
      {stats.map((s) => (
        <div key={s.label} className="flex flex-col">
          <span className="flex items-center gap-1 text-2xs uppercase tracking-wider text-muted-foreground">
            {s.label}
            {s.hint ? <InfoHint>{s.hint}</InfoHint> : null}
          </span>
          <span className="text-sm font-medium text-foreground">{s.value}</span>
        </div>
      ))}
    </div>
  );
}

export function CoverageCard({
  line,
  utilization,
}: {
  line: CoverageLine;
  utilization?: Utilization | null;
}) {
  // Only this product's buckets, keyed by the benefit NAME the schedule uses —
  // the same lowercased join key utilization.py buckets on. Memoised: a member
  // with 11 products renders 11 of these, and a fresh Map identity on every
  // render would also defeat any memo on the schedule below.
  const { usageByBenefit, productUsage } = useMemo(() => {
    const buckets = utilization?.insured ?? [];
    const mine = buckets.filter((b) => b.product_code === line.product_code);
    return {
      usageByBenefit: new Map(
        mine
          .filter((b) => b.benefit_key)
          .map((b) => [b.benefit_key!.trim().toLowerCase(), b]),
      ),
      productUsage: mine.find((b) => !b.benefit_key) ?? null,
    };
  }, [utilization, line.product_code]);
  const hasSchedule = Boolean(line.benefit_schedule?.items?.length);
  const confidencePct =
    line.match_confidence != null
      ? `${Math.round(line.match_confidence * 100)}%`
      : null;

  return (
    <div className="rounded-lg border border-border bg-card p-4">
      <div className="flex flex-wrap items-start justify-between gap-2">
        <div>
          <div className="flex items-center gap-2">
            <span className="rounded bg-muted px-1.5 py-0.5 font-mono text-2xs text-muted-foreground">
              {line.product_code}
            </span>
            <h3 className="text-sm font-semibold text-foreground">
              {line.product_name ?? line.product_code}
            </h3>
          </div>
          {line.category_display && (
            <p className="mt-1 text-xs text-muted-foreground">
              {line.category_display}
            </p>
          )}
        </div>
        <div className="flex shrink-0 flex-wrap items-center gap-1.5">
          {line.plan_code && (
            <Badge variant="outline">Plan {line.plan_code}</Badge>
          )}
          {line.match_method && (
            <Badge variant="default">
              {METHOD_LABEL[line.match_method] ?? line.match_method}
              {confidencePct ? ` · ${confidencePct}` : ""}
            </Badge>
          )}
        </div>
      </div>

      {line.rule_human_readable && (
        <p className="mt-2 text-2xs italic text-muted-foreground">
          Why assigned: {line.rule_human_readable}
        </p>
      )}

      {line.covers_dependants && line.covered_dependants.length > 0 && (
        <div className="mt-2 flex flex-wrap items-center gap-1.5 text-xs text-muted-foreground">
          <Users className="size-3.5" />
          <span>Extends to:</span>
          {line.covered_dependants.map((d) => (
            <Badge key={d.id} variant="outline">
              {depLabel(d)}
            </Badge>
          ))}
        </div>
      )}

      {line.financials && <CoverageFinancials fin={line.financials} />}

      <div className="mt-3 border-t border-border pt-3">
        {hasSchedule ? (
          <BenefitScheduleView
            schedule={line.benefit_schedule}
            annualPolicyLimit={line.annual_policy_limit}
            coverDescription={line.cover_description}
            usageByBenefit={usageByBenefit}
            productUsage={productUsage}
          />
        ) : (
          <p className="text-xs text-muted-foreground">
            Schedule of benefits not yet configured for this plan.
          </p>
        )}
      </div>
    </div>
  );
}
