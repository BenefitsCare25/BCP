/** Shared election UI + logic — composed by BOTH the broker elections page
 * (routes/enrollment/elections.tsx) and the member portal "My enrollment"
 * (components/portal/MemberEnrollmentPanel.tsx, also rendered read-only in the
 * broker's employee-view preview). Keeping the tier picker, dependant cover and
 * flex math here is what guarantees the member sees exactly the choices and
 * prices the broker would elect on their behalf. */
import { ArrowDown, ArrowUp, Lock } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { InfoHint } from "@/components/ui/tooltip";
import { actionClass } from "@/components/portal/leaf/Action";
import type {
  CohortTier,
  ElectionIn,
  EnrollmentDetail,
  EnrollmentOptions,
  MemberLeaveOptions,
  ProductTierSet,
} from "@/api/enrollment";
import { cn } from "@/lib/cn";
import { useInLeaf } from "@/lib/leaf-scope";
import { fmtAmount, fmtCurrency } from "@/lib/format";
import type { PlanFinancials } from "@/types";
import { Badge } from "@/components/ui/badge";
import { Checkbox } from "@/components/ui/checkbox";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";

/** Radix PORTALS its listbox to `document.body`, i.e. outside the `.leaf`
 * subtree — so on the member surface the open dropdown rendered in the BROKER's
 * tokens and type while the trigger under it stayed in the member's. Declaring
 * `leaf` on the portalled content re-points the tokens for that subtree, which
 * is the same mechanism `PortalShell` uses one level up. (`InfoHint` solves the
 * identical problem the other way, by rendering inline instead of portalling —
 * see lib/leaf-scope.) */
function LeafSelectContent({ children }: { children: React.ReactNode }) {
  const inLeaf = useInLeaf();
  return (
    <SelectContent className={inLeaf ? "leaf" : undefined}>
      {children}
    </SelectContent>
  );
}

/** The minimal dependant shape the election UI needs (statement dependants and
 * portal dependants both satisfy it). */
