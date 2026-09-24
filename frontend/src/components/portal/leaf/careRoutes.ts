import type { BenefitItem, CoverageLine } from "@/types";
import { displayProps, formatValue, propertyKind, subItemsOf } from "@/lib/benefitSchedule";
import { propertyLabel } from "@/lib/sob";
import { productShortLabel } from "./glossary";

export type CareRoute = {
  key: string;
  title: string;
  description: string;
  section: "care" | "other";
  lines: CoverageLine[];
};

type RouteProfile = Omit<CareRoute, "lines">;

const PROFILES: RouteProfile[] = [
  { key: "gp", title: "See a GP", description: "Clinic visits and everyday care", section: "care" },
  { key: "specialist", title: "See a specialist", description: "Consultations, tests and referrals", section: "care" },
  { key: "hospital", title: "Hospital & surgery", description: "Room, admission and treatment cover", section: "care" },
  { key: "dental", title: "Dental care", description: "Check-ups and treatment", section: "care" },
  { key: "maternity", title: "Maternity", description: "Pregnancy and childbirth care", section: "care" },
  { key: "vision", title: "Vision", description: "Eye care and optical benefits", section: "care" },
  { key: "wellness", title: "Wellness", description: "Screening and wellbeing", section: "care" },
  { key: "international", title: "Overseas medical", description: "Medical care while abroad", section: "care" },
  { key: "protection", title: "Illness & injury support", description: "Cover for serious illness, disability or accidents", section: "other" },
  { key: "posting", title: "Overseas posting", description: "Cover during an overseas assignment", section: "other" },
  { key: "travel", title: "Business travel", description: "Cover for work trips", section: "other" },
  { key: "work-injury", title: "Work injury help", description: "Support after an injury at work", section: "other" },
];

/** The registry (`product_registry.care_route`) owns the grouping; a product
 * it doesn't route lands under "Other cover" under its own name. */
function routeKey(line: CoverageLine): string {
  return line.care_route ?? `other:${line.product_code.trim().toUpperCase()}`;
}

export function buildCareRoutes(lines: CoverageLine[]): CareRoute[] {
  const groups = new Map<string, CoverageLine[]>();
  for (const line of lines) {
    const key = routeKey(line);
    groups.set(key, [...(groups.get(key) ?? []), line]);
  }
  const known = PROFILES.filter((profile) => groups.has(profile.key)).map((profile) => ({
    ...profile,
    lines: groups.get(profile.key)!,
  }));
  const unknown = [...groups.entries()]
    .filter(([key]) => key.startsWith("other:"))
    .map(([key, group]) => ({
      key,
      title: productShortLabel(group[0].product_code, group[0].product_name),
      description: "Your plan details",
      section: "other" as const,
      lines: group,
    }));
  return [...known, ...unknown];
}

export type CareFact = { label: string; value: string; note?: string };

const CURRENCY = "S$";

type FactSlot = { label: string; match: RegExp };
/** A cap, not any row that merely mentions a year ("Annual health screening"). */
const YEARLY_LIMIT =
  /^(?:annual|yearly|overall)$|\b(?:annual|yearly|overall)\s+(?:limit|maximum|cap)\b|\bper[ -]?(?:policy[ -]?)?year\b/i;
const SLOTS: Record<string, FactSlot[]> = {
  gp: [
    { label: "Panel clinic", match: /^(?:panel(?:\s+(?:gp|clinic|doctor|consultation))?|panel consultation)$/i },
    { label: "Non-panel clinic", match: /non[ -]?panel|non[ -]?preferred/i },
    { label: "Your share", match: /co[ -]?(?:pay(?:ment)?|insurance)|deductible/i },
    { label: "Visit limit", match: /per[ -]?visit|consultation (?:fee|limit)|maximum visits?/i },
    { label: "Yearly limit", match: YEARLY_LIMIT },
  ],
  specialist: [
    { label: "Referral", match: /referral|referred/i },
    { label: "Specialist consultation", match: /specialist (?:care|consultation|visit|fee)/i },
    { label: "Tests & scans", match: /diagnostic|x[ -]?ray|mri|ct scan|laboratory/i },
    { label: "Your share", match: /co[ -]?(?:pay(?:ment)?|insurance)|deductible/i },
  ],
  hospital: [
    { label: "Ward entitlement", match: /ward (?:class|entitlement|type)/i },
    { label: "Room & board", match: /room\s*(?:&|and)?\s*board|daily room/i },
    { label: "Intensive care", match: /\bicu\b|intensive care/i },
    { label: "Surgery & hospital costs", match: /(?:in[ -]?patient|hospital|surgical?) (?:expenses?|benefits?|limit|fees?)/i },
    { label: "Your share", match: /co[ -]?(?:pay(?:ment)?|insurance)|deductible/i },
    { label: "Before or after admission", match: /pre[ -]?hospital|post[ -]?hospital/i },
  ],
  dental: [
    { label: "Panel dentist", match: /^panel(?: dentist| dental)?$/i },
    { label: "Non-panel dentist", match: /non[ -]?panel/i },
    { label: "Yearly limit", match: YEARLY_LIMIT },
    { label: "Treatment", match: /scaling|polishing|extraction|consultation/i },
  ],
};

function readable(item: BenefitItem): string | null {
  const value = formatValue(item.value, item.kind, CURRENCY);
  const note = item.note?.trim();
  if (value) return value;
  if (note) return note;
  return null;
}

/** Suggest concise facts from the matched plan. A missing row never becomes an invented entitlement. */
export function careFacts(line: CoverageLine, routeKey: string): CareFact[] {
  const items = line.benefit_schedule?.items ?? [];
  const candidates = items.flatMap((item) => [
    item,
    ...subItemsOf(item).map((sub) => ({
      ...item,
      name: sub.name,
      value: sub.value,
      note: sub.note,
      kind: sub.kind,
      limits: sub.limits,
    })),
    // Properties carry no kind of their own: the parent's would print a visit
    // count under a currency row as "S$6", so they format by their key, exactly
    // as the full schedule renders them. Keys the parser also mirrors into
    // `limits` are skipped so a fact never repeats its own note.
    ...displayProps(item.properties).map(([key, value]) => ({
      ...item,
      name: propertyLabel(key),
      value,
      kind: propertyKind(key),
      note: `For ${item.name}`,
      limits: [],
      sub_items: [],
      properties: {},
    })),
  ]);
  const facts: CareFact[] = [];
  const sumInsured = line.financials?.sum_insured;
  if (sumInsured != null) {
    facts.push({
      label: "Amount you're covered for",
      value: formatValue(String(sumInsured), "currency", CURRENCY)!,
    });
  }
  if (line.annual_policy_limit) {
    facts.push({
      label: "Yearly cap",
      value: formatValue(line.annual_policy_limit, undefined, CURRENCY) ?? line.annual_policy_limit,
      note: "The most this plan pays in one policy year",
    });
  }
  const slots = SLOTS[routeKey] ?? [];
  const used = new Set<BenefitItem>();
  for (const slot of slots) {
    const item = candidates.find((candidate) =>
      !used.has(candidate) && slot.match.test(candidate.name ?? "") && readable(candidate),
    );
    if (!item) continue;
    used.add(item);
    const value = readable(item)!;
    const notes = [
      item.value ? item.note : null,
      /as charged/i.test(value) ? "Subject to policy terms" : null,
      ...(item.limits ?? []).map((limit) =>
        limit.value ? `${limit.label}: ${limit.value}` : limit.label,
      ),
    ].filter(Boolean).join(" · ");
    facts.push({ label: slot.label, value, note: notes || undefined });
  }
  return facts;
}
