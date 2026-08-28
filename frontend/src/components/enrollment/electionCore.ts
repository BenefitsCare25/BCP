/** Election LOGIC — shared by the broker elections page
 * (`routes/enrollment/elections.tsx`) and the member portal
 * (`components/portal/enrollment/`, also rendered read-only in the broker's
 * employee-view preview).
 *
 * **This module, not a shared component tree, is what guarantees the member
 * sees exactly the tiers, directions and prices a broker would elect on their
 * behalf.** Tier resolution, dependant pricing, the flex arithmetic, the leave
 * bounds and the PUT payload are one implementation with no surface flag in
 * them, so the two pages cannot disagree about the money or about what gets
 * saved.
 *
 * The two surfaces' PRESENTATION deliberately does not live here. It used to:
 * one component tree branching on `memberLabels` and `useInLeaf()`, which is
 * where four recorded defects came from — including 44px member controls
 * leaking onto the broker's 36px page, because one of the two branches was
 * added in one component and forgotten in another three hundred lines away.
 * A pure module has no such branch to forget. */
import type {
  CohortTier,
  ElectionIn,
  EnrollmentDetail,
  EnrollmentOptions,
  MemberLeaveOptions,
  ProductTierSet,
} from "@/api/enrollment";
import type { FlexProrationLine } from "@/types";
import { fmtAmount } from "@/lib/format";

/** The minimal dependant shape the election UI needs (statement dependants and
 * portal dependants both satisfy it). */
export interface DependantRef {
  id: string;
  name?: string | null;
  relationship?: string | null;
  /** "spouse" | "child" | null, classified by the SERVER (`DependantSummary.
   *  role`). Optional only because a caller may pass a hand-built stub. */
  role?: string | null;
}

export interface ProductState {
  productCode: string;
  tierKey: string; // "" when declined; else the elected tier's unique key
  declined: boolean;
  dependantIds: string[];
  /** Elected freestanding dependant option LEVEL per role ({role: category_id}) —
   *  only for products whose slip lists multiple unlinked levels (option_choices). */
  depOptionIds: Record<string, string>;
}

/** Exact money with the sign OUTSIDE the currency symbol. Interpolating
 * `$${fmtAmount(v)}` renders a credit as "$-120"; money reads "-$120".
 *
 * **BROKER surface only.** The member portal formats money through
 * `components/portal/leaf/Figure.tsx` (`Money` / `moneyText`), which applies
 * DESIGN.md's Tabular-Figure Rule and the scheme's own currency symbol. This
 * used to take a `symbol` parameter for member callers; nothing passes one any
 * more, and keeping it invited a second, subtly different member-facing
 * formatter beside `moneyText` — which is how the recorded `$`/`S$` split
 * happened. Bare `$` is the broker page's convention.
 *
 * **It is `fmtAmount`, not `fmtCurrency`, and that is deliberate rather than
 * incidental.** `fmtCurrency` compacts at a thousand, so a broker reconciling a
 * wallet read "$2.7K" for a figure they have to tie back to a price tag —
 * `fmtAmount` exists for exactly this ("editable / exact figures … where the
 * compact $1.2M form would lose precision"). What did NOT survive that swap,
 * and is restored here, is `fmtCurrency`'s sub-cent guard: a residual −0.004
 * balance rendered as "-$0", which reads as settled while `submitBlocked` is
 * still firing on it. */
export function signedMoney(v: number): string {
  const sign = v < 0 ? "-" : "";
  const a = Math.abs(v);
  if (a > 0 && a < 0.01) return `${sign}$${a.toExponential(2)}`;
  return `${sign}$${fmtAmount(a)}`;
}

/** The dependant's pricing role, as classified by the SERVER.
 *
 * **Read, never re-derived.** This used to mirror the backend's spouse/child
 * word lists, and the mirror had already drifted: it carried a bare `"step"`
 * that `flex_membership._CHILD_WORDS` deliberately omits (with a comment saying
 * why — it would read "stepmother" as a child). A step-parent therefore priced
 * as a child here and as unpriceable there, so the member was quoted a
 * confident child rate and then refused at submit with `unpriced_elections` —
 * exactly the failure `dependantPricing`'s `unresolved` flag exists to prevent.
 *
 * The repo's rule for a join key the server already owns is served-never-
 * mirrored (see the claim-type key in CLAUDE.md); this is the same case. */