export interface DependantRef {
  id: string;
  name?: string | null;
  relationship?: string | null;
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

// Up/down indicator shown beside a non-baseline tier.
export function DirectionTag({ direction }: { direction: CohortTier["direction"] }) {
  if (direction === "upgrade")
    return (
      <span className="inline-flex items-center gap-0.5 text-2xs text-good">
        <ArrowUp className="size-2.5" /> Upgrade
      </span>
    );
  if (direction === "downgrade")
    return (
      <span className="inline-flex items-center gap-0.5 text-2xs text-warn">
        <ArrowDown className="size-2.5" /> Downgrade
      </span>
    );
  return null;
}

// Relationship classification — mirrors the backend's spouse/child word lists so
// the live dependant cost matches the server's snapshot.
const SPOUSE_WORDS = ["spouse", "husband", "wife", "partner"];
const CHILD_WORDS = ["child", "children", "son", "daughter", "kid", "step"];

/** Exact money with the sign OUTSIDE the currency symbol. Interpolating
 * `$${fmtAmount(v)}` renders a credit as "$-120"; money reads "-$120".
 *
 * `symbol` exists because the member portal must print `S$2,700` (DESIGN.md's
 * Tabular-Figure Rule: money is never abbreviated and never symbol-less on a
 * member surface) while the broker page keeps its bare `$`.
 *
 * **It is `fmtAmount`, not `fmtCurrency`, on BOTH surfaces, and that is
 * deliberate rather than incidental.** `fmtCurrency` compacts at a thousand,
 * so a broker reconciling a wallet read "$2.7K" for a figure they have to tie
 * back to a price tag — `fmtAmount` exists for exactly this ("editable / exact
 * figures … where the compact $1.2M form would lose precision"). What did NOT
 * survive that swap, and is restored here, is `fmtCurrency`'s sub-cent guard:
 * a residual −0.004 balance rendered as "-$0", which reads as settled while
 * `submitBlocked` is still firing on it. */
function signedMoney(v: number, symbol = "$"): string {
  const sign = v < 0 ? "-" : "";
  const a = Math.abs(v);
  if (a > 0 && a < 0.01) return `${sign}${symbol}${a.toExponential(2)}`;
  return `${sign}${symbol}${fmtAmount(a)}`;
}

export function classifyRel(rel?: string | null): "spouse" | "child" | null {
  const s = (rel ?? "").toLowerCase();
  if (!s) return null;
  if (SPOUSE_WORDS.some((w) => s.includes(w))) return "spouse";
  if (CHILD_WORDS.some((w) => s.includes(w))) return "child";
  return null;
}

/** Live dependant flex for the SELECTED plan/tier = incremental cost of the covered
 *  dependants (additive over Employee-Only), matching the server's ``dependant_tag``. */
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
  if (!dep || dep.mode === "none" || !selectedIds.length) return known(0);
  const tp = dep.by_tier[tierKey];
  if (!tp) return unknown;
  if (dep.mode === "per_pax") {
    return tp.per_pax_rate == null
      ? unknown
      : known(tp.per_pax_rate * selectedIds.length);
  }
  let spouse = 0;
  let child = 0;
  for (const id of selectedIds) {
    const kind = classifyRel(deps.find((x) => x.id === id)?.relationship);
    if (kind === "spouse") spouse += 1;
    else if (kind === "child") child += 1;
  }
  if (dep.mode === "slip_options") {
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
      const role = classifyRel(deps.find((x) => x.id === id)?.relationship);
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

/** The figure alone, for the places that only ever sum it. */
export function dependantCost(
  dep: ProductTierSet["dependant"],
  tierKey: string,
  selectedIds: string[],
  deps: DependantRef[],
  depOptionIds: Record<string, string> = {},
): number {
  return dependantPricing(dep, tierKey, selectedIds, deps, depOptionIds).total;
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
): FlexSummary | null {
  if (!options || options.flex_wallet == null) return null;
  let total = 0;
  let incomplete = false;
  for (const ts of tierSets) {
    const ps = state[ts.product_code];
    if (!ps || ps.declined) continue;
    const tier = ts.tiers.find((t) => t.key === ps.tierKey);
    if (tier?.price_tag) total += tier.price_tag;
    // Compulsory dependant cover is employer-funded (part of base) — only
    // a voluntary opt-in draws flex.
    if (allowDeps && ts.dependant_participation !== "compulsory") {
      // `dependantPricing`, not `dependantCost`: the cost alone throws away the
      // one thing this loop has to propagate — that a covered dependant could
      // not be priced, which makes every figure below a lower bound rather than
      // an answer.
      const priced = dependantPricing(
        ts.dependant, ps.tierKey, ps.dependantIds, dependants, ps.depOptionIds,
      );
      total += priced.total;
      if (priced.unresolved) incomplete = true;
    }
  }
  const rate = options.member_leave_rate ?? 0;
  const days = Number(leaveDays) || 0;
  const leaveImpact =
    rate > 0 && days > 0 && leaveAction !== "none"
      ? (leaveAction === "buy" ? -1 : 1) * days * rate
      : 0;
  return {
    wallet: options.flex_wallet,
    currency: options.flex_currency,
    total,
    leaveImpact,
    balance: options.flex_wallet - total + leaveImpact,
    onChange: options.flex_drawdown_rule === "on_change",
    incomplete,
  };
}

// One figure in the flex-wallet balance strip.
export function FlexStat({
  label,
  value,
  tone,
}: {
  label: string;
  value: string;
  tone?: "good" | "bad";
}) {
  return (
    <div>
      <div className="text-2xs uppercase tracking-wider text-muted-foreground">
        {label}
      </div>
      <div
        className={cn(
          "text-sm font-semibold",
          tone === "bad"
            ? "text-error"
            : tone === "good"
              ? "text-good"
              : "text-foreground",
        )}
      >
        {value}
      </div>
    </div>
  );
}

/** Flex wallet balance strip — wallet minus the price tags of selected coverage. */
export function FlexBalanceStrip({
  flex,
  allowOverdraft,
  shortfallHint,
  memberLabels = false,
  moneySymbol = "$",
}: {
  flex: FlexSummary;
  allowOverdraft: boolean;
  /** Surface-specific guidance when overdrawn and overdrafts are blocked. */
  shortfallHint: string;
  /** Currency symbol for the figures. The portal passes "S$" so the wallet
   * reads the same as the coverage tab; the broker page keeps "$". */
  moneySymbol?: string;
  /**
   * Say it in the member's words. "Flex drawn (changes)" and "Balance
   * remaining" name the mechanism a broker operates; a member is asking how
   * much they have and how much is left. The figures are identical — only the
   * labels differ — so the two surfaces can't disagree about the money.
   */
  memberLabels?: boolean;
}) {
  const t = memberLabels
    ? {
        wallet: "Your allowance",
        drawn: flex.onChange ? "Spent on your changes" : "Spent on your cover",
        bought: "Leave you bought",
        sold: "Leave you sold",
        shortfall: "Over your allowance by",
        balance: "Left to spend",
      }
    : {
        wallet: "Flex wallet",
        drawn: flex.onChange ? "Flex drawn (changes)" : "Price tags used",
        bought: "Leave bought",
        sold: "Leave sold",
        shortfall: "Shortfall (top-up)",
        balance: "Balance remaining",
      };
  return (
    <div
      className={cn(
        "grid gap-3 rounded-lg border border-border bg-muted/20 p-3",
        // Two-up on a phone, full width on desktop. At 3-4 fixed columns each
        // stat had ~90px, so "Flex drawn (changes)" wrapped to three lines and
        // its figure sat under a stack of label.
        flex.leaveImpact !== 0
          ? "grid-cols-2 sm:grid-cols-4"
          : "grid-cols-2 sm:grid-cols-3",
      )}
    >
      <FlexStat label={t.wallet} value={signedMoney(flex.wallet, moneySymbol)} />
      <FlexStat
        label={t.drawn}
        value={signedMoney(flex.total, moneySymbol)}
        tone={flex.total < 0 ? "good" : undefined}
      />
      {flex.leaveImpact !== 0 && (
        <FlexStat
          label={flex.leaveImpact < 0 ? t.bought : t.sold}
          value={`${flex.leaveImpact < 0 ? "-" : "+"}${signedMoney(
            Math.abs(flex.leaveImpact),
            moneySymbol,
          )}`}
          tone={flex.leaveImpact < 0 ? "bad" : "good"}
        />
      )}
      <FlexStat
        label={flex.balance < 0 ? t.shortfall : t.balance}
        value={signedMoney(Math.abs(flex.balance), moneySymbol)}
        tone={flex.balance < 0 ? "bad" : "good"}
      />
      {/* Said BEFORE the shortfall line, because it changes how to read every
          figure above it — a total that is missing a dependant's price is a
          floor, and a balance derived from it is a ceiling. Silence here let
          the strip contradict the card below it. */}
      {flex.incomplete && (
        <p className="col-span-full text-xs text-muted-foreground">
          {memberLabels
            ? "One of the people you've covered doesn't have a price yet, so this is the most we can work out so far — it will go up once that choice is made."
            : "A covered dependant is unpriced, so these figures are a lower bound until the option level resolves."}
        </p>
      )}
      {flex.balance < 0 && (
        <p className="col-span-full text-xs">
          {allowOverdraft ? (
            <span className="text-muted-foreground">
              This window allows overdrafts — the shortfall can be submitted
              (e.g. recovered via payroll).
            </span>
          ) : (
            <span className="text-error">{shortfallHint}</span>
          )}
        </p>
      )}
    </div>
  );
}

// Premium / covered-amount summary shown under a plan choice.
/**
 * The money facts for a tier.
 *
 * On the MEMBER surface this renders one line — the covered amount — because
 * `build_portal_enrollment` scrubs the premium fields before the portal or the
 * employee-view preview ever receives them. A premium is what the company pays
 * the insurer; it is not a price the member can act on, and sitting it beside a
 * flex figure invites reading one as the other. What a member decides on is the
 * covered amount here plus the flex price tag rendered above this row.
 *
 * On the BROKER elections page the premium fields are present and still render
 * — that is the surface where they are the point. Both behaviours come from one
 * component reading whatever the server chose to send, so the gate lives in one
 * place instead of being re-decided in the UI.
 */
export function PlanFinancialsRow({
  fin,
  memberLabels = false,
  moneySymbol = "$",
}: {
  fin: PlanFinancials;
  /** Member voice. Off by default so the broker page keeps its own wording. */
  memberLabels?: boolean;
  /** Currency symbol for the figures — "S$" on the member surface. */
  moneySymbol?: string;
}) {
  const stats: { label: string; value: string }[] = [];
  if (fin.sum_insured != null)
    stats.push({
      label: memberLabels ? "You'd be covered for" : "Covered amount",
      value: signedMoney(fin.sum_insured, moneySymbol),
    });
  if (fin.premium_rate != null)
    stats.push({
      label: `Rate${fin.rate_basis === "per_1000_si" ? " (per $1k SI)" : ""}`,
      value: String(fin.premium_rate),
    });
  if (fin.annual_premium != null)
    stats.push({
      label: "Annual premium",
      value: signedMoney(fin.annual_premium, moneySymbol),
    });
  if (!stats.length) return null;
  return (
    <div className="mt-2 grid grid-cols-1 gap-x-3 gap-y-2 border-t border-border pt-2 sm:grid-cols-3">
      {stats.map((s) => (
        <div key={s.label}>
          <div className="text-2xs uppercase tracking-wider text-muted-foreground">
            {s.label}
          </div>
          <div className="text-xs font-medium text-foreground">{s.value}</div>
        </div>
      ))}
    </div>
  );
}

/** Why a leave action can't be chosen — null when it can. The same rules the
 *  server enforces in `enrollment_validation.validate_leave` / `apply_leave`,
 *  stated up front so a member doesn't discover the limit as a 422 on save. */
function leaveBlockedReason(
  action: "buy" | "sell",
  leave: MemberLeaveOptions | null,
): string | null {
  if (!leave) return "No leave policy is configured for this benefit year.";
  if (action === "buy") {
    if (!leave.allow_buy) return "Buying leave isn't permitted this year.";
    if (leave.max_buy_days <= 0) return "No buy-leave allowance is configured.";
    return null;
  }
  if (!leave.allow_sell) return "Selling leave isn't permitted this year.";
  if (!leave.sell_eligible) return "This member isn't eligible to sell leave.";
  if (leave.max_sell_days <= 0) return "No sell-leave allowance is configured.";
  return null;
}

/** Buy/sell-leave election card. Shows the DAY limit, the member's per-day rate
 * and the resulting dollar impact on the flex wallet — the elected trade and the
 * maximum they could trade — so leave reads in money, not just day counts. */
export function LeaveTradingCard({
  action,
  days,
  leave,
  ratePerDay,
  disabled,
  saving,
  onActionChange,
  onDaysChange,
  onSave,
  moneySymbol = "$",
}: {
  action: string;
  days: string;
  /** The member's bounds + eligibility (null = no leave policy this year). */
  leave: MemberLeaveOptions | null;
  /** Per-day flex price of a traded day (null/0 = leave is unpriced). */
  ratePerDay: number | null;
  /** Currency symbol for every figure in this card. **It must be threaded, not
   * defaulted away**: this card sits directly beneath `FlexBalanceStrip`, which
   * the portal passes "S$", so hardcoding "$" here had a member reading
   * "Your allowance S$2,700" and "$250 per day" in adjacent cards — the exact
   * defect `moneySymbol` was introduced to prevent, reappearing in the one
   * component that never received it. */
  moneySymbol?: string;
  disabled: boolean;
  saving: boolean;
  onActionChange: (action: string) => void;
  onDaysChange: (days: string) => void;
  onSave: () => void;
}) {
  const buyBlocked = leaveBlockedReason("buy", leave);
  const sellBlocked = leaveBlockedReason("sell", leave);
  const trading = action === "buy" || action === "sell";
  const isBuy = action === "buy";
  const maxDays = leave ? (isBuy ? leave.max_buy_days : leave.max_sell_days) : 0;
  const minDays = leave ? (isBuy ? leave.min_buy_days : leave.min_sell_days) : 0;
  const step = leave?.increment_days || 0.5;
  const rate = ratePerDay ?? 0;
  const enteredDays = Number(days) || 0;
  // Signed flex impact, mirroring `leave_flex_amount`: buying spends, selling credits.
  const impact = rate > 0 && trading ? enteredDays * rate : 0;
  const maxImpact = rate > 0 && trading ? maxDays * rate : 0;
  // Mirror EVERY rule `validate_leave` enforces, not just the maximum — a guard
  // that catches one of three still lets the 422 it exists to prevent through.
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
  const currentBlocked = trading ? (isBuy ? buyBlocked : sellBlocked) : null;
  // The Reach Rule applies on the member surface only: the broker page is a
  // desktop tool at h-9 throughout, and this card is shared by both.
  const touch = useInLeaf();
  const saveBlocked =
    disabled || saving || !!daysError || notPositive || !!currentBlocked;

  return (
    <div className="rounded-lg border border-border bg-card p-3">
      <div className="flex items-center gap-1">
        <span className="text-sm font-medium text-foreground">Leave trading</span>
        <InfoHint>
          Buy extra leave days (spends flex) or sell days back (credits flex). The
          per-day rate comes from the leave policy for this member's
          {leave?.rate_attribute ? ` ${leave.rate_attribute}` : " grade"}.
        </InfoHint>
      </div>

      {/* The allowance, stated before anything is picked — day cap AND what it is
          worth. Without this the member only learns their limit by exceeding it. */}
      {leave && (!buyBlocked || !sellBlocked) && (
        <p className="mt-0.5 text-xs text-muted-foreground">
          {[
            !buyBlocked &&
              `buy up to ${leave.max_buy_days} day${leave.max_buy_days === 1 ? "" : "s"}${
                rate > 0 ? ` (-${moneySymbol}${fmtAmount(leave.max_buy_days * rate)})` : ""
              }`,
            !sellBlocked &&
              `sell up to ${leave.max_sell_days} day${leave.max_sell_days === 1 ? "" : "s"}${
                rate > 0 ? ` (+${moneySymbol}${fmtAmount(leave.max_sell_days * rate)})` : ""
              }`,
          ]
            .filter(Boolean)
            .join(" · ")}
          {rate > 0 && ` · ${moneySymbol}${fmtAmount(rate)} per day`}
        </p>
      )}

      <div className="mt-2 flex flex-wrap items-end gap-3">
        <div>
          <Label>Action</Label>
          <Select value={action} onValueChange={onActionChange} disabled={disabled}>
            <SelectTrigger className={cn("w-[140px]", touch && "min-h-11 text-base sm:text-sm")}>
              <SelectValue />
            </SelectTrigger>
            <LeafSelectContent>
              <SelectItem value="none">None</SelectItem>
              <SelectItem value="buy" disabled={!!buyBlocked}>
                Buy
              </SelectItem>
              <SelectItem value="sell" disabled={!!sellBlocked}>
                Sell
              </SelectItem>
            </LeafSelectContent>
          </Select>
        </div>
        <div>
          <Label htmlFor="leave-days">Days</Label>
          <Input
            id="leave-days"
            type="number"
            min={minDays}
            max={trading ? maxDays : undefined}
            step={step}
            className={cn("w-[120px]", touch && "min-h-11 text-base sm:text-sm")}
            value={days}
            disabled={disabled || !trading}
            aria-invalid={!!daysError || undefined}
            onChange={(e) => onDaysChange(e.target.value)}
          />
          {trading && (
            <p
              className={cn(
                "mt-1 text-2xs",
                daysError ? "text-error" : "text-muted-foreground",
              )}
            >
              {daysError ?? (
                <>
                  {minDays > 0 ? `${minDays}–${maxDays} days` : `Up to ${maxDays} day${maxDays === 1 ? "" : "s"}`}
                  {step !== 1 ? `, in ${step}-day steps` : ""}
                </>
              )}
            </p>
          )}
        </div>
        {/* The money view of the elected trade — day counts alone don't tell the
            member what leaving with 3 days costs their wallet. */}
        {trading && rate > 0 && (
          <div className="rounded-md border border-border bg-muted/20 px-2.5 py-1.5">
            <div className="text-2xs uppercase tracking-wider text-muted-foreground">
              {isBuy ? "Flex spent" : "Flex credited"}
            </div>
            {/* Exact figures, not compacted — this lands on payroll. */}
            <div
              className={cn(
                "text-sm font-semibold",
                isBuy ? "text-error" : "text-good",
              )}
            >
              {isBuy ? "-" : "+"}{moneySymbol}{fmtAmount(Math.abs(impact))}
            </div>
            <div className="text-2xs text-muted-foreground">
              {enteredDays} × {moneySymbol}{fmtAmount(rate)}/day · max {moneySymbol}
              {fmtAmount(maxImpact)}
            </div>
          </div>
        )}
        {/* Two renderings of one control, because this card is shared with the
            broker's enrollment page. On the member surface it has to be part of
            the portal's action language — a lone shared-`Button` outline beside
            the terracotta pills reads as a control that belongs to a different
            product — and the shared `Button` cannot get there through tokens
            alone (its outline variant draws from `border-input`, which also
            draws every text field). `touch` already marks this surface for the
            44px reach scale; it marks the tone too. */}
        {touch ? (
          <button
            type="button"
            className={actionClass("quiet", { className: "px-4" })}
            disabled={saveBlocked}
            onClick={onSave}
          >
            Save leave
          </button>
        ) : (
          <Button
            variant="outline"
            size="sm"
            disabled={saveBlocked}
            onClick={onSave}
          >
            Save leave
          </Button>
        )}
      </div>

      {/* Why an option is unavailable, and what a missing rate means. Both are
          silent server-side outcomes otherwise (a 422, or a $0 flex draw). */}
      {currentBlocked && (
        <p className="mt-2 text-xs text-error">{currentBlocked}</p>
      )}
      {trading && !currentBlocked && rate <= 0 && (
        <p className="mt-2 text-xs text-warn">
          No leave rate is configured
          {leave?.rate_value ? ` for “${leave.rate_value}”` : ""} — trading leave
          won't change the flex wallet.
        </p>
      )}
      {!trading && (buyBlocked || sellBlocked) && (
        <p className="mt-2 text-xs text-muted-foreground">
          {buyBlocked && sellBlocked
            ? `${buyBlocked} ${sellBlocked}`
            : (buyBlocked ?? sellBlocked)}
        </p>
      )}
    </div>
  );
}

/** One product's election card: tier picker with direction + flex tags,
 * decline toggle, dependant cover (checkboxes + freestanding option levels)
 * and the live flex readouts. Fully controlled via `ps` + `onChange`. */
export function ElectionProductCard({
  ts,
  ps,
  disabled,
  allowDeps,
  dependants,
  flexOnChange,
  gloss,
  memberLabels = false,
  moneySymbol = "$",
  onChange,
}: {
  ts: ProductTierSet;
  ps: ProductState;
  disabled: boolean;
  allowDeps: boolean;
  dependants: DependantRef[];
  flexOnChange: boolean;
  /** Plain-language line under the product code, on surfaces that need one. */
  gloss?: string | null;
  /** Member voice for the money copy. Off by default — the broker elections
   * page keeps its own wording. */
  memberLabels?: boolean;
  /** Currency symbol for the figures — "S$" on the member surface. */
  moneySymbol?: string;
  onChange: (next: ProductState) => void;
}) {
  const isCompulsory = !ts.can_decline;
  const selectedTier = ts.tiers.find((t) => t.key === ps.tierKey);
  const selectedFin = !ps.declined ? selectedTier?.financials ?? null : null;
  // The Reach Rule is a MEMBER-surface rule: the broker elections page is a
  // desktop tool at h-9 throughout, and this card is shared by both. Applied
  // unconditionally the 44px controls leaked onto the broker page, which then
  // carried 44px tier selects beside the 36px ones every other card uses —
  // while `LeaveTradingCard`, three hundred lines down, gates the identical
  // change on this exact flag. Same component, same surface, two answers.
  const touch = useInLeaf();
  const dependantScope = ts.dependant_participation;
  // Compulsory dependant cover = automatic (no member choice, no flex);
  // voluntary = opt-in checkboxes that draw flex.
  const depCompulsory = dependantScope === "compulsory";
  // Money on a MEMBER surface is never abbreviated (DESIGN.md) — `fmtCurrency`
  // renders 1200 as "$1.2K", which is the wrong precision beside a wallet
  // balance the member is trying to spend down to the dollar. The broker page
  // keeps the compact form, where it is a column of figures being scanned.
  const money = memberLabels
    ? (v: number) => signedMoney(v, moneySymbol)
    : fmtCurrency;
  return (
    <div
      className={cn(
        "rounded-lg border bg-card p-3",
        ps.declined ? "border-border opacity-60" : "border-border",
      )}
    >
      {/* Stacks below `sm`. As one justify-between row the right-hand group
          alone (a 240px Select plus the Decline control) measured ~310px, which
          pushed the enrollment page to 465px wide inside a 414px viewport — the
          whole surface scrolled sideways, and the plan picker was the thing
          hanging off the edge. */}
      <div className="flex flex-col gap-2 sm:flex-row sm:items-center sm:justify-between sm:gap-3">
        <div className="min-w-0">
          <div className="flex flex-wrap items-center gap-2">
            {/* A product CODE alone means nothing to the person choosing, and
                on a member surface it is not the working vocabulary — the name
                is. The broker page keeps the code, which is what they file and
                talk in. */}
            <span className="text-sm font-medium text-foreground">
              {memberLabels ? ts.product_name ?? ts.product_code : ts.product_code}
            </span>
            {/* Members get the words printed, not a pill: the album has no
                pill shapes, and "Required" beside a plan is a fact about the
                plan rather than a status to be badged. */}
            {memberLabels ? (
              <span className="text-2xs uppercase tracking-wider text-muted-foreground">
                {isCompulsory ? "Included for everyone" : "Your choice"}
              </span>
            ) : isCompulsory ? (
              <Badge variant="outline" className="gap-1 text-2xs">
                <Lock className="size-2.5" /> Required
              </Badge>
            ) : (
              <Badge variant="outline" className="text-2xs text-muted-foreground">
                Optional
              </Badge>
            )}
          </div>
          {/* The gloss is passed in rather than looked up here, so the broker's
              elections page is unchanged. */}
          {gloss && (
            <p className="mt-0.5 text-xs text-muted-foreground">{gloss}</p>
          )}
        </div>
        <div className="flex items-center gap-2 sm:shrink-0">
          {!isCompulsory && !disabled && (
            <label
              className={cn(
                "flex shrink-0 cursor-pointer items-center gap-1.5 text-xs text-muted-foreground",
                touch && "min-h-11",
              )}
            >
              <Checkbox
                checked={ps.declined}
                onCheckedChange={(c) => onChange({ ...ps, declined: !!c })}
              />
              Decline
            </label>
          )}
          <Select
            value={ps.declined ? "" : ps.tierKey}
            onValueChange={(v) => onChange({ ...ps, tierKey: v, declined: false })}
            disabled={disabled || ps.declined || !ts.allow_plan_change}
          >
            <SelectTrigger
              className={cn(
                "w-full text-base sm:w-[240px] sm:text-sm",
                touch && "min-h-11",
              )}
            >
              <SelectValue placeholder={ps.declined ? "Declined" : "Keep current"} />
            </SelectTrigger>
            <LeafSelectContent>
              {ts.tiers.map((t) => (
                <SelectItem key={t.key} value={t.key}>
                  <span className="flex items-center gap-2">
                    <span>
                      {t.label}
                      {t.is_baseline ? " (current)" : ""}
                    </span>
                    {!t.is_baseline && <DirectionTag direction={t.direction} />}
                    {t.price_tag != null && (
                      <span className="text-2xs text-muted-foreground">
                        · flex {money(t.price_tag)}
                      </span>
                    )}
                  </span>
                </SelectItem>
              ))}
            </LeafSelectContent>
          </Select>
        </div>
      </div>
      {/* What this choice costs the MEMBER — the one figure they can act on,
          so it is stated as a sentence rather than a labelled stat. A signed
          number against a jargon label ("Flex change: -$120") made the reader
          work out both the direction and whose money it is. */}
      {!ps.declined && selectedTier?.price_tag != null && (
        memberLabels ? (
          <p className="mt-2 text-xs text-muted-foreground">
            {selectedTier.price_tag === 0 ? (
              flexOnChange ? (
                "No change to your flex wallet."
              ) : (
                "Nothing is drawn from your flex wallet."
              )
            ) : selectedTier.price_tag > 0 ? (
              <>
                Costs you{" "}
                <span className="font-medium text-foreground">
                  {signedMoney(selectedTier.price_tag, moneySymbol)}
                </span>{" "}
                from your flex wallet
                {flexOnChange ? ", compared with your current plan." : "."}
              </>
            ) : (
              <>
                Gives you back{" "}
                <span className="font-medium text-foreground">
                  {signedMoney(Math.abs(selectedTier.price_tag), moneySymbol)}
                </span>{" "}
                in your flex wallet
                {flexOnChange ? ", compared with your current plan." : "."}
              </>
            )}
          </p>
        ) : (
          <div className="mt-2 flex items-center gap-1.5 text-xs">
            <span className="text-muted-foreground">
              {flexOnChange ? "Flex change:" : "Flex price tag:"}
            </span>
            <span className="font-medium text-foreground">
              {signedMoney(selectedTier.price_tag)}
            </span>
            <span className="text-2xs text-muted-foreground">
              {flexOnChange
                ? "(difference vs default plan, drawn from wallet)"
                : "(deducted from wallet — separate from premium)"}
            </span>
          </div>
        )
      )}
      {selectedFin && !ps.declined && (
        <PlanFinancialsRow
          fin={selectedFin}
          memberLabels={memberLabels}
          moneySymbol={moneySymbol}
        />
      )}
      {allowDeps && dependants.length > 0 && !ps.declined && (
        <div className="mt-2 border-t border-border pt-2">
          {dependantScope && (
            <div className="mb-1 flex items-center gap-1 text-2xs uppercase tracking-wider text-muted-foreground">
              {/* `dependantScope` is the slip's participation enum
                  ("compulsory" / "voluntary") — the broker's filing word for a
                  rule, printed raw. A member reading "Dependants · voluntary"
                  learns nothing they can act on; what they need is whether
                  their family is already in or whether ticking is what puts
                  them in. */}
              <span>
                {memberLabels
                  ? depCompulsory
                    ? "Your family, already covered"
                    : "Add your family"
                  : `Dependants · ${dependantScope}${depCompulsory ? " (auto-included)" : ""}`}
              </span>
              <InfoHint>
                {depCompulsory
                  ? memberLabels
                    ? "Your family is covered on this plan automatically — it costs you nothing extra."
                    : "Dependant cover is included automatically for this plan — no extra flex is drawn."
                  : memberLabels
                    ? "Tick anyone you'd like covered on this plan. Doing so spends part of your flex allowance."
                    : "Tick a family member to add them to this plan. Voluntary cover draws extra flex from the wallet."}
              </InfoHint>
            </div>
          )}
          <div className="flex flex-wrap gap-3">
            {dependants.map((d) => (
              <label
                key={d.id}
                // The Radix checkbox is 16x16; the label is the real target, so
                // the touch height goes here rather than inflating the box.
                className={cn(
                  "flex cursor-pointer items-center gap-1.5 text-xs text-foreground",
                  touch && "min-h-11",
                )}
              >
                <Checkbox
                  // Compulsory: all dependants are covered → checked + locked.
                  checked={depCompulsory || ps.dependantIds.includes(d.id)}
                  disabled={disabled || depCompulsory}
                  onCheckedChange={(c) => {
                    const ids = c
                      ? [...ps.dependantIds, d.id]
                      : ps.dependantIds.filter((x) => x !== d.id);
                    onChange({ ...ps, dependantIds: ids });
                  }}
                />
                {d.name ?? d.relationship ?? d.id}
              </label>
            ))}
          </div>
          {/* Freestanding dependant cover LEVELS (slip lists several, unlinked
              to employee plans) — one elected per role; each covered dependant
              then draws that level's slip rate on their own age band. */}
          {!depCompulsory &&
            (ts.dependant?.option_choices ?? [])
              .filter((r) =>
                ps.dependantIds.some(
                  (id) =>
                    classifyRel(
                      dependants.find((d) => d.id === id)?.relationship,
                    ) === r.role,
                ),
              )
              .map((r) => (
                <div key={r.role} className="mt-2 flex flex-wrap items-center gap-2">
                  <span className="text-xs capitalize text-muted-foreground">
                    {memberLabels ? `How much cover for your ${r.role}` : `${r.role} cover level`}
                  </span>
                  <Select
                    value={ps.depOptionIds[r.role] ?? ""}
                    onValueChange={(v) =>
                      onChange({
                        ...ps,
                        depOptionIds: { ...ps.depOptionIds, [r.role]: v },
                      })
                    }
                    disabled={disabled}
                  >
                    {/* Full width on a phone: at a fixed 220px this sat beside
                        its own label inside a 390px column and pushed the card
                        sideways — the same defect the tier picker above
                        documents. */}
                    <SelectTrigger
                      className={cn(
                        "w-full text-base sm:w-[220px] sm:text-sm",
                        touch && "min-h-11",
                      )}
                    >
                      <SelectValue placeholder="Select level…" />
                    </SelectTrigger>
                    <LeafSelectContent>
                      {r.choices.map((c) => (
                        <SelectItem key={c.category_id} value={c.category_id}>
                          <span className="flex items-center gap-2">
                            <span>
                              {c.sum_insured != null
                                ? `${c.label} — ${money(c.sum_insured)}`
                                : c.label}
                            </span>
                            <span className="text-2xs text-muted-foreground">
                              {c.amount != null
                                ? `· flex ${money(c.amount)}`
                                : "· by age"}
                            </span>
                          </span>
                        </SelectItem>
                      ))}
                    </LeafSelectContent>
                  </Select>
                  {!ps.depOptionIds[r.role] && (
                    <span className="text-2xs text-warn">
                      {memberLabels
                        ? "Pick a level to see what it costs you"
                        : "Unpriced until a level is selected"}
                    </span>
                  )}
                </div>
              ))}
          {!depCompulsory && ts.dependant && ts.dependant.mode !== "none" && (() => {
            const { total: cost, unresolved } = dependantPricing(
              ts.dependant, ps.tierKey, ps.dependantIds, dependants, ps.depOptionIds,
            );
            // The member sentence. "Dependant flex: +$0 (per dependant, added
            // to wallet draw)" named a pricing MODE, showed a signed figure and
            // left the member to work out whose money moved — and printed
            // "+$0" at them when nobody was ticked at all. The pricing mode is
            // the broker's concern: what a member decides on is whether adding
            // their family costs them anything.
            if (memberLabels) {
              if (!ps.dependantIds.length) return null;
              return (
                <p className="mt-2 text-xs text-muted-foreground">
                  {/* "Draws nothing" is only said when the price is KNOWN to be
                      zero. An unresolved price is $0 in the arithmetic but not
                      an answer, and asserting free cover beside this card's own
                      "pick a level" warning is how a member reaches a submit
                      that 409s `unpriced_elections` at them. */}
                  {unresolved ? (
                    "We'll show what covering them costs once the level above is chosen."
                  ) : cost > 0 ? (
                    <>
                      Covering them costs you{" "}
                      <span className="font-medium text-foreground">
                        {money(cost)}
                      </span>{" "}
                      from your flex wallet.
                    </>
                  ) : (
                    "Covering them draws nothing from your flex wallet."
                  )}
                </p>
              );
            }
            return (
              <div className="mt-2 flex items-center gap-1.5 text-xs">
                <span className="text-muted-foreground">Dependant flex:</span>
                <span className="font-medium text-foreground">
                  {unresolved ? "unpriced" : `+${money(cost)}`}
                </span>
                <span className="text-2xs text-muted-foreground">
                  {ts.dependant.mode === "per_pax"
                    ? "(per dependant, added to wallet draw)"
                    : ts.dependant.mode === "slip_options"
                      ? "(per dependant at the elected level, from the slip)"
                      : "(family-tier cost over Employee-Only)"}
                </span>
              </div>
            );
          })()}
        </div>
      )}
    </div>
  );
}
