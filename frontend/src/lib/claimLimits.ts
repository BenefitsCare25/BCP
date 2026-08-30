import type {
  ClaimLimitBasis,
  ClaimLimitSetting,
  SobItemAnswer,
  SobSchedule,
} from "@/types";
import { cellValue } from "@/lib/sob";

export const CLAIM_LIMIT_BASIS_LABELS: Record<ClaimLimitBasis, string> = {
  policy_year: "Per policy year",
  lifetime: "Lifetime",
  per_visit: "Per visit",
  per_day: "Per day",
  percentage: "Percentage / co-pay",
  as_charged: "As charged",
  informational: "Other policy wording",
};

export const CLAIM_LIMIT_BASES = Object.keys(
  CLAIM_LIMIT_BASIS_LABELS,
) as ClaimLimitBasis[];

export function isLiveAnnualLimit(
  setting: ClaimLimitSetting | null | undefined,
): boolean {
  return Boolean(
    setting &&
      setting.status === "verified" &&
      setting.basis === "policy_year" &&
      setting.currency === "SGD" &&
      setting.amount !== null &&
      setting.amount > 0,
  );
}

/** The conservative amount a member can read as available after submissions
 * already in flight. A foreign pending claim without a policy-currency amount
 * makes that figure unknowable, so callers must say so instead of guessing. */
export function availableAfterPending(
  remaining: number | null,
  pending: number,
  pendingUnconverted = 0,
): number | null {
  if (remaining === null || pendingUnconverted > 0) return null;
  return Math.max(0, remaining - pending);
}

export function inferredLimitBasis(value: string | null): ClaimLimitBasis {
  const text = (value ?? "").trim().toLowerCase();
  if (text.includes("as charged")) return "as_charged";
  if (text.includes("%")) return "percentage";
  if (/\/(?:day|night)\b|\bper\s+(?:day|night)\b|\bdaily\b/i.test(text)) {
    return "per_day";
  }
  if (/\/(?:visit|consultation?)\b|\bper\s+(?:visit|consultation?)\b/i.test(text)) {
    return "per_visit";
  }
  if (text.includes("lifetime")) return "lifetime";
  if (/\bper\s+(?:policy\s+)?year\b|\bper\s+annum\b|\/year\b/i.test(text)) {
    return "policy_year";
  }
  return /\d/.test(text) ? "policy_year" : "informational";
}

export function parsedLimitAmount(value: string | null): number | null {
  const match = (value ?? "").match(/\d[\d,]*(?:\.\d+)?/);
  if (!match) return null;
  const amount = Number(match[0].replaceAll(",", ""));
  return Number.isFinite(amount) && amount >= 0 ? amount : null;
}

export function draftLimitSetting(
  display: string | null,
  claimScopeCodes: string[] = [],
): ClaimLimitSetting {
  const basis = inferredLimitBasis(display);
  return {
    basis,
    amount: basis === "policy_year" ? parsedLimitAmount(display) : null,
    currency: "SGD",
    display: display?.trim() || null,
    claim_scope_codes: claimScopeCodes,
    status: "needs_review",
    source: display?.trim() ? "detected" : "manual",
  };
}

export interface ClaimLimitSource {
  wording: string | null;
  structuredPolicyYear: boolean;
  monetary: boolean;
}

export function hasMonetaryContext(value: string | null): boolean {
  return /\$|\bsgd\b|\bdollars?\b/i.test(value ?? "");
}

export function draftDetectedLimitSetting(
  source: ClaimLimitSource,
  claimScopeCodes: string[] = [],
): ClaimLimitSetting {
  const draft = draftLimitSetting(source.wording, claimScopeCodes);
  if (source.structuredPolicyYear && !source.monetary) {
    return { ...draft, basis: "informational", amount: null };
  }
  return draft;
}

export function columnIdForPlan(sob: SobSchedule, planCode: string): string | null {
  const direct = sob.columns.find((column) => column.plan_codes.includes(planCode));
  if (direct) return direct.id;
  return sob.columns.length === 1 ? sob.columns[0].id : null;
}

export function itemLimitForPlan(
  sob: SobSchedule,
  item: SobItemAnswer,
  planCode: string,
): ClaimLimitSetting | null {
  const columnId = columnIdForPlan(sob, planCode);
  return columnId ? item.claim_limits?.[columnId] ?? null : null;
}

export function claimLimitSourceForPlan(
  sob: SobSchedule,
  item: SobItemAnswer,
  planCode: string,
): ClaimLimitSource {
  const columnId = columnIdForPlan(sob, planCode);
  if (!columnId) {
    return { wording: null, structuredPolicyYear: false, monetary: false };
  }
  const rawPolicyYear =
    item.column_properties?.[columnId]?.per_policy_year ??
    item.properties?.per_policy_year;
  const policyYear = String(rawPolicyYear ?? "").trim();
  if (
    policyYear &&
    !["na", "n/a", "not applicable", "not covered"].includes(
      policyYear.toLowerCase(),
    )
  ) {
    return {
      wording: /\byear\b/i.test(policyYear)
        ? policyYear
        : `${policyYear} per policy year`,
      structuredPolicyYear: true,
      monetary:
        hasMonetaryContext(policyYear) || /\bas charged\b/i.test(policyYear),
    };
  }
  const wording = cellValue(item, columnId) || null;
  return {
    wording,
    structuredPolicyYear: false,
    monetary: hasMonetaryContext(wording),
  };
}

export function sourceWordingForPlan(
  sob: SobSchedule,
  item: SobItemAnswer,
  planCode: string,
): string | null {
  return claimLimitSourceForPlan(sob, item, planCode).wording;
}
