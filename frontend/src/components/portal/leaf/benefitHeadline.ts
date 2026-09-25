/** The ONE figure a benefit leads with, wherever it is shown.
 *
 * Home, the Coverage cards and "What's left" used to pick their figure three
 * different ways, so the same member read three different stories: Home headed
 * "Hospital & surgery" with the kidney-dialysis SUB-limit as if it were the
 * hospital cover, "What's left" listed one row, and the claim form said no
 * balance existed. The rule is now single-sourced:
 *
 *   1. A tracked balance (money or visits) wins, labelled with ITS OWN name
 *      when it is a sub-limit — never presented as the whole benefit.
 *   2. Otherwise the plan's first stated fact (what you pay, the yearly limit).
 *   3. Otherwise nothing: a sheet with no figure is honest; an invented one is not.
 */
import type { CoverageLine, Utilization, UtilizationBucket } from "@/types";
import { availableAfterPending } from "@/lib/claimLimits";
import { careFacts } from "./careFacts";
import { currencySymbol, moneyText } from "./Figure";

export type BenefitHeadline = {
  label: string;
  value: string;
  note?: string;
  /** 0–1 share used, when a tracked balance backs the figure. */
  used?: number;
};

type VisitBucket = UtilizationBucket & {
  limit_basis?: UtilizationBucket["limit_basis"] | "visits_per_year";
};

export function isTrackedBucket(bucket: VisitBucket): boolean {
  if (bucket.orphaned || bucket.limit_is_enforceable !== true) return false;
  if (bucket.limit_basis === "visits_per_year") {
    return (bucket.visit_limit ?? 0) > 0 && bucket.visits_remaining != null;
  }
  return bucket.limit !== null && bucket.limit > 0 && bucket.remaining !== null;
}

/** Tracked balances for a set of product codes, roll-up rows first. */
export function trackedBuckets(utilization: Utilization | undefined, codes: string[]): VisitBucket[] {
  const wanted = new Set(codes.map((code) => code.trim().toUpperCase()));
  return ((utilization?.insured ?? []) as VisitBucket[])
    .filter((bucket) => wanted.has((bucket.product_code ?? "").trim().toUpperCase()))
    .filter(isTrackedBucket)
    .sort((a, b) => Number(a.benefit_key !== null) - Number(b.benefit_key !== null));
}

export function bucketHeadline(bucket: VisitBucket, currency: string): BenefitHeadline {
  const isSubLimit = bucket.benefit_key !== null;
  if (bucket.limit_basis === "visits_per_year") {
    const limit = bucket.visit_limit ?? 0;
    const used = bucket.visits_used ?? 0;
    return {
      label: isSubLimit ? `${bucket.benefit_key} · visits left` : "Visits left",
      value: `${bucket.visits_remaining} of ${limit}`,
      used: limit ? used / limit : 0,
    };
  }
  const symbol = currencySymbol(currency);
  const afterPending = availableAfterPending(bucket.remaining, bucket.pending, bucket.pending_unconverted);
  const left = afterPending ?? bucket.remaining ?? 0;
  return {
    label: isSubLimit ? `${bucket.benefit_key} · left` : "Left this year",
    value: `${symbol}${moneyText(left)}`,
    note: bucket.pending > 0 ? `${symbol}${moneyText(bucket.pending)} in review` : `of ${symbol}${moneyText(bucket.limit ?? 0)}`,
    used: bucket.limit ? Math.min(1, bucket.approved / bucket.limit) : 0,
  };
}

export function benefitHeadline(
  routeKey: string,
  lines: CoverageLine[],
  utilization: Utilization | undefined,
  currency = "SGD",
): BenefitHeadline | null {
  const tracked = trackedBuckets(utilization, lines.map((line) => line.product_code));
  const rollUp = tracked.find((bucket) => bucket.benefit_key === null);
  if (rollUp) return bucketHeadline(rollUp, currency);
  const fact = lines.map((line) => careFacts(line, routeKey)[0]).find(Boolean);
  const sub = tracked[0] ? bucketHeadline(tracked[0], currency) : null;
  // Only a SUB-limit is tracked: the plan's own fact leads, and the sub-limit
  // rides beneath it under its own name.
  if (fact) {
    return sub
      ? { label: fact.label, value: fact.value, note: `${sub.label.replace(/ · left$/, "")}: ${sub.value} left` }
      : { label: fact.label, value: fact.value };
  }
  return sub;
}
