/** A stored roster value as editable text, or `undefined` when it is not a
 *  plain value. Insurer member-ID maps, lists and yes/no flags are never edited
 *  as text, so a save cannot flatten them into "[object Object]" or "true". */
export function attrText(value: unknown): string | undefined {
  if (value == null) return "";
  return typeof value === "string" || typeof value === "number"
    ? String(value)
    : undefined;
}

/** The text-editable fields of `values`, minus the `hidden` keys. */
export function editableAttrs(
  values: Record<string, unknown>,
  hidden: ReadonlySet<string>,
): Record<string, string> {
  const out: Record<string, string> = {};
  for (const [key, value] of Object.entries(values)) {
    const text = attrText(value);
    if (!hidden.has(key) && text !== undefined) out[key] = text;
  }
  return out;
}

/** Only the fields whose text differs from `base`, converted for saving. A
 *  field stored as a number stays a number, so rules like `salary > 1600`
 *  still compare numerically; text, including identifiers with leading zeros,
 *  stays text. A cleared field is saved as null. */
export function changedAttrs(
  base: Record<string, unknown>,
  draft: Record<string, string>,
): Record<string, unknown> {
  const out: Record<string, unknown> = {};
  for (const [key, text] of Object.entries(draft)) {
    const before = base[key];
    if (text === attrText(before)) continue;
    const trimmed = text.trim();
    if (trimmed === "") out[key] = null;
    else if (typeof before === "number" && Number.isFinite(Number(trimmed))) {
      out[key] = Number(trimmed);
    } else out[key] = text;
  }
  return out;
}
