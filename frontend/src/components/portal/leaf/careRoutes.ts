import type { BenefitItem, CoverageLine } from "@/types";
import { formatValue, subItemsOf } from "@/lib/benefitSchedule";
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

const CODE_ROUTE: Record<string, string> = {
  GCGP: "gp", GOGP: "gp", GP: "gp",
  SP: "specialist", GCSP: "specialist", GOSP: "specialist",
  GHS: "hospital", GHS2: "hospital", GMM: "hospital", GMM2: "hospital",
  GD: "dental", DENTAL: "dental",
  MATERNITY: "maternity", VISION: "vision", WELLNESS: "wellness", IMP: "international",
  GCI: "protection", GDD: "protection", GDI: "protection", GPA: "protection", GTPD: "protection",
  OSI: "posting", GBT: "travel", WICA: "work-injury", WICI: "work-injury",
};

export function isMemberVisible(line: CoverageLine): boolean {
  return line.product_code.trim().toUpperCase() !== "GTL";
}

function routeKey(line: CoverageLine): string {
  const code = line.product_code.trim().toUpperCase();
  if (code.startsWith("GHS-")) return "hospital";
  return CODE_ROUTE[code] ?? `other:${code}`;
}

export function buildCareRoutes(lines: CoverageLine[]): CareRoute[] {
  const groups = new Map<string, CoverageLine[]>();
  for (const line of lines.filter(isMemberVisible)) {
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

type FactSlot = { label: string; match: RegExp };
const SLOTS: Record<string, FactSlot[]> = {
  gp: [
    { label: "Panel clinic", match: /^(?:panel(?:\s+(?:gp|clinic|doctor|consultation))?|panel consultation)$/i },
    { label: "Non-panel clinic", match: /non[ -]?panel|non[ -]?preferred/i },
    { label: "Your share", match: /co[ -]?(?:pay(?:ment)?|insurance)|deductible/i },
    { label: "Visit limit", match: /per[ -]?visit|consultation (?:fee|limit)|maximum visits?/i },
    { label: "Yearly limit", match: /annual|yearly|per[ -]?policy[ -]?year/i },
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
    { label: "Yearly limit", match: /annual|yearly|per[ -]?year/i },
    { label: "Treatment", match: /scaling|polishing|extraction|consultation/i },
  ],
};

function readable(item: BenefitItem): string | null {
  const value = formatValue(item.value, item.kind, "S$");
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
    ...Object.entries(item.properties ?? {}).map(([key, value]) => ({
      ...item,
      name: propertyLabel(key),
      value,
      note: `For ${item.name}`,
      limits: [],
    })),
  ]);
  const slots = SLOTS[routeKey];
  if (!slots) return [];
  const used = new Set<BenefitItem>();
  const facts: CareFact[] = [];
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
