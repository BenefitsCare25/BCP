import type {
  ClaimLimitBasis,
  ClaimLimitScope,
  ClaimLimitSetting,
  SobItemAnswer,
  SobSchedule,
} from "@/types";
import { cellValue } from "@/lib/sob";
import { isAbsentValue } from "@/lib/sobValues";

export const CLAIM_LIMIT_BASIS_LABELS: Record<ClaimLimitBasis, string> = {
  policy_year: "Per policy year",
  visits_per_year: "Visits per policy year",
  per_disability: "Per disability",
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

/** Bases that keep a running count against claims (SGD per year, visits per
 * year) — the only ones that create a member balance and an approval guard. */
export const TRACKED_BASES = new Set<ClaimLimitBasis>(["policy_year", "visits_per_year"]);

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

export function isLiveVisitLimit(setting: ClaimLimitSetting | null | undefined): boolean {
  return Boolean(
    setting &&
      setting.status === "verified" &&
      setting.basis === "visits_per_year" &&
      setting.amount !== null &&
      Number.isInteger(setting.amount) &&
      setting.amount >= 1,
  );
}

/** A setting that counts down against claims and guards approval. */
export function isLiveTrackedLimit(setting: ClaimLimitSetting | null | undefined): boolean {
  return isLiveAnnualLimit(setting) || isLiveVisitLimit(setting);
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

const VISIT_COUNT = /\b(\d{1,3})\s*visits?\b/i;
const MONEY = /\$|\bsgd\b|\bdollars?\b/i;

/** Mirror of backend `claim_limits.infer_limit_basis` (a SUGGESTION only). */
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
  if (VISIT_COUNT.test(text) && !MONEY.test(text)) return "visits_per_year";
  if (/\bper\s+(?:disability|admission)\b/i.test(text)) return "per_disability";
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
  const visits = basis === "visits_per_year" ? (display ?? "").match(VISIT_COUNT) : null;
  return {
    basis,
    amount:
      basis === "policy_year"
        ? parsedLimitAmount(display)
        : visits
          ? Number(visits[1])
          : null,
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
  return MONEY.test(value ?? "");
}

export function draftDetectedLimitSetting(
  source: ClaimLimitSource,
  claimScopeCodes: string[] = [],
): ClaimLimitSetting {
  const draft = draftLimitSetting(source.wording, claimScopeCodes);
  // A bare per-policy-year number carries no unit (dollars or visits), so it
  // stays wording until a broker says which. "5 visits" states its unit — the
  // parser writes it when the slip's formatting shows a count.
  if (source.structuredPolicyYear && !source.monetary && draft.basis !== "visits_per_year") {
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

/** The SoB wording a column's setting is reviewed against (backend
 * `claim_limits.item_source_wording`): a structured per-policy-year copay
 * field wins, otherwise the row's cell. */
export function claimLimitSourceForColumn(
  item: SobItemAnswer,
  columnId: string,
): ClaimLimitSource {
  const rawPolicyYear =
    item.column_properties?.[columnId]?.per_policy_year ??
    item.properties?.per_policy_year;
  const policyYear = String(rawPolicyYear ?? "").trim();
  if (policyYear && !isAbsentValue(policyYear)) {
    return {
      wording: /\byear\b/i.test(policyYear)
        ? policyYear
        : `${policyYear} per policy year`,
      structuredPolicyYear: true,
      monetary: hasMonetaryContext(policyYear) || /\bas charged\b/i.test(policyYear),
    };
  }
  const value = cellValue(item, columnId);
  const wording = value && !isAbsentValue(value) ? value : null;
  return { wording, structuredPolicyYear: false, monetary: hasMonetaryContext(wording) };
}

export function claimLimitSourceForPlan(
  sob: SobSchedule,
  item: SobItemAnswer,
  planCode: string,
): ClaimLimitSource {
  const columnId = columnIdForPlan(sob, planCode);
  if (!columnId) return { wording: null, structuredPolicyYear: false, monetary: false };
  return claimLimitSourceForColumn(item, columnId);
}

export function sourceWordingForPlan(
  sob: SobSchedule,
  item: SobItemAnswer,
  planCode: string,
): string | null {
  return claimLimitSourceForPlan(sob, item, planCode).wording;
}

const normalizeWording = (value: string | null | undefined) =>
  (value ?? "").trim().replace(/\s+/g, " ").toLocaleLowerCase();

/** The SoB wording moved after the setting was saved — it must be re-reviewed
 * (backend `validate_schedule_limits` refuses confirmation until it is). */
export function sourceChanged(setting: ClaimLimitSetting, wording: string | null): boolean {
  return normalizeWording(setting.display) !== normalizeWording(wording);
}

/** Claim types suggested for a benefit row, from the backend's own rules
 * (they ride on the template's claim scopes). */
export function suggestedScopeCodes(scopes: ClaimLimitScope[], rowName: string): string[] {
  const name = rowName.replace(/\s+/g, " ").trim().toLowerCase();
  if (!name) return [];
  const exclude = scopes[0]?.exclude_terms ?? [];
  if (exclude.some((term) => name.includes(term))) return [];
  // Checked in the backend's order (`match_priority`), not display order, so
  // the first match here is the one the import seeded.
  const hit = [...scopes]
    .filter((scope) => scope.match_priority != null)
    .sort((a, b) => (a.match_priority ?? 0) - (b.match_priority ?? 0))
    .find((scope) =>
      (scope.match_terms ?? []).some((term) => term.split("+").every((part) => name.includes(part))),
    );
  return hit ? [hit.code] : [];
}

const money = (amount: number) =>
  `S$${amount.toLocaleString(undefined, { maximumFractionDigits: 2 })}`;

/** Slip wording as a chip reads it: a bare amount becomes S$. */
function readableWording(wording: string | null | undefined): string | null {
  if (!wording) return null;
  const bare = wording.trim().replace(/,/g, "");
  return /^\d+(\.\d+)?$/.test(bare) ? money(Number(bare)) : wording;
}

/** Wording that could be a limit: an amount, not a ward name like "1 Bed". */
export function looksLikeLimit(wording: string | null): boolean {
  return /\$|\d{2,}/.test(wording ?? "");
}

export type LimitTone = "live" | "review" | "wording" | "none";

/** One plain-English line for a limit cell, plus whether it counts down. */
export function describeLimit(
  setting: ClaimLimitSetting | null | undefined,
  /** `undefined` = the setting has no SOB cell behind it (overall limit). */
  wording: string | null | undefined,
): { text: string; tone: LimitTone } {
  if (!setting) {
    const text = readableWording(wording);
    return { text: text ?? "—", tone: text ? "wording" : "none" };
  }
  // A broker decision recorded against wording the slip no longer says.
  const changed =
    wording !== undefined && setting.source === "manual" && sourceChanged(setting, wording);
  const tone: LimitTone =
    setting.status === "needs_review" || changed
      ? "review"
      : isLiveTrackedLimit(setting)
        ? "live"
        : "wording";
  const amount = setting.amount;
  const text = (() => {
    switch (setting.basis) {
      case "policy_year":
        return amount ? `${money(amount)} a year` : "Yearly amount not set";
      case "visits_per_year":
        return amount ? `${amount} visit${amount === 1 ? "" : "s"} a year` : "Visit count not set";
      case "as_charged":
        return "As charged";
      default:
        return readableWording(wording) ?? CLAIM_LIMIT_BASIS_LABELS[setting.basis];
    }
  })();
  return { text: changed ? `${text} · slip changed` : text, tone };
}

/** What an employee will read for this setting on the portal. */
export function memberPreview(setting: ClaimLimitSetting, wording: string | null | undefined): string {
  if (setting.basis === "policy_year" && setting.amount) {
    return `“${money(setting.amount)} left of ${money(setting.amount)} this year” — counts down as claims are approved`;
  }
  if (setting.basis === "visits_per_year" && setting.amount) {
    return `“${setting.amount} of ${setting.amount} visits left this year” — counts down as claims are approved`;
  }
  if (setting.basis === "as_charged") return "“Covered as charged, subject to policy terms”";
  return wording ? `“${wording}” — shown as a condition, never a balance` : "Shown as a condition, never a balance";
}
