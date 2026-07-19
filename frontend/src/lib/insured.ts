/**
 * Reading a category's Insured field.
 *
 * Mirrors `insured_names()` in `backend/app/services/matching_engine.py`.
 * Storage is a list of entity tokens so that an entity whose registered name
 * contains a comma ("Acme Pte Ltd, Singapore Branch") stays ONE entity. Rows
 * written before the token picker are still comma-joined strings, so every
 * read goes through here.
 */
export function insuredNames(raw: unknown): string[] {
  if (!raw) return [];
  const parts = Array.isArray(raw) ? raw : String(raw).split(",");
  return parts.map((p) => String(p).trim()).filter(Boolean);
}

/** Display form — the legal spellings, joined for a badge or summary line. */
export function insuredLabel(raw: unknown): string {
  return insuredNames(raw).join(", ");
}
