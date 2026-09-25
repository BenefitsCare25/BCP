/** The key facts at the top of a member's care detail, per product family.
 *
 * Each family answers the questions a member has before arranging that kind of
 * care, in the order they ask them (docs/SOB_LIMITS_REDESIGN_PLAN.md, "Employee
 * presentation, per product"):
 *   hospital — which ward, how long, how much for surgery, before/after a stay;
 *   GP       — what each kind of clinic costs me;
 *   specialist — panel vs non-panel limits, scans, therapy;
 *   dental   — my yearly limit, and how panel vs non-panel is paid.
 *
 * The schedule reaching here is already the MEMBER projection
 * (`backend/app/services/member_schedule.py`): "NA", "Not covered" and insurer
 * admin rows are gone, co-insurance reads as a percentage. So every fact below
 * comes from a value the plan actually states; a missing row produces no fact,
 * never an invented or "NA" one. */
import type { BenefitItem, BenefitSubItem, CoverageLine } from "@/types";
import { formatValue, subItemsOf } from "@/lib/benefitSchedule";
import { isAbsentValue } from "@/lib/sobValues";

export type CareFact = { label: string; value: string; note?: string };

const S$ = "S$";
const MAX_FACTS = 7;

const clean = (value: string | null | undefined): string | null =>
  isAbsentValue(value) ? null : String(value).trim();

const isBareAmount = (value: string) => /^\d{1,3}(,\d{3})*(\.\d+)?$|^\d+(\.\d+)?$/.test(value);

/** Money reads as S$; wording stays as written. */
function money(value: string | null | undefined): string | null {
  const v = clean(value);
  if (!v) return null;
  return isBareAmount(v) ? formatValue(v, "currency", S$) : v;
}

const asCharged = (value: string) => /^as charged$/i.test(value.trim());

/** "Up to S$3,000", "Covered as charged", or the policy's own wording. */
function upTo(value: string | null | undefined, suffix = ""): string | null {
  const v = clean(value);
  if (!v) return null;
  if (asCharged(v)) return "Covered as charged";
  if (isBareAmount(v)) return `Up to ${money(v)}${suffix}`;
  return v;
}

/** A limit qualifier the parser attached, e.g. "Maximum no. of days: 120 days". */
function limitNote(item: { limits?: BenefitItem["limits"] }, match: RegExp): string | null {
  const hit = (item.limits ?? []).find((l) => match.test(l.label) && clean(l.value));
  return hit ? String(hit.value) : null;
}

function prop(item: BenefitItem, key: string): string | null {
  return clean(item.properties?.[key]);
}

const find = (items: BenefitItem[], match: RegExp) =>
  items.find((item) => match.test(item.name ?? ""));

/** The service a sub-row names, without the insurer's bracketed detail. */
const shortName = (name: string) => name.split("(")[0].replace(/\s+/g, " ").trim() || name;

// ── Hospital (GHS / GHS2 / IMP) ──────────────────────────────────────────────

const WARD_PLACES: [RegExp, string][] = [
  [/\bprivate\b/i, "private hospital"],
  [/\brestr(?:uctured|\.)?/i, "restructured hospital"],
  [/\bgov(?:ernmen)?t\b/i, "government hospital"],
];

/** "1 Bed Private" → "1-bed ward, private hospital". Anything else as written. */
function wardEntitlement(value: string): string {
  const match = value.match(/^\s*(\d+)\s*[- ]?\s*beds?\b\s*(.*)$/i);
  if (!match) return value;
  const place = WARD_PLACES.find(([pattern]) => pattern.test(match[2]))?.[1];
  const rest = place ? "" : match[2].trim();
  return [`${match[1]}-bed ward`, place ?? rest].filter(Boolean).join(", ");
}

