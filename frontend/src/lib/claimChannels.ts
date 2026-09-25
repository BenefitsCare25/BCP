/** Outpatient "channels" — the per-visit / co-pay / per-policy-year groups a GP
 * or specialist schedule states for each way a member can be seen (Panel,
 * Polyclinic, Non-Panel, A&E, WhiteCoat …). Each is ONE line of the product,
 * never the product's overall limit, so the Claim limits tab shows them side
 * by side and edits them one plan column at a time. */
import type { ClaimLimitSetting, SobItemAnswer } from "@/types";
import { copayFields, copayValue } from "@/lib/sob";
import { isAbsentValue } from "@/lib/sobValues";
import { claimLimitSourceForColumn, parsedLimitAmount } from "@/lib/claimLimits";

export const isChannelRow = (item: SobItemAnswer) => item.kind === "copay";

export type YearlyUnit = "money" | "visits" | "wording";

export interface Yearly {
  /** null = a bare number with no unit: the broker must say which. */
  unit: YearlyUnit | null;
  amount: number | null;
  raw: string;
}

const MONEY = /\$|\bsgd\b/i;
const VISITS = /\bvisits?\b/i;

const present = (value: string | null | undefined): string | null => {
  const v = (value ?? "").trim();
  return v && !isAbsentValue(v) ? v : null;
};

const asCharged = (value: string) => /^as charged$/i.test(value.trim());

export function money(value: string | number): string {
  const amount = typeof value === "number" ? value : parsedLimitAmount(value);
  if (amount === null) return String(value);
  return `S$${amount.toLocaleString(undefined, { maximumFractionDigits: 2 })}`;
}

/** Money unless it is wording ("As charged", "Refer to schedule"). */
function amountOrWording(value: string): string {
  if (asCharged(value)) return "as charged";
  return /^\s*(?:s?\$|sgd)?\s*[\d,]+(?:\.\d+)?\s*$/i.test(value) ? `up to ${money(value)}` : value;
}

export function parseYearly(raw: string | null | undefined): Yearly | null {
  const value = present(raw);
  if (!value) return null;
  const amount = parsedLimitAmount(value);
  if (VISITS.test(value)) return { unit: "visits", amount, raw: value };
  if (MONEY.test(value)) return { unit: "money", amount, raw: value };
  if (amount !== null && /^[\d,.\s]+$/.test(value)) return { unit: null, amount, raw: value };
  return { unit: "wording", amount: null, raw: value };
}

/** Stored form of a yearly cap: units written out, so neither the member
 * portal nor the backend has to guess ("5 visits", "S$500"). */
export function formatYearly(unit: YearlyUnit | "none", amount: number | null, wording = ""): string {
  if (unit === "none") return "NA";
  if (unit === "wording") return wording.trim() || "NA";
  if (amount === null) return "NA";
  if (unit === "visits") return `${amount} visit${amount === 1 ? "" : "s"}`;
  return money(amount);
}

export function yearlyText(yearly: Yearly): string {
  if (yearly.unit === "visits" && yearly.amount !== null) {
    return `${yearly.amount} visit${yearly.amount === 1 ? "" : "s"} a year`;
  }
  if (yearly.unit === "money" && yearly.amount !== null) return `${money(yearly.amount)} a year`;
  if (yearly.unit === null) return `“${yearly.raw}” a year · S$ or visits?`;
  return yearly.raw;
}

export interface ChannelFacts {
  /** "As charged", "Up to S$30 a visit", "Restructured: as charged · Private: up to S$120". */
  cover: string | null;
  copay: string | null;
  yearly: Yearly | null;
  /** The slip says NA (or nothing) for every field: this plan doesn't offer it. */
  notCovered: boolean;
  /** Not a single field filled in — usually a leftover template row. */
  empty: boolean;
}

const SITE_LABEL: Record<string, string> = { restructured: "Restructured", private: "Private" };

export function channelFacts(item: SobItemAnswer, columnId: string): ChannelFacts {
  const fields = copayFields(item);
  const raw = (key: string) => copayValue(item, columnId, key);
  const cover: string[] = [];
  const copays: string[] = [];
  for (const { key } of fields) {
    const value = present(raw(key));
    if (!value) continue;
    const site = key.match(/_(restructured|private)$/)?.[1];
    const where = site ? `${SITE_LABEL[site]}: ` : "";
    if (key.startsWith("per_visit")) {
      const text = amountOrWording(value);
      cover.push(site ? `${where}${text}` : text === "as charged" ? "As charged" : `${text} a visit`);
    } else if (key.startsWith("co_payment")) {
      copays.push(`${where}${/%$/.test(value) ? value : money(value)} co-pay`);
    }
  }
  const coverText = cover.length ? cover.join(" · ") : null;
  const yearly = parseYearly(raw("per_policy_year"));
  const empty = fields.every(({ key }) => !raw(key).trim());
  return {
    cover: coverText ? coverText.charAt(0).toUpperCase() + coverText.slice(1) : null,
    copay: copays.length ? copays.join(" · ") : null,
    yearly,
    notCovered: !empty && !coverText && copays.length === 0 && !yearly,
    empty,
  };
}

/** Every field reads the same in both columns — one decision can cover both. */
export function sameChannelValues(item: SobItemAnswer, a: string, b: string): boolean {
  return copayFields(item).every(
    ({ key }) => copayValue(item, a, key).trim().toLowerCase() === copayValue(item, b, key).trim().toLowerCase(),
  );
}

const baseName = (name: string) =>
  name.split(/[—–(]/)[0].toLowerCase().replace(/[^a-z0-9]/g, "");

/** The named channel an empty row duplicates ("Non-Panel —" vs "Non Panel"),
 * the trace an older slip import left behind. */
export function duplicateOf(item: SobItemAnswer, rows: SobItemAnswer[]): SobItemAnswer | null {
  const key = baseName(item.name);
  if (!key) return null;
  return rows.find((other) => other.uid !== item.uid && baseName(other.name) === key) ?? null;
}

export type Counting = "counted" | "shown";

/** The claim-limit setting a channel decision stores for one column, or null
 * when there is nothing to record (no yearly cap and no claim type). Built
 * from the row AFTER its values were edited, so `display` is the wording the
 * backend re-checks at confirmation. */
export function channelSetting(
  item: SobItemAnswer,
  columnId: string,
  counting: Counting,
  scopeCodes: string[],
): ClaimLimitSetting | null {
  const source = claimLimitSourceForColumn(item, columnId);
  const yearly = parseYearly(copayValue(item, columnId, "per_policy_year"));
  const base = {
    currency: "SGD",
    display: source.wording,
    claim_scope_codes: scopeCodes,
    status: "verified" as const,
    source: "manual" as const,
  };
  if (yearly && (yearly.unit === "money" || yearly.unit === "visits") && yearly.amount) {
    return {
      ...base,
      basis: yearly.unit === "money" ? "policy_year" : "visits_per_year",
      amount: yearly.amount,
      ...(counting === "shown" ? { display_only: true } : {}),
    };
  }
  if (yearly) return { ...base, basis: "informational", amount: null };
  if (scopeCodes.length === 0) return null;
  const perVisit = present(copayValue(item, columnId, "per_visit"));
  return {
    ...base,
    basis: perVisit && asCharged(perVisit) ? "as_charged" : perVisit ? "per_visit" : "informational",
    amount: null,
  };
}
