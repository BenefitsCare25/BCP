import type {
  FamilyStatusCode,
  FlexSchemeBody,
  FlexTier,
  FlexTierHeadcount,
} from "@/types";

const FAMILY_CODES: FamilyStatusCode[] = ["S", "M", "M1C", "M2C", "M3C"];
const CURRENCY_RE = /^[A-Z]{3}$/;

/** Platform default currency — used when neither a tier nor the scheme sets one.
 *  Mirrors the backend `DEFAULT_CURRENCY` in app/services/flex_membership.py. */
export const DEFAULT_CURRENCY = "SGD";

/** Currencies offered in the scheme/tier dropdowns (APAC-first, then majors). */
export const CURRENCY_OPTIONS: readonly string[] = [
  "SGD", "MYR", "THB", "VND", "IDR", "PHP", "HKD", "CNY", "TWD", "KRW",
  "INR", "JPY", "AUD", "USD", "EUR", "GBP",
];
/** Parse a numeric input: blank → null, non-numeric → null (never NaN). */
export function numOrNull(value: string): number | null {
  if (value.trim() === "") return null;
  const n = Number(value);
  return Number.isFinite(n) ? n : null;
}

/** Format a flex wallet amount with its currency code (null/undefined → "—"). */
export function formatWallet(
  amount: number | null | undefined,
  currency: string | null | undefined,
): string {
  if (amount == null) return "—";
  const num = amount.toLocaleString(undefined, { maximumFractionDigits: 0 });
  return currency ? `${currency} ${num}` : num;
}

function isWholeNonNeg(v: unknown): boolean {
  return typeof v === "number" && Number.isInteger(v) && v >= 0;
}

/**
 * Coerce a (possibly malformed/legacy) scheme bag into a well-shaped body so the
 * form can render without crashing — tiers is always an array, meta an object.
 */
export function normalizeFlexBody(raw: Partial<FlexSchemeBody> | null | undefined): FlexSchemeBody {
  const b = raw ?? {};
  return {
    meta: b.meta && typeof b.meta === "object" ? b.meta : {},
    tiers: Array.isArray(b.tiers) ? b.tiers : [],
    eligibility: b.eligibility ?? null,
    dependant_def: b.dependant_def ?? null,
  };
}

/**
 * Client mirror of the backend `validate_scheme` (app/api/v1/flex_schemes.py).
 * Gives immediate inline feedback; the backend re-validates on confirm as the
 * authority. Returns a list of human-readable errors (empty == valid).
 */
export function validateFlexScheme(body: FlexSchemeBody): string[] {
  const errors: string[] = [];
  const meta = body.meta ?? {};
  const tiers = body.tiers ?? [];

  if (tiers.length === 0) {
    errors.push("Scheme must have at least one eligibility tier.");
  }

  // Currency is per-tier (a scheme can span countries); meta.currency is an
  // optional default validated here, with each tier resolving its own below.
  const defaultCurrency = (meta.currency ?? "").trim().toUpperCase();
  if (defaultCurrency && !CURRENCY_RE.test(defaultCurrency)) {
    errors.push(`Default currency '${defaultCurrency}' is not a 3-letter ISO code.`);
  }

  // Effective period is optional (blank inherits the policy year window), but
  // an entered pair must not be inverted. <input type="date"> guarantees the
  // ISO format, so only ordering is checked here.
  if (
    meta.effective_start &&
    meta.effective_end &&
    meta.effective_end < meta.effective_start
  ) {
    errors.push("Effective start date must be on or before the effective end date.");
  }

  // GST rate is a percentage.
  if (
    meta.gst_rate != null &&
    (typeof meta.gst_rate !== "number" || meta.gst_rate < 0 || meta.gst_rate > 100)
  ) {
    errors.push("GST rate must be a percentage between 0 and 100.");
  }

  tiers.forEach((tier, i) => {
    const label = tier.name?.trim() || `Tier ${i + 1}`;

    // Effective currency always resolves (tier → scheme default → platform SGD),
    // so it's never "required"; only an explicitly-entered value is format-checked.
    const tierCurrency = (tier.currency ?? "").trim().toUpperCase();
    if (tierCurrency && !CURRENCY_RE.test(tierCurrency)) {
      errors.push(`${label}: currency '${tierCurrency}' is not a 3-letter ISO code.`);
    }

    const emp = tier.employee_type ?? {};
    const hasEligibility =
      (emp.match_designations?.length ?? 0) > 0 ||
      (emp.match_grades?.length ?? 0) > 0 ||
      (emp.raw ?? "").trim() !== "" ||
      typeof emp.job_grade_min === "number" ||
      typeof emp.job_grade_max === "number";
    if (!hasEligibility) {
      errors.push(
        `${label}: pick at least one job title or job grade for eligibility.`,
      );
    }
    if (
      typeof emp.job_grade_min === "number" &&
      typeof emp.job_grade_max === "number" &&
      emp.job_grade_min > emp.job_grade_max
    ) {
      errors.push(`${label}: job-grade min is greater than max.`);
    }

    const hasTierCap = typeof tier.system_cap === "number";
    if (typeof tier.system_cap === "number" && tier.system_cap < 0) {
      errors.push(`${label}: flat annual cap must be ≥ 0.`);
    }
    const limits = tier.limits ?? [];
    if (limits.length === 0 && !hasTierCap) {
      errors.push(
        `${label}: needs at least one family-status limit row, or a flat annual cap.`,
      );
    }
    const seen = new Set<string>();
    limits.forEach((row) => {
      if (!FAMILY_CODES.includes(row.family_status)) {
        errors.push(`${label}: invalid family status '${row.family_status}'.`);
      } else if (seen.has(row.family_status)) {
        errors.push(`${label}: duplicate family status '${row.family_status}'.`);
      } else {
        seen.add(row.family_status);
      }
      if (typeof row.amount !== "number" || row.amount < 0) {
        errors.push(`${label}: limit amount for '${row.family_status}' must be ≥ 0.`);
      }
    });

    const cats = tier.benefit_categories ?? [];
    if (cats.length === 0) {
      errors.push(`${label}: needs at least one benefit category.`);
    }
    cats.forEach((cat) => {
      if (!(cat.name ?? "").trim()) {
        errors.push(`${label}: a benefit category is missing a name.`);
      }
      if (typeof cat.claimable !== "boolean") {
        errors.push(
          `${label}: category '${cat.name || "(unnamed)"}' must set claimable true/false.`,
        );
      }
      if (typeof cat.sub_limit === "number" && cat.sub_limit < 0) {
        errors.push(`${label}: a category sub-limit must be ≥ 0.`);
      }
    });
  });

  // Scheme-level dependant age limits (age next-birthday per role). Mirror of the
  // backend meta.dependant_age_limits check; the UI edits max only, min may exist
  // in stored data.
  const depLimits = meta.dependant_age_limits ?? {};
  for (const role of ["spouse", "child"] as const) {
    const win = depLimits[role];
    if (!win) continue;
    for (const [field, val] of [
      ["min", win.min],
      ["max", win.max],
    ] as const) {
      if (val != null && !isWholeNonNeg(val)) {
        errors.push(`Dependant ${role} age ${field} must be a non-negative whole number.`);
      }
    }
    if (typeof win.min === "number" && typeof win.max === "number" && win.min > win.max) {
      errors.push(`Dependant ${role} age min must be ≤ max.`);
    }
  }

  return errors;
}

