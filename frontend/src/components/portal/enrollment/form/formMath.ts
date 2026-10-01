/** The e-form's arithmetic and pre-flight checks, kept out of the JSX.
 *
 * Every check here is ALSO enforced by `POST /portal/enrollment/sign`; these
 * exist so a member learns about a problem on the step that causes it rather
 * than from a refused send. Keep the two in step:
 * `backend/app/services/enrollment_forms/selections.py`.
 *
 * Who is covered follows the server: on VOLUNTARY family cover it is the
 * member's ticks (`ps.dependantIds`); on COMPULSORY family cover it is every
 * active family member inside that product's age window, whatever the deck's
 * state says. */
import type { ProductTierSet } from "@/api/enrollment";
import type {
  FormClause,
  FormDependant,
  FormRuleView,
  ProductContribution,
} from "@/api/enrollmentForms";
import {
  type ProductState,
  dependantParticipationFor,
} from "@/components/enrollment/electionCore";

export type PendingRequests = Record<string, string[]>;

function familyMode(ts: ProductTierSet, ps: ProductState | undefined) {
  if (!ps || ps.declined) return null;
  return dependantParticipationFor(ts, ps.tierKey);
}

/** The family members covered on one product as currently chosen. */
export function coveredOn(
  ts: ProductTierSet,
  ps: ProductState | undefined,
  dependants: FormDependant[],
): FormDependant[] {
  const mode = familyMode(ts, ps);
  if (!ps || mode === null) return [];
  if (mode === "compulsory") {
    return dependants.filter(
      (d) =>
        d.status === "active" &&
        d.role !== null &&
        !d.ineligible_products.includes(ts.product_code),
    );
  }
  return ps.dependantIds
    .map((id) => dependants.find((d) => d.id === id))
    .filter((d): d is FormDependant => !!d);
}

/** The member's annual share of the premium for one product as currently
 * chosen, or null when the broker hasn't set a share for it. */
export function memberShare(
  contribution: ProductContribution | undefined,
  ts: ProductTierSet,
  ps: ProductState | undefined,
  dependants: FormDependant[],
): number | null {
  if (!contribution || !ps || ps.declined) return null;
  const tier = contribution.tiers.find((t) => t.tier_key === ps.tierKey);
  if (!tier) return null;
  const roles = coveredOn(ts, ps, dependants)
    .map((d) => d.role)
    .filter((r): r is "spouse" | "child" => r !== null);
  const spouses = roles.filter((r) => r === "spouse").length;
  const children = roles.length - spouses;
  const parts: (number | null)[] = [tier.employee];
  if (roles.length) {
    if (tier.mode === "tiered") {
      const role = spouses && children ? "both" : spouses ? "spouse" : "child";
      parts.push(tier.family[role] ?? null);
    } else {
      parts.push(tier.per_dependant !== null ? tier.per_dependant * roles.length : null);
    }
  }
  const known = parts.filter((p): p is number => p !== null);
  return known.length ? Math.round(known.reduce((a, b) => a + b, 0) * 100) / 100 : null;
}

export interface TierPrice {
  /** Full annual premium of this plan for the member's family as chosen. */
  premium: number | null;
  /** Against the plan the member holds today (null on that plan itself). */
  change: number | null;
  /** What the member pays a year on this plan. */
  share: number | null;
}

function compositionPremium(
  tier: ProductContribution["tiers"][number] | undefined,
  roles: ("spouse" | "child")[],
): number | null {
  if (!tier) return null;
  const spouses = roles.filter((r) => r === "spouse").length;
  const children = roles.length - spouses;
  if (tier.mode === "flat") {
    const own = tier.premium.EO;
    return own === undefined ? null : own + (tier.premium_per_dependant ?? 0) * roles.length;
  }
  const key = spouses && children ? "EF" : spouses ? "ES" : children ? "EC" : "EO";
  return tier.premium[key] ?? null;
}

/** The figures beside ONE plan option, as the paper form printed beside each
 * plan: its premium, what changes against the held plan, and the member's
 * share — priced for the family members currently ticked. */