export function classifyRel(dep?: DependantRef | null): "spouse" | "child" | null {
  const role = dep?.role;
  return role === "spouse" || role === "child" ? role : null;
}

/** What covering the selected dependants draws from the wallet, and whether we
 * actually KNOW it.
 *
 * `unresolved` matters because $0 is returned for two entirely different
 * situations: cover that genuinely costs nothing, and a price we cannot work
 * out yet (no tier row, a freestanding option level not yet elected, an
 * age-banded amount the server hasn't resolved). Presenting the second as the
 * first tells a member their family is free and then 409s `unpriced_elections`
 * at them on submit. */
export function dependantPricing(
  dep: ProductTierSet["dependant"],
  tierKey: string,
  selectedIds: string[],
  deps: DependantRef[],
  depOptionIds: Record<string, string> = {},
): { total: number; unresolved: boolean } {
  const known = (total: number) => ({ total, unresolved: false });
  const unknown = { total: 0, unresolved: true };
  if (!dep || !selectedIds.length) return known(0);
  const tp = dep.by_tier[tierKey];
  if (!tp) return unknown;
  const mode = tp.mode ?? dep.mode;
  if (mode === "none") return known(0);
  if (mode === "per_pax") {
    return tp.per_pax_rate == null
      ? unknown
      : known(tp.per_pax_rate * selectedIds.length);
  }
  let spouse = 0;
  let child = 0;
  for (const id of selectedIds) {
    const kind = classifyRel(deps.find((x) => x.id === id));
    if (kind === "spouse") spouse += 1;
    else if (kind === "child") child += 1;
  }
  if (mode === "slip_options") {
    // Slip dependant option rows stick to the elected employee plan: each
    // covered dependant draws that option's amount. Freestanding option LEVELS
    // (option_choices) instead price from the level elected per role — using
    // the server's per-dependant resolved amounts so age-banded levels show
    // each dependant's own band. Anything unresolvable counts as $0 live (the
    // server marks the tag unpriced at save rather than guessing).
    const amountFor = (role: "spouse" | "child") =>
      tp.family.find((f) => f.role === role)?.amount ?? null;
    let total = 0;
    let unresolved = false;
    for (const id of selectedIds) {
      const role = classifyRel(deps.find((x) => x.id === id));
      // **An unclassifiable relationship is UNKNOWN, not free.** `classifyRel`
      // only recognises spouse- and child-words, so a parent, a sibling or a
      // blank relationship falls through — and skipping it silently left
      // `unresolved` false, which is precisely the assertion this flag exists
      // to prevent: the member is told "covering them draws nothing from your
      // flex wallet" about cover that cannot be priced at all, and the server
      // then 409s `unpriced_elections` at them on submit.
      if (!role) {
        unresolved = true;
        continue;
      }
      const linked = amountFor(role);
      if (linked != null) {
        total += linked;
        continue;
      }
      const chosen = depOptionIds[role];
      const choice = dep.option_choices
        ?.find((r) => r.role === role)
        ?.choices.find((c) => c.category_id === chosen);
      const amount = choice?.amounts_by_dependant?.[id] ?? choice?.amount ?? null;
      // Unresolvable counts as $0 in the TOTAL — the server marks the tag
      // unpriced at save rather than guessing — but it is reported as unknown
      // so no surface can call it free.
      if (amount == null) unresolved = true;
      else total += amount;
    }
    return { total, unresolved };
  }
  const role = spouse && child ? "both" : spouse ? "spouse" : child ? "child" : null;
  // Same rule as the slip_options branch above: we are past the early return,
  // so dependants ARE selected — a null role means none of them classified, and
  // that is a price we cannot work out rather than a price of nothing.
  if (!role) return unknown;
  const amount = tp.family.find((f) => f.role === role)?.amount;
  return amount == null ? unknown : known(amount);
}

