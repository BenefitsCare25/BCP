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
import type {
  CohortTier,
  ElectionIn,
  EnrollmentDetail,
  EnrollmentOptions,
  MemberLeaveOptions,
  ProductTierSet,
} from "@/api/enrollment";
import { cn } from "@/lib/cn";
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

export function classifyRel(rel?: string | null): "spouse" | "child" | null {
  const s = (rel ?? "").toLowerCase();
  if (!s) return null;
  if (SPOUSE_WORDS.some((w) => s.includes(w))) return "spouse";
  if (CHILD_WORDS.some((w) => s.includes(w))) return "child";
  return null;
}

/** Live dependant flex for the SELECTED plan/tier = incremental cost of the covered
 *  dependants (additive over Employee-Only), matching the server's ``dependant_tag``. */
export function dependantCost(
  dep: ProductTierSet["dependant"],
  tierKey: string,
  selectedIds: string[],
  deps: DependantRef[],
  depOptionIds: Record<string, string> = {},
): number {
  if (!dep || dep.mode === "none" || !selectedIds.length) return 0;
  const tp = dep.by_tier[tierKey];
  if (!tp) return 0;
  if (dep.mode === "per_pax") return (tp.per_pax_rate ?? 0) * selectedIds.length;
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
    for (const id of selectedIds) {
      const role = classifyRel(deps.find((x) => x.id === id)?.relationship);
      if (!role) continue;
      const linked = amountFor(role);
      if (linked != null) {
        total += linked;
        continue;
      }
      const chosen = depOptionIds[role];
      const choice = dep.option_choices
        ?.find((r) => r.role === role)
        ?.choices.find((c) => c.category_id === chosen);
      total += choice?.amounts_by_dependant?.[id] ?? choice?.amount ?? 0;
    }
    return total;
  }
  const role = spouse && child ? "both" : spouse ? "spouse" : child ? "child" : null;
  if (!role) return 0;
  return tp.family.find((f) => f.role === role)?.amount ?? 0;
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
  for (const ts of tierSets) {
    const ps = state[ts.product_code];
    if (!ps || ps.declined) continue;
    const tier = ts.tiers.find((t) => t.key === ps.tierKey);
    if (tier?.price_tag) total += tier.price_tag;
    // Compulsory dependant cover is employer-funded (part of base) — only
    // a voluntary opt-in draws flex.
    if (allowDeps && ts.dependant_participation !== "compulsory")
      total += dependantCost(
        ts.dependant, ps.tierKey, ps.dependantIds, dependants, ps.depOptionIds,
      );
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
}: {
  flex: FlexSummary;
  allowOverdraft: boolean;
  /** Surface-specific guidance when overdrawn and overdrafts are blocked. */
  shortfallHint: string;
}) {
  return (
    <div
      className={cn(
        "grid gap-3 rounded-lg border border-border bg-muted/20 p-3",
        flex.leaveImpact !== 0 ? "grid-cols-4" : "grid-cols-3",
      )}
    >
      <FlexStat label="Flex wallet" value={fmtCurrency(flex.wallet)} />
      <FlexStat
        label={flex.onChange ? "Flex drawn (changes)" : "Price tags used"}
        value={fmtCurrency(flex.total)}
        tone={flex.total < 0 ? "good" : undefined}
      />
      {flex.leaveImpact !== 0 && (
        <FlexStat
          label={flex.leaveImpact < 0 ? "Leave bought" : "Leave sold"}
          value={`${flex.leaveImpact < 0 ? "-" : "+"}${fmtCurrency(
            Math.abs(flex.leaveImpact),
          )}`}
          tone={flex.leaveImpact < 0 ? "bad" : "good"}
        />
      )}
      <FlexStat
        label={flex.balance < 0 ? "Shortfall (top-up)" : "Balance remaining"}
        value={fmtCurrency(Math.abs(flex.balance))}
        tone={flex.balance < 0 ? "bad" : "good"}
      />
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
export function PlanFinancialsRow({ fin }: { fin: PlanFinancials }) {
  const stats: { label: string; value: string }[] = [];
  if (fin.sum_insured != null)
    stats.push({ label: "Covered amount", value: fmtCurrency(fin.sum_insured) });
  if (fin.premium_rate != null)
    stats.push({
      label: `Rate${fin.rate_basis === "per_1000_si" ? " (per $1k SI)" : ""}`,
      value: String(fin.premium_rate),
    });
  if (fin.annual_premium != null)
    stats.push({ label: "Annual premium", value: fmtCurrency(fin.annual_premium) });
  if (!stats.length) return null;
  return (
    <div className="mt-2 grid grid-cols-2 gap-x-3 gap-y-1 border-t border-border pt-2 sm:grid-cols-3">
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
}: {
  action: string;
  days: string;
  /** The member's bounds + eligibility (null = no leave policy this year). */
  leave: MemberLeaveOptions | null;
  /** Per-day flex price of a traded day (null/0 = leave is unpriced). */
  ratePerDay: number | null;
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
                rate > 0 ? ` (-$${fmtAmount(leave.max_buy_days * rate)})` : ""
              }`,
            !sellBlocked &&
              `sell up to ${leave.max_sell_days} day${leave.max_sell_days === 1 ? "" : "s"}${
                rate > 0 ? ` (+$${fmtAmount(leave.max_sell_days * rate)})` : ""
              }`,
          ]
            .filter(Boolean)
            .join(" · ")}
          {rate > 0 && ` · $${fmtAmount(rate)} per day`}
        </p>
      )}

      <div className="mt-2 flex flex-wrap items-end gap-3">
        <div>
          <Label>Action</Label>
          <Select value={action} onValueChange={onActionChange} disabled={disabled}>
            <SelectTrigger className="w-[140px]">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="none">None</SelectItem>
              <SelectItem value="buy" disabled={!!buyBlocked}>
                Buy
              </SelectItem>
              <SelectItem value="sell" disabled={!!sellBlocked}>
                Sell
              </SelectItem>
            </SelectContent>
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
            className="w-[120px]"
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
              {isBuy ? "-" : "+"}${fmtAmount(Math.abs(impact))}
            </div>
            <div className="text-2xs text-muted-foreground">
              {enteredDays} × ${fmtAmount(rate)}/day · max ${fmtAmount(maxImpact)}
            </div>
          </div>
        )}
        <Button
          variant="outline"
          size="sm"
          disabled={
            disabled || saving || !!daysError || notPositive || !!currentBlocked
          }
          onClick={onSave}
        >
          Save leave
        </Button>
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
  onChange,
}: {
  ts: ProductTierSet;
  ps: ProductState;
  disabled: boolean;
  allowDeps: boolean;
  dependants: DependantRef[];
  flexOnChange: boolean;
  onChange: (next: ProductState) => void;
}) {
  const isCompulsory = !ts.can_decline;
  const selectedTier = ts.tiers.find((t) => t.key === ps.tierKey);
  const selectedFin = !ps.declined ? selectedTier?.financials ?? null : null;
  const dependantScope = ts.dependant_participation;
  // Compulsory dependant cover = automatic (no member choice, no flex);
  // voluntary = opt-in checkboxes that draw flex.
  const depCompulsory = dependantScope === "compulsory";
  return (
    <div
      className={cn(
        "rounded-lg border bg-card p-3",
        ps.declined ? "border-border opacity-60" : "border-border",
      )}
    >
      <div className="flex items-center justify-between gap-3">
        <div className="flex items-center gap-2">
          <span className="text-sm font-medium text-foreground">{ts.product_code}</span>
          {isCompulsory ? (
            <Badge variant="outline" className="gap-1 text-2xs">
              <Lock className="size-2.5" /> Required
            </Badge>
          ) : (
            <Badge variant="outline" className="text-2xs text-muted-foreground">
              Optional
            </Badge>
          )}
        </div>
        <div className="flex items-center gap-2">
          {!isCompulsory && !disabled && (
            <label className="flex cursor-pointer items-center gap-1.5 text-xs text-muted-foreground">
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
            <SelectTrigger className="w-[240px]">
              <SelectValue placeholder={ps.declined ? "Declined" : "Keep current"} />
            </SelectTrigger>
            <SelectContent>
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
                        · flex {fmtCurrency(t.price_tag)}
                      </span>
                    )}
                  </span>
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>
      </div>
      {!ps.declined && selectedTier?.price_tag != null && (
        <div className="mt-2 flex items-center gap-1.5 text-xs">
          <span className="text-muted-foreground">
            {flexOnChange ? "Flex change:" : "Flex price tag:"}
          </span>
          <span className="font-medium text-foreground">
            {fmtCurrency(selectedTier.price_tag)}
          </span>
          <span className="text-2xs text-muted-foreground">
            {flexOnChange
              ? "(difference vs default plan, drawn from wallet)"
              : "(deducted from wallet — separate from premium)"}
          </span>
        </div>
      )}
      {selectedFin && !ps.declined && <PlanFinancialsRow fin={selectedFin} />}
      {allowDeps && dependants.length > 0 && !ps.declined && (
        <div className="mt-2 border-t border-border pt-2">
          {dependantScope && (
            <div className="mb-1 flex items-center gap-1 text-2xs uppercase tracking-wider text-muted-foreground">
              <span>
                Dependants · {dependantScope}
                {depCompulsory && " (auto-included)"}
              </span>
              <InfoHint>
                {depCompulsory
                  ? "Dependant cover is included automatically for this plan — no extra flex is drawn."
                  : "Tick a family member to add them to this plan. Voluntary cover draws extra flex from the wallet."}
              </InfoHint>
            </div>
          )}
          <div className="flex flex-wrap gap-3">
            {dependants.map((d) => (
              <label
                key={d.id}
                className="flex items-center gap-1.5 text-xs text-foreground"
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
                <div key={r.role} className="mt-2 flex items-center gap-2">
                  <span className="text-xs capitalize text-muted-foreground">
                    {r.role} cover level
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
                    <SelectTrigger className="w-[220px]">
                      <SelectValue placeholder="Select level…" />
                    </SelectTrigger>
                    <SelectContent>
                      {r.choices.map((c) => (
                        <SelectItem key={c.category_id} value={c.category_id}>
                          <span className="flex items-center gap-2">
                            <span>
                              {c.sum_insured != null
                                ? `${c.label} — ${fmtCurrency(c.sum_insured)}`
                                : c.label}
                            </span>
                            <span className="text-2xs text-muted-foreground">
                              {c.amount != null
                                ? `· flex ${fmtCurrency(c.amount)}`
                                : "· by age"}
                            </span>
                          </span>
                        </SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                  {!ps.depOptionIds[r.role] && (
                    <span className="text-2xs text-warn">
                      Unpriced until a level is selected
                    </span>
                  )}
                </div>
              ))}
          {!depCompulsory && ts.dependant && ts.dependant.mode !== "none" && (() => {
            const cost = dependantCost(
              ts.dependant, ps.tierKey, ps.dependantIds, dependants, ps.depOptionIds,
            );
            return (
              <div className="mt-2 flex items-center gap-1.5 text-xs">
                <span className="text-muted-foreground">Dependant flex:</span>
                <span className="font-medium text-foreground">
                  +{fmtCurrency(cost)}
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
