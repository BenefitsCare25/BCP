/** Schedule-of-Benefits reading logic, shared by the broker's renderer
 * (`components/configuration/BenefitScheduleView`) and the member portal's
 * (`components/portal/leaf/ScheduleLeaf`).
 *
 * These are the rules for *what a schedule says* — which rows carry content,
 * which are reference material, how a stored value formats, and which rows earn
 * the headline. Only presentation differs between the two surfaces; if this
 * logic were duplicated the two could disagree about which benefit a member's
 * claim was filed against, which is exactly the kind of divergence the
 * injected-hook sharing elsewhere in the portal exists to prevent. */
import type {
  BenefitItem,
  BenefitKind,
  BenefitSubItem,
  UtilizationBucket,
} from "@/types";

// `properties` is an open bag: outpatient copay fields (per_visit / co_payment
// / per_policy_year and their site variants), the dental Panel/Non-Panel axis,
// and the slip parser's qualifier keys below.
//
// Only these four are ALSO written into `limits` by the parser
// (placement_slip_sob.py::_SOB_PROPERTY_PATTERNS), so they are the only ones
// that must be suppressed to avoid rendering twice. Everything else is shown.
//
// This is deliberately a deny-list. It used to be an allow-list of copay
// prefixes, which silently dropped every dental Panel/Non-Panel value and would
// drop any future qualifier preset whose key didn't start with a listed prefix.
const MIRRORED_INTO_LIMITS = new Set([
  "maximum_days",
  "qualification_period",
  "co_insurance",
  "surgical_schedule",
]);

export function displayProps(
  properties: Record<string, string> | undefined,
): [string, string][] {
  return Object.entries(properties ?? {}).filter(
    ([k, v]) => !MIRRORED_INTO_LIMITS.has(k) && String(v ?? "").trim() !== "",
  );
}

/** How many valued rows to show before collapsing the rest. A fully-covered
 * employee's schedules run to ~230 rows across all products. */
export const SUMMARY_ROWS = 6;

export function formatValue(
  value: string | null | undefined,
  kind?: BenefitKind,
  /** Currency mark for bare numeric values. The member portal passes "S$",
   * which is what every other figure on that surface is written with — a bare
   * "$" beside `FillRule`'s "S$0 of S$5,000 used" is two conventions for one
   * figure, and reads as USD on a Singapore portal. The broker app keeps "$". */
  symbol = "$",
): string | null {
  if (value == null || value === "") return null;
  const v = value.trim();
  // A DECIMAL part counts as numeric. Excel hands the parser floats, so a
  // money cell reaches us as "500000.0" / "1000.0" — which the integer-only
  // test rejected, printing the raw float (trailing ".0" and all) instead of
  // "$500,000". Six distinct values in CDL's schedules alone rendered that way,
  // on the member's page as well as the broker's.
  const numeric = /^\d{1,3}(,\d{3})*(\.\d+)?$/.test(v) || /^\d+(\.\d+)?$/.test(v);
  const asCurrency = () =>
    numeric
      ? `${symbol}${Number(v.replace(/,/g, "")).toLocaleString(undefined, {
          maximumFractionDigits: 2,
        })}`
      : v;

  // A kind that states the type outright wins over the digits.
  if (kind === "percent") return v.endsWith("%") ? v : `${v}%`;
  if (kind === "days") return /\bdays?\b/i.test(v) ? v : `${v} days`;
  if (kind === "text" || kind === "boolean" || kind === "list" || kind === "scale") {
    return v;
  }
  if (kind === "currency") return asCurrency();

  // `amount` is the DEFAULT kind, not a broker's choice — `sob_columns` stamps
  // it on any row nobody typed a type for, so it carries no more information
  // than no kind at all. Both fall back to the numeric heuristic, which keeps
  // the 400 stored `amount` rows (all of GBT) rendering as currency.
  //
  // This is why a genuine non-money count still needs its kind set explicitly:
  // "Number of Visits: 6" is only distinguishable from "$6" by someone saying so.
  return asCurrency();
}

// `benefit_schedule` is an untyped JSON column server-side (`dict[str, Any]`),
// so `BenefitItem` describes what writers SHOULD produce, not what every stored
// row actually has — seeded and hand-PATCHed schedules routinely omit
// `sub_items` entirely. Read it defensively: a missing key must render an empty
// section, never crash the member's benefits page.
export function subItemsOf(item: BenefitItem): BenefitSubItem[] {
  return Array.isArray(item.sub_items) ? item.sub_items : [];
}

/** A row worth rendering: it says something beyond its own name. */
export function hasContent(item: BenefitItem): boolean {
  return Boolean(
    (item.value != null && item.value !== "") ||
      item.note ||
      (item.limits && item.limits.length > 0) ||
      subItemsOf(item).length > 0 ||
      displayProps(item.properties).length > 0,
  );
}

/** Enumerations (covered conditions, compensation scales) are reference, not limits. */
export function isEnumeration(item: BenefitItem): boolean {
  return (
    (item.kind === "list" || item.kind === "scale") && subItemsOf(item).length > 0
  );
}

export interface ScheduleReading {
  /** Every row worth showing, in document order. */
  items: BenefitItem[];
  /** The rows shown before the member asks for the rest. */
  headline: BenefitItem[];
  /** True when there is a tail behind the disclosure. */
  collapsible: boolean;
  /** The plan has benefit names but no values recorded against them. */
  valuesMissing: boolean;
}

/**
 * Decide what a schedule shows.
 *
 * Rows that say nothing beyond their own name were once rendered as bare
 * labels — a dental plan whose values were never filled in showed nine of them.
 * If dropping them empties the schedule outright, the plan genuinely has names
 * but no values, so the names are kept: they still tell the member what is
 * covered.
 *
 * The headline is the non-enumeration rows, but any row the member has CLAIMED
 * against is always promoted into it. A flat first-N by document order could
 * bury the one balance they opened the page to check behind the disclosure.
 * Document order is preserved among the chosen rows.
 */
export function readSchedule(
  allItems: BenefitItem[] | null | undefined,
  usageByBenefit?: Map<string, UtilizationBucket>,
): ScheduleReading {
  const all = allItems ?? [];
  const withContent = all.filter(hasContent);
  const items = withContent.length > 0 ? withContent : all;
  const valuesMissing = withContent.length === 0 && all.length > 0;

  const candidates = items.filter((i) => !isEnumeration(i));
  const used = (i: BenefitItem) => {
    const b = usageByBenefit?.get((i.name ?? "").trim().toLowerCase());
    return Boolean(b && (b.approved > 0 || b.pending > 0));
  };
  const usedRows = candidates.filter(used);
  const filler = candidates
    .filter((i) => !used(i))
    .slice(0, Math.max(0, SUMMARY_ROWS - usedRows.length));
  const picked = new Set<BenefitItem>([...usedRows, ...filler]);
  const headline = candidates.filter((i) => picked.has(i));

  return {
    items,
    headline,
    collapsible: items.length > headline.length,
    valuesMissing,
  };
}

/** Usage for one schedule row, by the lowercased benefit NAME — the same join
 * key `utilization.py` buckets on. */
export function usageFor(
  item: BenefitItem,
  usageByBenefit?: Map<string, UtilizationBucket>,
): UtilizationBucket | undefined {
  return usageByBenefit?.get((item.name ?? "").trim().toLowerCase());
}
