/** Each care route owns one colour pair and, where one exists, one clay object.
 *
 * Single-sourced so a benefit reads as the same thing everywhere it appears —
 * the Home sheet, the Coverage card and the claim form all resolve GP to lime
 * and the stethoscope. Tones are identity, never status: claim states keep the
 * strike colours, which no tone here reuses. */

export type CareTone = "lime" | "blue" | "peach" | "pink" | "lilac" | "mint";

const TONES: Record<string, CareTone> = {
  gp: "lime",
  specialist: "blue",
  hospital: "peach",
  dental: "pink",
  maternity: "pink",
  vision: "blue",
  wellness: "mint",
  international: "lilac",
  protection: "lilac",
  posting: "mint",
  travel: "mint",
  "work-injury": "peach",
};

const ART: Record<string, string> = {
  gp: "gp",
  specialist: "specialist",
  hospital: "hospital",
  dental: "dental",
};


export function careTone(routeKey: string): CareTone {
  return TONES[routeKey] ?? "lilac";
}

/** Public path of the route's clay object, or null when none was made for it. */
export function careArt(routeKey: string): string | null {
  const name = ART[routeKey];
  return name ? `/portal/clay/${name}.webp` : null;
}

/** Clay objects for the portal's own sections (not care routes). */
export const sectionArt = {
  card: "/portal/clay/card.webp",
  claim: "/portal/clay/claim.webp",
  family: "/portal/clay/family.webp",
  clinic: "/portal/clay/clinic.webp",
  messages: "/portal/clay/messages.webp",
  security: "/portal/clay/security.webp",
  enrol: "/portal/clay/enrol.webp",
} as const;

/** Where a surface only has a PRODUCT code (utilisation rows carry no care
 *  route), the route it belongs to — so "What's left" colours a balance the
 *  same way Home colours that benefit. Unknown codes fall to lilac. */
const PRODUCT_ROUTE: Record<string, string> = {
  GHS: "hospital", GHS2: "hospital", GMM: "hospital", GMM2: "hospital",
  GCGP: "gp", GP: "gp", GOGP: "gp",
  GCSP: "specialist", SP: "specialist", GOSP: "specialist",
  GD: "dental", GDI: "dental",
};

export function productRouteKey(code: string | null | undefined): string {
  return PRODUCT_ROUTE[(code ?? "").trim().toUpperCase()] ?? "protection";
}
