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
      !setting.display_only &&
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
      !setting.display_only &&
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
const PER_VISIT = /\/(?:visit|consultation?)\b|\bper\s+(?:visit|consultation?)\b/i;
const PER_DAY = /\/(?:day|night)\b|\bper\s+(?:day|night)\b|\bdaily\b/i;
const ANNUAL =/\bannual(?:ly)?\b|\bper\s+(?:policy\s+)?year\b|\bper\s+annum\b|\/year\b/i;
const BARE_AMOUNT_WORDS = /s?\$|\bsgd\b|\d[\d,]*(?:\.\d+)?|\b(?:overall|maximum|max|limit|up|to|of)\b/gi;

/** Mirror of backend `claim_limits.infer_limit_basis` (a SUGGESTION only). */
export function inferredLimitBasis(value: string | null): ClaimLimitBasis {
  const text = (value ?? "").trim().toLowerCase();
  // "As Charged up to Overall Annual Limit of $1,000" is a S$1,000 yearly cap.
  // Only when nothing narrower is stated ("S$30 per visit, 10 visits per year").
  if (
    ANNUAL.test(text) &&
    MONEY.test(text) &&
    !text.includes("%") &&
    !PER_VISIT.test(text) &&
    !PER_DAY.test(text) &&
    !VISIT_COUNT.test(text)
  ) {
    return "policy_year";
  }
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
  // Only a bare amount ("3000", "S$500", "Maximum SGD 1,000") reads as a yearly
  // cap; "SGD 250 per tooth" or "S$15 million any one claim" says something else.
  return /\d/.test(text) && !text.replace(BARE_AMOUNT_WORDS, "").replace(/[\s.,:;()-]/g, "")
    ? "policy_year"
    : "informational";
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

/** A per-policy-year figure with no unit ("5"): dollars or visits, only the
 * broker knows. Mirror of backend `claim_limits.unitless_policy_year`. */
export function isUnitlessPolicyYear(source: ClaimLimitSource): boolean {
  return (
    source.structuredPolicyYear &&
    !source.monetary &&
    !VISIT_COUNT.test(source.wording ?? "") &&
    /\d/.test(source.wording ?? "")
  );
}

/** Whether Confirm setup would be refused for this cell — the same rules as
 * backend `validate_schedule_limits`, so "to review" means exactly that.
 * `source` is `undefined` for the overall limit, which has no SOB cell. */
export function blocksConfirm(
  setting: ClaimLimitSetting | null | undefined,
  source: ClaimLimitSource | undefined,
): boolean {
  if (!setting) return false;
  if (setting.status === "needs_review") {
    if (TRACKED_BASES.has(setting.basis)) return true;
    return setting.basis === "informational" && source !== undefined && isUnitlessPolicyYear(source);
  }
  return source !== undefined && sourceChanged(setting, source.wording);
}

/** live = counts down on the portal · review = blocks confirmation ·
 * wording = shown to employees as the policy states it · none = nothing set. */
export type LimitTone = "live" | "review" | "wording" | "none";

const visits = (count: number) => `${count} visit${count === 1 ? "" : "s"}`;

/** One plain-English line for a limit cell, plus how it behaves. */
export function describeLimit(
  setting: ClaimLimitSetting | null | undefined,
  /** `undefined` = the setting has no SOB cell behind it (overall limit). */
  source: ClaimLimitSource | undefined,
  /** The row is hidden from employees in the SOB. */
  hidden = false,
): { text: string; tone: LimitTone } {
  const wording = source?.wording;
  if (!setting) {
    const text = readableWording(wording);
    return { text: text ?? "—", tone: text ? "wording" : "none" };
  }
  const changed =
    source !== undefined && setting.status !== "needs_review" && sourceChanged(setting, wording ?? null);
  // A drawdown balance on What's left for a line What's covered hides:
  // Confirm setup refuses it (backend `validate_schedule_limits`).
  const hiddenDrawdown = hidden && isLiveTrackedLimit(setting);
  // "Not a limit" and "As charged" state no limit, so they carry no state
  // badge and never count as a displayed limit.
  const tone: LimitTone = blocksConfirm(setting, source) || hiddenDrawdown
    ? "review"
    : isLiveTrackedLimit(setting)
      ? "live"
      : setting.status === "not_limit" || setting.basis === "as_charged"
        ? "none"
        : "wording";
  const amount = setting.amount;
  const text = (() => {
    if (setting.status === "not_limit") return "Not a limit";
    if (source && setting.status === "needs_review" && isUnitlessPolicyYear(source)) {
      return `“${parsedLimitAmount(wording ?? null)}” a year · S$ or visits?`;
    }
    switch (setting.basis) {
      case "policy_year":
        return amount ? `${money(amount)} a year` : "Amount not set";
      case "visits_per_year":
        return amount ? `${visits(amount)} a year` : "Visit count not set";
      case "as_charged":
        return "As charged";
      default:
        return readableWording(wording) ?? CLAIM_LIMIT_BASIS_LABELS[setting.basis];
    }
  })();
  const flagged = hiddenDrawdown ? `${text} · hidden in SOB` : text;
  return { text: changed ? `${flagged} · slip changed` : flagged, tone };
}

/** What an employee will read for this setting on the portal. */
export function memberPreview(setting: ClaimLimitSetting, wording: string | null | undefined): string {
  if (setting.display_only && setting.amount) {
    const cap = setting.basis === "visits_per_year" ? visits(setting.amount) : money(setting.amount);
    return `Up to ${cap} a year`;
  }
  if (setting.basis === "policy_year" && setting.amount) {
    return `${money(setting.amount)} left of ${money(setting.amount)} this year`;
  }
  if (setting.basis === "visits_per_year" && setting.amount) {
    return `${setting.amount} of ${setting.amount} visits left this year`;
  }
  if (setting.basis === "as_charged") return "Covered as charged";
  return wording?.trim() || "—";
}
