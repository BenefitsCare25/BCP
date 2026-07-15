// Preserve numeric types on save so rules like `salary > 1600` still compare
// numerically — a pure-number string becomes a number, everything else stays text.
export function coerceAttrs(
  attrs: Record<string, string>,
): Record<string, unknown> {
  const out: Record<string, unknown> = {};
  for (const [k, v] of Object.entries(attrs)) {
    const t = v.trim();
    out[k] = t !== "" && /^-?\d+(\.\d+)?$/.test(t) ? Number(t) : v;
  }
  return out;
}