// ── Tier wallet shape + review status (single source of truth for the UI) ─────

/** How a tier's wallet is defined:
 *  - "family" — per-family-status rows (richest data).
 *  - "flat"   — a single flat annual cap.
 *  - "mixed"  — a flat cap (fallback) PLUS per-family overrides.
 *  - "none"   — neither set: this tier assigns NO wallet (needs fixing). */
export type FlexWalletShape = "family" | "flat" | "mixed" | "none";

export function flexTierWalletShape(tier: FlexTier): FlexWalletShape {
  const hasCap = typeof tier.system_cap === "number";
  const hasLimits = (tier.limits ?? []).length > 0;
  if (hasCap && hasLimits) return "mixed";
  if (hasLimits) return "family";
  if (hasCap) return "flat";
  return "none";
}

export interface FlexTierReview {
  shape: FlexWalletShape;
  /** Wallet/eligibility problems worth a banner. Empty = nothing to flag here. */
  reasons: string[];
  /** Doc-extracted eligibility terms not matched to the roster (own UI block). */
  unresolvedCount: number;
  /** True when the tier needs a broker's attention for any reason. */
  needsReview: boolean;
}

// Mirror of FlexTierEditor's isCovered: a doc token is resolved once a selected
// designation covers it as a whole word.
function tokenCovered(tok: string, desigs: string[]): boolean {
  const t = tok.trim().toLowerCase();
  return desigs.some((d) => {
    const words = d.trim().toLowerCase().split(/\s+/);
    return d.trim().toLowerCase() === t || words.includes(t);
  });
}

/** Assess a tier's data quality: is the wallet set, does it match anyone, and
 *  are there unmapped doc terms? Drives the tab dot + editor review banner. */
export function flexTierReview(
  tier: FlexTier,
  headcount?: FlexTierHeadcount,
): FlexTierReview {
  const shape = flexTierWalletShape(tier);
  const reasons: string[] = [];
  if (shape === "none") reasons.push("No wallet amount set");

  const emp = tier.employee_type ?? {};
  const desigs = emp.match_designations ?? [];
  const grades = emp.match_grades ?? [];
  if (desigs.length === 0 && grades.length === 0) {
    reasons.push("No eligibility (job title / grade) set");
  } else if (headcount && headcount.eligible === 0) {
    reasons.push("Matches no one on the current roster");
  }

  const unresolvedCount = (emp.unresolved ?? []).filter(
    (t) => !tokenCovered(t, desigs),
  ).length;

  return {
    shape,
    reasons,
    unresolvedCount,
    needsReview: reasons.length > 0 || unresolvedCount > 0,
  };
}