function hospitalFacts(items: BenefitItem[]): CareFact[] {
  const facts: CareFact[] = [];
  const ward = find(items, /room\s*(?:&|and)?\s*board|ward/i);
  if (ward && clean(ward.value)) {
    const days = limitNote(ward, /days?/i);
    const value = clean(ward.value)!;
    // A ward class and a daily dollar allowance are different facts; never
    // present one as the other.
    const daily = isBareAmount(value);
    facts.push({
      label: daily ? "Room & board" : "Your ward",
      value: daily ? `${money(value)} a day` : wardEntitlement(value),
      note: days ? `Up to ${days}` : undefined,
    });
  }
  const icu = find(items, /\bicu\b|intensive care/i);
  if (icu && clean(icu.value)) {
    const days = limitNote(icu, /days?/i);
    facts.push({ label: "Intensive care", value: upTo(icu.value)!, note: days ? `Up to ${days}` : undefined });
  }
  const inpatient = find(items, /in[- ]?patient (?:expenses|benefits?)|hospital (?:&|and) surgical/i);
  if (inpatient && clean(inpatient.value)) {
    const covers = subItemsOf(inpatient).map((s) => shortName(s.name)).filter(Boolean);
    facts.push({
      label: "Hospital & surgery bills",
      value: upTo(inpatient.value)!,
      note: covers.length ? `Includes ${covers.join(", ").toLowerCase()}` : undefined,
    });
  }
  const prePost = find(items, /pre[- ]?(?:&|and)?\s*post|out[- ]?patient expenses/i);
  if (prePost && clean(prePost.value)) {
    const days = prop(prePost, "qualification_period") ?? limitNote(prePost, /qualification|days/i);
    facts.push({
      label: "Before & after a hospital stay",
      value: upTo(prePost.value)!,
      note: days ? `Specialist visits and tests within ${days} of your stay` : undefined,
    });
  }
  const emergency = find(items, /emergency|accidental outpatient/i);
  if (emergency && clean(emergency.value)) {
    facts.push({ label: "A&E after an accident", value: upTo(emergency.value)! });
  }
  const govt = find(items, /government (?:restructured )?hospital|restructured hospital/i);
  if (govt && clean(govt.value)) {
    facts.push({ label: "In a government hospital", value: upTo(govt.value)! });
  }
  return facts;
}

/** Major medical sits inside Hospital & surgery as cover that starts after it. */
function majorMedicalFacts(items: BenefitItem[]): CareFact[] {
  const facts: CareFact[] = [];
  const starts = items
    .flatMap((item) => subItemsOf(item))
    .find((sub) => /^(?:from|deductible)$/i.test(sub.name.trim()) && clean(sub.value));
  if (starts) {
    facts.push({
      label: "When it starts",
      value: /ghs|inpatient limits/i.test(starts.value ?? "")
        ? "After your hospital plan's limits are used"
        : `From the ${clean(starts.value)}`,
    });
  }
  const max = find(items, /maximum benefit|overall (?:annual )?limit/i);
  if (max && clean(max.value)) facts.push({ label: "Maximum it pays", value: upTo(max.value)! });
  const share = items.map((item) => prop(item, "co_insurance")).find(Boolean);
  if (share) facts.push({ label: "Your share", value: `${share} of the bill (co-insurance)` });
  return facts;
}

// ── GP (GCGP / GP / GOGP) ────────────────────────────────────────────────────

const CLINIC_LABELS: [RegExp, string][] = [
  [/^non[- ]?panel/i, "Non-panel clinic"],
  [/^panel/i, "Panel clinic"],
  [/polyclinic/i, "Polyclinic"],
  [/a\s*&\s*e/i, "A&E"],
  [/white\s*coat|tele/i, "Video consultation"],
  [/tcm|chinese/i, "TCM"],
  [/overseas/i, "Overseas GP"],
];

function clinicLabel(name: string): string {
  return CLINIC_LABELS.find(([pattern]) => pattern.test(name))?.[1] ?? name;
}