/** Baseline-only state, for a surface where no enrollment row exists yet (the
 * broker's employee-view preview). */
export function baselineElectionState(
  tierSets: ProductTierSet[],
): Record<string, ProductState> {
  const next: Record<string, ProductState> = {};
  for (const ts of tierSets) {
    const baseline = ts.tiers.find((t) => t.is_baseline) ?? ts.tiers[0];
    next[ts.product_code] = {
      productCode: ts.product_code,
      tierKey: baseline?.key ?? "",
      declined: false,
      dependantIds: [],
      depOptionIds: {},
    };
  }
  return next;
}

/** Seed the per-product election state from the saved election → baseline
 * snapshot → cohort baseline. Resolves the current tier by its
 * (tier_category_id, plan_code) pair — a category or a plan can each repeat
 * across tiers, so match on both. */
export function seedElectionState(
  enr: EnrollmentDetail,
  tierSets: ProductTierSet[],
): Record<string, ProductState> {
  const baseProducts = enr.baseline_snapshot?.products ?? {};
  const next: Record<string, ProductState> = {};
  for (const ts of tierSets) {
    const code = ts.product_code;
    const el = enr.elections.find((e) => e.product_code === code);
    const base = baseProducts[code];
    const declined = el ? el.action === "decline" : base?.declined ?? false;
    const wantTierId =
      el?.tier_category_id ?? base?.tier_category_id ?? ts.baseline_tier_category_id;
    const wantPlan = el?.elected_plan_code ?? base?.plan_code ?? ts.baseline_plan_code;
    const current =
      ts.tiers.find((t) => t.tier_category_id === wantTierId && t.plan_code === wantPlan) ??
      ts.tiers.find((t) => t.is_baseline) ??
      ts.tiers[0];
    next[code] = {
      productCode: code,
      tierKey: current?.key ?? "",
      declined,
      dependantIds: el?.covered_dependant_ids ?? base?.covered_dependant_ids ?? [],
      depOptionIds: el?.dependant_option_ids ?? base?.dependant_option_ids ?? {},
    };
  }
  return next;
}

/** What the member HOLDS today, in the same shape as their working state — the
 * "before" side of every change the enrollment surface reports.
 *
 * Deliberately NOT `seedElectionState`. That one resolves the state to EDIT,
 * which is the saved election when one exists; a member who elected an upgrade
 * yesterday and reloads would then be told they are changing nothing, while the
 * enrollment they are about to send does change their cover. This resolves the
 * standing coverage instead: the tier flagged `is_current` (the server is the
 * only side that can read a standing override — see `CohortTier.is_current`),
 * and the window's own opening snapshot for everything else.
 *
 * `baseline_snapshot` is the right source for the dependant sets for the same
 * reason: it is coverage as it stood when the window opened, whereas
 * `enrollment.elections` is the member's unconfirmed intent. */
export function heldElectionState(
  enr: EnrollmentDetail | null,
  tierSets: ProductTierSet[],
): Record<string, ProductState> {
  const baseProducts = enr?.baseline_snapshot?.products ?? {};
  const next: Record<string, ProductState> = {};
  for (const ts of tierSets) {
    const base = baseProducts[ts.product_code];
    const held =
      ts.tiers.find((t) => t.is_current) ??
      // A payload predating `is_current` still resolves through the snapshot's
      // own (category, plan) pair before falling back to the cohort baseline.
      (base
        ? ts.tiers.find(
            (t) =>
              t.tier_category_id === base.tier_category_id &&
              t.plan_code === base.plan_code,
          )
        : undefined) ??
      ts.tiers.find((t) => t.is_baseline) ??
      ts.tiers[0];
    next[ts.product_code] = {
      productCode: ts.product_code,
      tierKey: held?.key ?? "",
      declined: base?.declined ?? false,
      dependantIds: base?.covered_dependant_ids ?? [],
      depOptionIds: base?.dependant_option_ids ?? {},
    };
  }
  return next;
}

