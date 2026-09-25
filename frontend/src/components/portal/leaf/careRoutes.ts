import type { CoverageLine } from "@/types";
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
