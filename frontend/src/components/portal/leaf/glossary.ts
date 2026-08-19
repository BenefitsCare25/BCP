/** The Printed-Label Rule: no product code, benefit key, tier code or family
 * status appears on a member surface without its plain-language gloss set
 * beside it in the same frame. `GCGP` is never shown alone; it is shown as
 * "GCGP — everyday GP and clinic visits".
 *
 * Why this map lives in the frontend, given the house rule that product-type
 * knowledge belongs only in `product_registry.py`: this is not product-type
 * knowledge. The registry serves `code`, `name` and `line`, and none of them
 * answers a member's question — `line` is one of only three values
 * (medical / general / life / flex), and `name` is the insurer's own wording
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

/** Words that carry no meaning for the comparison below — articles, pronouns,
 * and the corporate furniture every group product's name is wrapped in. */
const NOISE = new Set([
  "and", "the", "for", "you", "your", "yours", "with", "from", "that", "this",
  "its", "are", "per", "any", "all", "group", "plan", "into", "onto", "upon",
  "who", "was", "were", "been", "have", "has",
]);

function contentWords(text: string): string[] {
  return text
    .toLowerCase()
    .split(/[^a-z]+/)
    .filter((w) => w.length >= 3 && !NOISE.has(w));
}

/** Two words are "the same word" if they share a four-character stem —
 * `surgery`/`surgical`, `accident`/`accidental`, `hospital`/`hospitalisation`.
 * Four is the shortest prefix that does not start pairing unrelated words
 * (`care`/`cardiac` stops at three, `claim`/`clarity` at three). */
function sameWord(a: string, b: string): boolean {
  if (a === b) return true;
  if (a.length < 4 || b.length < 4) return false;
  let i = 0;
  while (i < a.length && i < b.length && a[i] === b[i]) i++;
  return i >= 4;
}

/** The gloss to print BESIDE a heading — null when it would only repeat it.
 *
 * `productGloss` falls back to the insurer's own product name for any code
 * outside the map above, and every mount that shows a product uses that same
 * name as its heading. So a slip-imported product printed its name twice, once
 * as the title and once as the plain-language line under it, which reads as a
 * rendering fault rather than as a gloss.
 *
 * **An exact-match test was not enough.** "Group Hospital & Surgical" glossed
 * as "hospital stays and surgery" is not the same string and is still the same
 * sentence — the member reads one fact twice. So the test is what the gloss
 * ADDS: if it contributes fewer than two words the title does not already carry,
 * it is an echo rather than a translation and it is dropped.
 *
 * The threshold is two rather than one because English will always supply a
 * connective ("stays") that the title omits, and one such word is not an
 * explanation. It is deliberately generous in the other direction: every gloss
 * that genuinely explains a product survives it — "a lump sum if you're
 * diagnosed with a covered critical illness" adds four words to "Group Critical
 * Illness", "everyday GP and clinic visits" adds three to "Group Comprehensive
 * General Practitioner". A code-only heading (`GHS`, no product name on the
 * statement) shares no words at all, so it always keeps its gloss — which is
 * the case the Printed-Label Rule actually cares about. */
export function glossBeside(
  label: string | null | undefined,
  code: string,
  name?: string | null,
): string | null {
  const gloss = productGloss(code, name);
  if (!gloss || !label) return gloss;

  const labelWords = contentWords(String(label));
  if (labelWords.length === 0) return gloss;
  const added = contentWords(gloss).filter(
    (g) => !labelWords.some((l) => sameWord(g, l)),
  );
  return added.length < 2 ? null : gloss;
}

/** The SHORT form, for a frame too narrow to carry the sentence — today only
 * the coverage deck's index rail, where nine of these sit side by side.
 *
 * It is a second tier of the same copy, not a second vocabulary: every entry
 * here is the headline of the sentence above it ("everyday GP and clinic
 * visits" → "GP visits"), so the rail and the mount it points at can never
 * name the same product two different ways.
 *
 * **The fallback chain is what keeps the Printed-Label Rule intact.** A rail
 * reading `GCGP · GCSP · GHS · GD` is exactly the failure that rule exists to
 * prevent, so an unmapped code falls back to the insurer's own product NAME —
 * words, however long — and only to the bare code when the statement carries no
 * name at all. The chip truncates such a name visually; it is never shortened in
 * the accessibility tree, and the slide directly beneath prints it in full with
 * its gloss. */
const PRODUCT_SHORT: Record<string, string> = {
  GTL: "Life cover",
  GCI: "Critical illness",
  GDD: "Major illness",
  GDI: "Income cover",
  GPA: "Accidents",
  GTPD: "Disability",
  GHS: "Hospital",
  GHS2: "Hospital",
  GMM: "Major medical",
  GMM2: "Major medical",
  IMP: "Overseas medical",
  MATERNITY: "Maternity",
  VISION: "Vision",
  WELLNESS: "Wellness",
  SP: "Specialists",
  GCSP: "Specialists",
  GOSP: "Specialists",
  GCGP: "GP visits",
  GOGP: "GP visits",
  GP: "GP visits",
  GD: "Dental",
  DENTAL: "Dental",
  OSI: "Overseas posting",
  GBT: "Business travel",
  WICA: "Work injury",
};

export function productShortLabel(code: string, name?: string | null): string {
  const known = PRODUCT_SHORT[code.trim().toUpperCase()];
  if (known) return known;
  const trimmed = name?.trim();
  return trimmed && trimmed.length > 0 ? trimmed : code;
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