export function tierPrice(
  contribution: ProductContribution | undefined,
  ts: ProductTierSet,
  ps: ProductState,
  tierKey: string,
  dependants: FormDependant[],
): TierPrice | null {
  if (!contribution) return null;
  const tier = contribution.tiers.find((t) => t.tier_key === tierKey);
  if (!tier) return null;
  const held = ts.tiers.find((t) => t.is_current) ?? ts.tiers.find((t) => t.is_baseline);
  const asIf = { ...ps, tierKey, declined: false };
  const roles = coveredOn(ts, asIf, dependants)
    .map((d) => d.role)
    .filter((r): r is "spouse" | "child" => r !== null);
  const premium = compositionPremium(tier, roles);
  const heldPremium =
    held && held.key !== tierKey
      ? compositionPremium(
          contribution.tiers.find((t) => t.tier_key === held.key),
          roles,
        )
      : null;
  return {
    premium,
    change:
      premium !== null && heldPremium !== null
        ? Math.round((premium - heldPremium) * 100) / 100
        : null,
    share: memberShare(contribution, ts, asIf, dependants),
  };
}

/** "EMM can only be taken with GHS" — one message per broken rule. */
export function ruleProblems(
  rules: FormRuleView[],
  state: Record<string, ProductState>,
  tierSets: ProductTierSet[],
  dependants: FormDependant[],
): string[] {
  const byCode = new Map(tierSets.map((ts) => [ts.product_code, ts]));
  const out: string[] = [];
  for (const rule of rules) {
    const a = state[rule.product_code];
    const b = state[rule.requires_product_code];
    const aTs = byCode.get(rule.product_code);
    const bTs = byCode.get(rule.requires_product_code);
    const aName = rule.product_name ?? rule.product_code;
    const bName = rule.requires_product_name ?? rule.requires_product_code;
    if (!a || a.declined || !b || !aTs || !bTs) continue;
    if (b.declined) {
      out.push(`${aName} can only be taken together with ${bName}.`);
      continue;
    }
    if (familyMode(aTs, a) === null || familyMode(bTs, b) === null) continue;
    const onB = new Set(coveredOn(bTs, b, dependants).map((d) => d.id));
    const missing = coveredOn(aTs, a, dependants).filter((d) => !onB.has(d.id));
    if (missing.length) {
      out.push(
        `${missing.map((d) => d.name ?? "A family member").join(", ")} must also be covered on ${bName} to be covered on ${aName}.`,
      );
    }
  }
  return out;
}

/** Family members ticked onto a VOLUNTARY family plan whose own age window
 * excludes them. Compulsory family cover drops them by itself. */
export function eligibilityProblems(
  state: Record<string, ProductState>,
  tierSets: ProductTierSet[],
  dependants: FormDependant[],
): string[] {
  const out: string[] = [];
  for (const ts of tierSets) {
    const ps = state[ts.product_code];
    if (familyMode(ts, ps) !== "voluntary") continue;
    for (const dep of coveredOn(ts, ps, dependants)) {
      if (dep.ineligible_products.includes(ts.product_code)) {
        out.push(
          `${dep.name ?? "A family member"} is outside the age limit for ${
            ts.product_name ?? ts.product_code
          } — untick them there.`,
        );
      }
    }
  }
  return out;
}

/** Plans a newly added family member could be requested on: plans the member
 * keeps that take VOLUNTARY family cover. Empty when the period allows no
 * family changes. */
export function familyProducts(
  state: Record<string, ProductState>,
  tierSets: ProductTierSet[],
  allowDeps: boolean,
): ProductTierSet[] {
  if (!allowDeps) return [];
  return tierSets.filter((ts) => familyMode(ts, state[ts.product_code]) === "voluntary");
}

/** Requests narrowed to plans still requestable — a plan declined after a
 * request was ticked must not ride along into the signed form. */
export function prunePending(
  pending: PendingRequests,
  allowed: ProductTierSet[],
  dependants: FormDependant[],
): PendingRequests {
  const codes = new Set(allowed.map((ts) => ts.product_code));
  const out: PendingRequests = {};
  for (const [depId, wanted] of Object.entries(pending)) {
    const dep = dependants.find((d) => d.id === depId);
    if (!dep || dep.status !== "pending") continue;
    const keep = wanted.filter((c) => codes.has(c) && !dep.ineligible_products.includes(c));
    if (keep.length) out[depId] = keep;
  }
  return out;
}

/** Only VOLUNTARY family cover (or a request) brings in the family
 * declarations — the same rule as `selections.names_family`. */
export function namesFamily(
  state: Record<string, ProductState>,
  tierSets: ProductTierSet[],
  pending: PendingRequests,
  dependants: FormDependant[],
): boolean {
  if (Object.values(pending).some((codes) => codes.length > 0)) return true;
  return tierSets.some(
    (ts) =>
      familyMode(ts, state[ts.product_code]) === "voluntary" &&
      coveredOn(ts, state[ts.product_code], dependants).length > 0,
  );
}

export function applicableClauses(clauses: FormClause[], family: boolean): FormClause[] {
  return clauses.filter((c) => c.applies_to === "all" || family);
}