/** One clinic type's cost, in the order a member reads it. */
function clinicCost(item: BenefitItem): CareFact | null {
  const parts: string[] = [];
  const perVisit = prop(item, "per_visit");
  if (perVisit) parts.push(asCharged(perVisit) ? "Covered as charged" : `Up to ${money(perVisit)} a visit`);
  const restructured = prop(item, "per_visit_restructured");
  const privateVisit = prop(item, "per_visit_private");
  if (restructured) {
    parts.push(`Restructured hospital: ${asCharged(restructured) ? "covered as charged" : `up to ${money(restructured)}`}`);
  }
  if (privateVisit) {
    parts.push(`Private hospital: ${asCharged(privateVisit) ? "covered as charged" : `up to ${money(privateVisit)}`}`);
  }
  const copay = prop(item, "co_payment");
  if (copay) parts.push(`you pay ${/%$/.test(copay) ? copay : money(copay)} a visit`);
  if (parts.length === 0) return null;
  const value = parts.join(" · ");
  const yearly = prop(item, "per_policy_year");
  return {
    label: clinicLabel(item.name),
    value: value.charAt(0).toUpperCase() + value.slice(1),
    // A bare number here is a count or an amount depending on the policy, so
    // it is shown as the policy states it rather than guessed into dollars.
    note: yearly ? `Per policy year: ${isBareAmount(yearly) ? yearly : money(yearly)}` : undefined,
  };
}

/** A schedule that states each clinic type as its own row ("Panel
 * consultation: As charged", "Non-panel visit: 80") rather than as a
 * per-visit/co-pay group. */
function clinicRow(item: BenefitItem): CareFact | null {
  const label = CLINIC_LABELS.find(([pattern]) => pattern.test(item.name))?.[1];
  const value = clean(item.value);
  if (!label || !value) return null;
  const perVisit = /per visit/i.test(item.note ?? "");
  return {
    label,
    value: asCharged(value) ? "Covered as charged" : `Up to ${money(value)}${perVisit ? " a visit" : ""}`,
    note: perVisit ? undefined : clean(item.note) ?? undefined,
  };
}

function gpFacts(items: BenefitItem[]): CareFact[] {
  const grouped = items
    .filter((item) => item.kind === "copay" || Object.keys(item.properties ?? {}).some((k) => k.startsWith("per_visit")))
    .map(clinicCost)
    .filter((fact): fact is CareFact => fact !== null);
  const clinics = (grouped.length > 0 ? grouped : items.map(clinicRow).filter((f): f is CareFact => f !== null))
    .sort((a, b) => clinicOrder(a.label) - clinicOrder(b.label));
  const included = items
    .filter((item) => item.kind === "boolean" && /^(yes|y|covered)$/i.test(item.value ?? ""))
    // "Consultation and / or medication" is one service; "Medication requiring
    // prescription / non-retail medication" is a name and its restatement.
    .map((item) => item.name.replace(/\s*(?:and|&)\s*\/\s*or\s*/i, " or ").split(" / ")[0].trim())
    .filter(Boolean);
  const facts = [...clinics];
  if (included.length > 0) {
    facts.push({ label: "What's included", value: included.join(" · ") });
  }
  return facts;
}

function clinicOrder(label: string): number {
  const order = ["Panel clinic", "Polyclinic", "Non-panel clinic", "Video consultation", "A&E", "TCM", "Overseas GP"];
  const index = order.indexOf(label);
  return index < 0 ? order.length : index;
}

// ── Specialist (GCSP / SP / GOSP) ────────────────────────────────────────────

function specialistFacts(items: BenefitItem[]): CareFact[] {
  const facts: CareFact[] = [];
  for (const item of items) {
    const subs = subItemsOf(item).filter((s) => clean(s.value));
    const priced = subs.filter((s) => !/^(shares|separate)/i.test(s.value ?? ""));
    if (!clean(item.value) && priced.length > 0) {
      // "Specialist Care" → its panel / non-panel sub-rows ARE the facts.
      for (const sub of priced) facts.push(subFact(sub));
      continue;
    }
    if (!clean(item.value) || item.kind === "boolean") continue;
    if (/referral|referred/i.test(item.name)) {
      facts.unshift({ label: "Referral", value: clean(item.value)! });
      continue;
    }
    const shared = subs.find((s) => /^(shares|separate)/i.test(s.value ?? ""));
    const count = subs.find((s) => /visits?$/i.test(s.value ?? ""));
    facts.push({
      label: shortName(item.name),
      value: upTo(item.value)!,
      note: [shared?.value, count ? `${count.value} a year` : null].filter(Boolean).join(" · ") || undefined,
    });
  }
  return facts;
}