/** Do two election states describe the same cover?
 *
 * Dependants are compared as SETS — the tick list appends, so covering A then B
 * and covering B then A are the same election and must not be reported as a
 * change. A declined product compares on nothing else: which tier its radio
 * happens to be parked on is not part of the outcome.
 *
 * **`ignoreDependants` is for cover the member cannot elect**, and it exists
 * because the two sides record it differently. `buildElectionsPayload` persists
 * EVERY dependant on a product whose family cover is compulsory, while
 * `baseline_for` stores `covered_dependant_ids: None` for a member with no
 * override — so the first save made every such product differ from what the
 * member holds, and it was then marked "Changed" in the rail and printed
 * "Adding Jane" in the review under two identical plan names. Nothing about
 * that set was ever the member's choice. */
export function sameElection(
  a?: ProductState,
  b?: ProductState,
  opts?: {
    /** Compatibility shorthand: ignore covered people and selected levels. */
    ignoreDependants?: boolean;
    /** Ignore the covered-person set while still comparing selected levels. */
    ignoreDependantIds?: boolean;
    /** Ignore selected dependant cover levels. */
    ignoreDependantOptions?: boolean;
  },
): boolean {
  if (!a || !b) return a === b;
  if (a.declined !== b.declined) return false;
  if (a.declined) return true;
  if (a.tierKey !== b.tierKey) return false;
  if (opts?.ignoreDependants) return true;
  if (!opts?.ignoreDependantIds) {
    if (a.dependantIds.length !== b.dependantIds.length) return false;
    const ids = new Set(b.dependantIds);
    if (a.dependantIds.some((id) => !ids.has(id))) return false;
  }
  if (opts?.ignoreDependantOptions) return true;
  const keys = new Set([
    ...Object.keys(a.depOptionIds),
    ...Object.keys(b.depOptionIds),
  ]);
  for (const k of keys) {
    if ((a.depOptionIds[k] ?? "") !== (b.depOptionIds[k] ?? "")) return false;
  }
  return true;
}

/** Build the PUT /elections payload from local state — identical rules on both
 * surfaces: compulsory dependant cover persists ALL dependants; elected option
 * levels only for roles the product actually offers. */
export function buildElectionsPayload(
  state: Record<string, ProductState>,
  tierSets: ProductTierSet[],
  dependants: DependantRef[],
  allowDeps: boolean,
): ElectionIn[] {
  const setByCode = new Map(tierSets.map((ts) => [ts.product_code, ts]));
  return Object.values(state).map((s) => {
    const ts = setByCode.get(s.productCode);
    const tier = ts?.tiers.find((t) => t.key === s.tierKey);
    const depCompulsory = ts?.dependant_participation === "compulsory";
    const coveredDeps = !allowDeps
      ? null
      : depCompulsory
        ? dependants.map((d) => d.id)
        : s.dependantIds;
    const offeredRoles = new Set<string>(
      (ts?.dependant?.option_choices ?? []).map((r) => r.role),
    );
    const depOptions = Object.fromEntries(
      Object.entries(s.depOptionIds).filter(([role]) => offeredRoles.has(role)),
    );
    return {
      product_code: s.productCode,
      declined: s.declined,
      // Send both — the (tier_category_id, plan_code) pair uniquely identifies
      // the tier even when a category or plan_code is shared across tiers.
      plan_code: s.declined ? null : tier?.plan_code ?? null,
      tier_category_id: s.declined ? null : tier?.tier_category_id ?? null,
      covered_dependant_ids: coveredDeps,
      dependant_option_ids:
        s.declined || !Object.keys(depOptions).length ? null : depOptions,
    };
  });
}

export interface FlexSummary {
  wallet: number;
  /** How `wallet` was scaled to the member's cover period, when it was. SERVED,
   * never recomputed here — a fraction that drifts from the figure beside it is
   * silent, and the month count has no exact JS equivalent worth maintaining
   * twice. Null for anyone covered the whole period. */
  proration: FlexProrationLine | null;
  currency: string | null;
  total: number;
  leaveImpact: number;
  balance: number;
  onChange: boolean;
  /** At least one covered dependant has no price yet, so `total` and `balance`
   * are LOWER BOUNDS. Without this the strip deducted the dependants it could
   * price and stayed silent about the rest — so it showed a confident
   * "S$600 spent / S$2,100 left" while the card immediately below said "we'll
   * show what covering them costs once the level above is chosen". Two figures
   * on one screen, disagreeing about whether the price is known. */
  incomplete: boolean;
}

