/**
 * Basis-of-cover amounts and wording ("48 x basic monthly salary").
 *
 * The backend sends a basis as the slip states it. A plain amount ("10000.0",
 * "1,000,000", "S$10,000") is the cover itself and has no wording; anything
 * else — a salary multiple or a relative basis like "50% of GTL" — is how the
 * cover is worked out, and is what the employee portal shows instead of the
 * salary-derived amount.
 */
import { fmtAmount } from "@/lib/format";

// Mirrors backend plan_hydration._PLAIN_AMOUNT so both sides agree on what is
// an amount and what is wording.
const AMOUNT_RE = /^(?:S?\$\s*)?(\d[\d,]*(?:\.\d+)?)$/;
// "48 x basic monthly salary" / "24 times last drawn basic monthly salary"
const MULTIPLE_RE = /^(\d+(?:\.\d+)?)\s*(?:x|×|times)\s+(.+)$/i;

/** The number in a plain amount ("1,000,000", "S$10,000.00"), else null. */
export function parseAmount(raw: unknown): number | null {
  if (typeof raw === "number") return Number.isFinite(raw) ? raw : null;
  if (typeof raw !== "string") return null;
  const match = AMOUNT_RE.exec(raw.trim());
  if (!match) return null;
  const n = Number(match[1].replace(/,/g, ""));
  return Number.isFinite(n) ? n : null;
}

/** Readable basis wording, or null for a blank or plain-amount basis. */
export function basisWording(basis: string | null | undefined): string | null {
  const text = String(basis ?? "").trim().replace(/\s+/g, " ");
  if (!text || parseAmount(text) !== null) return null;
  const multiple = MULTIPLE_RE.exec(text);
  return multiple ? `${multiple[1]} × ${multiple[2]}` : text;
}

/** Basis wording with the policy maximum when one caps it:
 *  "48 × basic monthly salary, up to S$1,600,000". */
export function coverWording(
  basis: string | null | undefined,
  maxSumInsured: number | null | undefined,
): string | null {
  const wording = basisWording(basis);
  if (!wording) return null;
  return maxSumInsured != null && maxSumInsured > 0
    ? `${wording}, up to S$${fmtAmount(maxSumInsured)}`
    : wording;
}

/** Compact form for dense broker tables: "48 × BMS". */
export function basisShort(basis: string | null | undefined): string | null {
  const wording = basisWording(basis);
  return wording?.replace(/(last[- ]drawn )?basic monthly salary/i, "BMS") ?? null;
}

/** True when the basis is a salary multiple ("24 x basic monthly salary"). */
export function isSalaryBasis(basis: string | null | undefined): boolean {
  const wording = basisWording(basis);
  return wording != null && MULTIPLE_RE.test(wording) && /salary/i.test(wording);
}