function subFact(sub: BenefitSubItem): CareFact {
  const label = shortName(sub.name).replace(/^panel specialists?$/i, "Panel specialist")
    .replace(/^non[- ]?panel specialists?$/i, "Non-panel specialist");
  return { label, value: upTo(sub.value)!, note: clean(sub.note) ?? undefined };
}

// ── Dental (GD / DENTAL) ─────────────────────────────────────────────────────

function dentalFacts(line: CoverageLine, items: BenefitItem[]): CareFact[] {
  const facts: CareFact[] = [];
  const yearly = line.annual_policy_limit ?? clean(find(items, /annual (?:policy )?limit|policy limit/i)?.value);
  if (yearly) facts.push({ label: "Your yearly limit", value: money(yearly) ?? yearly });
  const panel = find(items, /^panel dentist/i);
  if (panel && clean(panel.value)) {
    const cashless = asCharged(panel.value!);
    const note = clean(panel.note);
    facts.push({
      label: "Panel dentist",
      value: cashless ? "Cashless — covered as charged" : clean(panel.value)!,
      // The slip's "Cashless for all procedures" heading restates the value.
      note: note && !(cashless && /cashless/i.test(note)) ? note : undefined,
    });
  }
  const nonPanel = find(items, /^non[- ]?panel dentist/i);
  if (nonPanel && clean(nonPanel.value)) {
    facts.push({ label: "Non-panel dentist", value: `You pay, then claim back: ${lowerFirst(clean(nonPanel.value)!)}` });
  }
  const priced = items.filter((item) => item !== panel && item !== nonPanel && isBareAmount(item.value ?? ""));
  if (priced.length > 0) {
    facts.push({
      label: "Treatment price list",
      value: `${priced.length} treatments with a maximum claim amount`,
      note: "See the full list below",
    });
  }
  return facts;
}

const lowerFirst = (text: string) =>
  /^[A-Z][a-z]/.test(text) ? text.charAt(0).toLowerCase() + text.slice(1) : text;

// ── Everything else ──────────────────────────────────────────────────────────

/** Protection, travel, posting: the first stated amounts, in document order. */
function genericFacts(items: BenefitItem[]): CareFact[] {
  return items
    .filter((item) => clean(item.value) && item.kind !== "boolean" && item.kind !== "list" && item.kind !== "scale")
    .map((item) => ({
      label: shortName(item.name),
      value: upTo(item.value) ?? clean(item.value)!,
      note: clean(item.note) ?? undefined,
    }));
}

const MAJOR_MEDICAL = new Set(["GMM", "GMM2"]);

export function careFacts(line: CoverageLine, routeKey: string): CareFact[] {
  const items = (line.benefit_schedule?.items ?? []).filter((item) => /\p{L}/u.test(item.name ?? ""));
  const code = line.product_code.trim().toUpperCase();
  const facts: CareFact[] = [];
  const sumInsured = line.financials?.sum_insured;
  if (sumInsured != null) {
    facts.push({ label: "Amount you're covered for", value: formatValue(String(sumInsured), "currency", S$)! });
  }
  if (routeKey !== "dental" && line.annual_policy_limit) {
    facts.push({ label: "Yearly limit", value: money(line.annual_policy_limit) ?? line.annual_policy_limit, note: "The most this plan pays in one policy year" });
  }
  if (MAJOR_MEDICAL.has(code)) facts.push(...majorMedicalFacts(items));
  else if (routeKey === "hospital") facts.push(...hospitalFacts(items));
  else if (routeKey === "gp") facts.push(...gpFacts(items));
  else if (routeKey === "specialist") facts.push(...specialistFacts(items));
  else if (routeKey === "dental") facts.push(...dentalFacts(line, items));
  else facts.push(...genericFacts(items));
  return facts.slice(0, MAX_FACTS);
}
