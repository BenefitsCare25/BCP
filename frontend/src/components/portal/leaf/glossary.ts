/** The Printed-Label Rule: no product code, benefit key, tier code or family
 * status appears on a member surface without its plain-language gloss set
 * beside it in the same frame. `GCGP` is never shown alone; it is shown as
 * "GCGP — everyday GP and clinic visits".
 *
 * Why this map lives in the frontend, given the house rule that product-type
 * knowledge belongs only in `product_registry.py`: this is not product-type
 * knowledge. The registry serves `code`, `name` and `line`, and none of them
 * answers a member's question — `line` is one of only three values
 * (medical / life / flex), and `name` is the insurer's own wording
 * ("Group Clinical General Practitioner"). What follows is member-facing COPY,
 * which is a design artifact and belongs with the surface that speaks it.
 *
 * The fallback is what keeps that safe: an unknown code falls back to the
 * product's own name from the statement, never to a bare code. Adding a
 * product to the registry can therefore never produce an unglossed label here
 * — it produces the insurer's wording until someone writes a better line. */

const PRODUCT_GLOSS: Record<string, string> = {
  GTL: "a lump sum for your family if you die",
  GCI: "a lump sum if you're diagnosed with a covered critical illness",
  GDD: "a lump sum if you're diagnosed with a covered major illness",
  GDI: "replaces part of your income if illness or injury stops you working",
  GPA: "a lump sum for accidental injury or death",
  GTPD: "a lump sum if you become permanently unable to work",
  GHS: "hospital stays and surgery",
  GHS2: "hospital stays and surgery",
  GMM: "hospital and surgery costs above your main hospital limit",
  GMM2: "hospital and surgery costs above your main hospital limit",
  IMP: "hospital and medical care while you're overseas",
  MATERNITY: "pregnancy and childbirth costs",
  VISION: "eye tests, glasses and lenses",
  WELLNESS: "health screening and wellbeing costs",
  SP: "specialist visits, on referral from a GP",
  GCSP: "specialist visits, on referral from a GP",
  GOSP: "specialist visits, on referral from a GP",
  GCGP: "everyday GP and clinic visits",
  GOGP: "everyday GP and clinic visits",
  GP: "everyday GP and clinic visits",
  GD: "dental check-ups and treatment",
  DENTAL: "dental check-ups and treatment",
  OSI: "cover while you're posted overseas",
  GBT: "cover while you're travelling for work",
  WICA: "cover if you're injured at work",
};

/** Plain-language gloss for a product. Falls back to the insurer's own product
 * name so a code never appears alone; returns null only when we have neither,
 * which callers must render as no gloss rather than as an empty dash. */
export function productGloss(
  code: string,
  name?: string | null,
): string | null {
  const known = PRODUCT_GLOSS[code.trim().toUpperCase()];
  if (known) return known;
  const trimmed = name?.trim();
  if (trimmed && trimmed.toUpperCase() !== code.trim().toUpperCase()) {
    return trimmed;
  }
  return null;
}

/** The gloss to print BESIDE a heading — null when it would only repeat it.
 *
 * `productGloss` falls back to the insurer's own product name for any code
 * outside the map above, and every mount that shows a product uses that same
 * name as its heading. So a slip-imported product printed its name twice, once
 * as the title and once as the plain-language line under it, which reads as a
 * rendering fault rather than as a gloss. */
export function glossBeside(
  label: string | null | undefined,
  code: string,
  name?: string | null,
): string | null {
  const gloss = productGloss(code, name);
  if (!gloss || !label) return gloss;
  return gloss.trim().toLowerCase() === String(label).trim().toLowerCase()
    ? null
    : gloss;
}

/** Family-status and tier codes carried on the flex scheme. Same rule: the
 * member sees words, not the scheme's internal token. */
const FAMILY_GLOSS: Record<string, string> = {
  EO: "you only",
  ES: "you and your spouse",
  EC: "you and your children",
  EF: "you and your family",
  SO: "your spouse only",
  CO: "your children only",
  FO: "your family only",
  SC: "your spouse and children",
};

export function familyGloss(code: string | null | undefined): string | null {
  if (!code) return null;
  return FAMILY_GLOSS[code.trim().toUpperCase()] ?? null;
}