/** Below this a negative balance is a rounding residue, not a shortfall.
 *
 * Single-sourced because the surfaces that read it sit next to each other: the
 * running balance in the enrollment rail, the "Short by" row in the wallet, and
 * the gate on the send button. At a residual −0.004 a bare `< 0` prints
 * "Short by S$0" — a settled-looking figure beside a refused submission — while
 * the gate is still firing. */
export const FLEX_EPSILON = 0.005;

export function flexShort(flex: FlexSummary | null | undefined): boolean {
  return !!flex && flex.balance < -FLEX_EPSILON;
}

/** Running flex balance: wallet − Σ coverage price tags + live buy/sell-leave
 * impact (buy spends, sell credits) at the member's per-day leave rate. */
export function computeFlex(
  options: EnrollmentOptions | undefined,
  tierSets: ProductTierSet[],
  state: Record<string, ProductState>,
  dependants: DependantRef[],
  allowDeps: boolean,
  leaveAction: string,
  leaveDays: string,
  leave?: MemberLeaveOptions | null,
): FlexSummary | null {
  if (!options || options.flex_wallet == null) return null;
  let total = 0;
  let incomplete = false;
  for (const ts of tierSets) {
    const ps = state[ts.product_code];
    if (!ps || ps.declined) continue;
    const tier = ts.tiers.find((t) => t.key === ps.tierKey);
    if (tier?.price_tag) total += tier.price_tag;
    if (allowDeps) {
      const dependantIds =
        ts.dependant_participation === "compulsory"
          ? dependants.map((dependant) => dependant.id)
          : ps.dependantIds;
      // `dependantPricing`, not `dependantCost`: the cost alone throws away the
      // one thing this loop has to propagate — that a covered dependant could
      // not be priced, which makes every figure below a lower bound rather than
      // an answer.
      const priced = dependantPricing(
        ts.dependant, ps.tierKey, dependantIds, dependants, ps.depOptionIds,
      );
      total += priced.total;
      if (priced.unresolved) incomplete = true;
    }
  }
  const rate = options.member_leave_rate ?? 0;
  // **Only a TRADEABLE day count moves the wallet.** `leaveDays` is live,
  // unsaved input, and its validity is checked in `leaveTrade`, which gates the
  // leave SAVE button — not this. So a member who typed 999 into a field capped
  // at 5 had the wallet credit 999 days: the strip showed a large "added to
  // your allowance" and cleared `submitBlocked` over a genuine shortfall (and a
  // stray "buy 999" fabricated one, refusing a member who could afford their
  // elections). The server re-validates either way, so this was a misstatement
  // and a false gate rather than a bypass — but it was a misstatement about
  // money, on the screen the member commits from.
  const cap =
    leaveAction === "buy" ? leave?.max_buy_days : leaveAction === "sell" ? leave?.max_sell_days : 0;
  const entered = Number(leaveDays);
  const days =
    Number.isFinite(entered) && entered > 0
      ? cap == null
        ? entered
        : Math.min(entered, Math.max(cap, 0))
      : 0;
  const leaveImpact =
    rate > 0 && days > 0 && leaveAction !== "none"
      ? (leaveAction === "buy" ? -1 : 1) * days * rate
      : 0;
  return {
    wallet: options.flex_wallet,
    proration: options.flex_proration ?? null,
    currency: options.flex_currency,
    total,
    leaveImpact,
    balance: options.flex_wallet - total + leaveImpact,
    onChange: options.flex_drawdown_rule === "on_change",
    incomplete,
  };
}

/** Why a leave action can't be chosen — null when it can. The same rules the
 *  server enforces in `enrollment_validation.validate_leave` / `apply_leave`,
 *  stated up front so a member doesn't discover the limit as a 422 on save. */
