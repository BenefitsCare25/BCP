import type { FlexPricingTier } from "@/api/enrollment";

/** The single implicit age band a plan-type product stores its one price under.
 *  Shared with `FlexPricingCard`'s `ALL_AGES` so the scalar lookup and the editor
 *  agree on which band holds "the plan price". */
export const ALL_AGES_LABEL = "All ages";

/** A display group of one or more tiers that share a plan.
 *
 *  For `plan_type` products a plan is priced ONCE across every job-category cohort
 *  (e.g. GCGP prices Plan 1 at the same rate for all four job categories), yet the
 *  backend keeps one tier per cohort so a broker CAN override per cohort. Grouping
 *  by `plan_code` lets the UI fold those identical cohort tiers into a single row
 *  whose edits fan out to every member key — so the grid stops showing "Plan 1"
 *  four times, and manual mode can't leave one cohort silently unpriced.
 *
 *  Grouping is structural only; whether a multi-tier group actually collapses is a
 *  per-table decision (see {@link planRows}) so genuinely divergent prices under one
 *  plan are never silently merged. */
export interface PlanTierGroup {
  /** Every member tier key sharing this plan — the fan-out targets. */
  keys: string[];
  /** Representative tier for the label / direction / baseline / slip preview. */
  rep: FlexPricingTier;
  /** The tiers in this group, in original order (for the divergence fallback). */
  tiers: FlexPricingTier[];
}

/** One rendered row: a folded plan (many keys, no cohort label) or a single split
 *  tier (one key, labelled by its cohort so identical-plan rows are distinguishable). */
export interface PlanRow {
  keys: string[];
  rep: FlexPricingTier;
  /** Cohort (job-category) label to disambiguate a split row; null when folded. */
  cohortLabel: string | null;
}

/** Group a product's tiers by plan for display.
 *
 *  Tiers sharing a non-blank `plan_code` fold into one group; a blank plan code has
 *  no shared identity, so each such tier stays on its own. This is mode-independent:
 *  genuinely-distinct life tiers carry distinct plan codes (so they never fold), and
 *  whether a same-plan group actually collapses is still gated per-table by
 *  {@link planRows}'s value check — so an age-banded product folds its repeated
 *  cohorts exactly like a per-plan one. */
export function groupTiersByPlan(tiers: FlexPricingTier[]): PlanTierGroup[] {
  const order: string[] = [];
  const byPlan = new Map<string, FlexPricingTier[]>();
  for (const t of tiers) {
    const gk = t.plan_code ? `plan:${t.plan_code}` : `key:${t.key}`;
    if (!byPlan.has(gk)) {
      byPlan.set(gk, []);
      order.push(gk);
    }
    byPlan.get(gk)!.push(t);
  }
  return order.map((gk) => {
    const group = byPlan.get(gk)!;
    // Prefer the baseline tier as representative so the "default plan" highlight
    // survives the fold (every cohort's compulsory tier is a baseline).
    const rep = group.find((t) => t.is_baseline) ?? group[0];
    return { keys: group.map((t) => t.key), rep, tiers: group };
  });
}

/** Rendered rows for a product's tiers, given how THIS table reads a tier's value.
 *  A plan whose cohorts agree on `valueOf` folds to one row (edits fan out to every
 *  key); a plan whose cohorts disagree stays split, each row carrying its cohort
 *  label so the otherwise-identical plan rows can be told apart. */
export function planRows(
  tiers: FlexPricingTier[],
  valueOf: (t: FlexPricingTier) => unknown,
): PlanRow[] {
  return groupTiersByPlan(tiers).flatMap((g) => {
    if (g.keys.length === 1 || allEqual(g.tiers, valueOf)) {
      return [{ keys: g.keys, rep: g.rep, cohortLabel: null }];
    }
    return g.tiers.map((t) => ({
      keys: [t.key],
      rep: t,
      cohortLabel: t.cohort_label ?? null,
    }));
  });
}

/** The single scalar price of a plan-type tier: the ALL_AGES band value, falling
 *  back to any numeric so a not-yet-re-saved age-banded value isn't hidden. Shared
 *  by the editor row and the collapsed chip preview so both fold identically. */
export function planScalar(
  priceTags: Record<string, Record<string, number | null>> | undefined,
  key: string,
): number | null {
  const row = priceTags?.[key];
  if (!row) return null;
  if (row[ALL_AGES_LABEL] != null) return row[ALL_AGES_LABEL] as number;
  const vals = Object.values(row).filter((v): v is number => typeof v === "number");
  return vals.length ? vals[0] : null;
}

/** Deterministic JSON with object keys sorted, so `{a,b}` and `{b,a}` serialise
 *  identically — {@link allEqual} must not split structurally-equal rows just
 *  because their keys were inserted in a different order. */
function stableStringify(value: unknown): string {
  return JSON.stringify(value, (_key, val) =>
    val && typeof val === "object" && !Array.isArray(val)
      ? Object.fromEntries(
          Object.keys(val as Record<string, unknown>)
            .sort()
            .map((k) => [k, (val as Record<string, unknown>)[k]]),
        )
      : val,
  );
}

/** True when every item maps to the same value — the collapse guard. A group whose
 *  members disagree on price must render per-cohort so nothing is silently merged.
 *  Compares by order-independent structural equality; a group of ≤1 always agrees. */
export function allEqual<T>(items: T[], valueOf: (item: T) => unknown): boolean {
  if (items.length <= 1) return true;
  const first = stableStringify(valueOf(items[0]));
  return items.every((it) => stableStringify(valueOf(it)) === first);
}
