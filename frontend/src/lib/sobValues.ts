/** What a Schedule-of-Benefits cell MEANS, independent of how it renders.
 *
 * Mirror of `backend/app/services/member_schedule.py` — the backend applies
 * these rules to every member response; the broker editor uses the same ones
 * to show which cells and rows a member will not see. Change both together. */

const NOT_APPLICABLE = new Set([
  "na", "n/a", "n.a", "n. a", "not applicable", "nil", "none", "-", "--", "—", "–",
]);
const NOT_COVERED = new Set(["not covered", "no cover", "not included"]);

const ADMIN_ROW_PATTERNS = [
  /remuneration model/i,
  /\bextension to cover gst\b/i,
  /^\s*surcharges?\s*:?\s*$/i,
];

function fold(value: unknown): string {
  return String(value ?? "")
    .split(/\s+/)
    .filter(Boolean)
    .join(" ")
    .toLowerCase()
    .replace(/[.:]+$/, "")
    .trim();
}

export function isNotApplicable(value: unknown): boolean {
  return NOT_APPLICABLE.has(fold(value));
}

export function isNotCovered(value: unknown): boolean {
  return NOT_COVERED.has(fold(value));
}

/** Blank, "NA"-style or "Not covered": the cell gives a member no cover. */
export function isAbsentValue(value: unknown): boolean {
  if (value == null) return true;
  const folded = fold(value);
  return !folded || NOT_APPLICABLE.has(folded) || NOT_COVERED.has(folded);
}

/** Insurer-arrangement rows a member never needs (hidden unless shown). */
export function isAdminRow(name: unknown): boolean {
  const text = String(name ?? "");
  return ADMIN_ROW_PATTERNS.some((pattern) => pattern.test(text));
}

/** Whether members see this row: the broker's explicit choice wins. */
export function hiddenFromMembers(row: { name?: string | null; member_hidden?: boolean }): boolean {
  if (typeof row.member_hidden === "boolean") return row.member_hidden;
  return isAdminRow(row.name);
}