export function leaveBlockedReason(
  action: "buy" | "sell",
  leave: MemberLeaveOptions | null,
): string | null {
  if (!leave) return "No leave policy is configured for this benefit year.";
  if (action === "buy") {
    if (!leave.allow_buy) return "Buying leave isn't permitted this year.";
    if (leave.max_buy_days <= 0) return "No buy-leave days are configured.";
    return null;
  }
  if (!leave.allow_sell) return "Selling leave isn't permitted this year.";
  if (!leave.sell_eligible) return "This member isn't eligible to sell leave.";
  if (leave.max_sell_days <= 0) return "No sell-leave days are configured.";
  return null;
}

export interface LeaveTrade {
  trading: boolean;
  isBuy: boolean;
  minDays: number;
  maxDays: number;
  step: number;
  rate: number;
  enteredDays: number;
  /** Magnitude of the flex movement at the entered day count (0 when unpriced). */
  impact: number;
  /** Magnitude at the member's day cap — what the allowance is worth in full. */
  maxImpact: number;
  buyBlocked: string | null;
  sellBlocked: string | null;
  /** The blocked reason for the CURRENTLY chosen action (null when none). */
  blockedReason: string | null;
  daysError: string | null;
  notPositive: boolean;
  /** Everything the save button must refuse, bar the caller's own
   *  `disabled`/`saving` — kept here so the two surfaces cannot enforce
   *  different subsets of what `validate_leave` checks. */
  invalid: boolean;
}

/** Every rule `validate_leave` enforces, evaluated once.
 *
 * Mirroring only the maximum — which is what a hand-rolled check reaches for —
 * still lets through the 422 this exists to prevent: the minimum, the increment
 * and the per-tier eligibility each reject a value the cap allows. */
export function leaveTrade(
  action: string,
  days: string,
  leave: MemberLeaveOptions | null,
  ratePerDay: number | null,
): LeaveTrade {
  const buyBlocked = leaveBlockedReason("buy", leave);
  const sellBlocked = leaveBlockedReason("sell", leave);
  const trading = action === "buy" || action === "sell";
  const isBuy = action === "buy";
  const maxDays = leave ? (isBuy ? leave.max_buy_days : leave.max_sell_days) : 0;
  const minDays = leave ? (isBuy ? leave.min_buy_days : leave.min_sell_days) : 0;
  const step = leave?.increment_days || 0.5;
  const rate = ratePerDay ?? 0;
  const enteredDays = Number(days) || 0;
  const overLimit = trading && enteredDays > maxDays;
  const belowMin = trading && enteredDays > 0 && enteredDays < minDays;
  const notPositive = trading && enteredDays <= 0;
  const offIncrement =
    trading &&
    enteredDays > 0 &&
    step > 0 &&
    Math.abs(enteredDays / step - Math.round(enteredDays / step)) > 1e-6;
  const daysError = overLimit
    ? `More than the ${maxDays}-day limit`
    : belowMin
      ? `At least ${minDays} day${minDays === 1 ? "" : "s"}`
      : offIncrement
        ? `Must be in ${step}-day steps`
        : null;
  const blockedReason = trading ? (isBuy ? buyBlocked : sellBlocked) : null;
  return {
    trading,
    isBuy,
    minDays,
    maxDays,
    step,
    rate,
    enteredDays,
    impact: rate > 0 && trading ? enteredDays * rate : 0,
    maxImpact: rate > 0 && trading ? maxDays * rate : 0,
    buyBlocked,
    sellBlocked,
    blockedReason,
    daysError,
    notPositive,
    invalid: !!daysError || notPositive || !!blockedReason,
  };
}

/** Up/down wording for a non-baseline tier, in whichever vocabulary the surface
 *  speaks. Both readings come from ONE direction value, so the two pages can
 *  never disagree about which way a tier moves. */
export function directionLabel(
  direction: CohortTier["direction"],
  memberVoice = false,
): string | null {
  if (direction === "upgrade") return memberVoice ? "More cover" : "Upgrade";
  if (direction === "downgrade") return memberVoice ? "Less cover" : "Downgrade";
  return null;
}
