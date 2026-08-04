/** The BROKER's election UI — composed only by `routes/enrollment/elections.tsx`.
 *
 * The member portal renders its own components (`components/portal/enrollment/`)
 * against the shared logic in `electionCore.ts`. That split is deliberate and
 * worth keeping: this file used to serve both surfaces by branching on
 * `memberLabels` and `useInLeaf()`, and four of the defects recorded against it
 * were one branch added here and forgotten three hundred lines away — most
 * visibly 44px member controls leaking onto this page's 36px rhythm.
 *
 * What must NOT fork is the arithmetic. Tier resolution, dependant pricing, the
 * flex balance, the leave bounds and the PUT payload all live in
 * `electionCore.ts` and are imported by both, so the member can never be shown
 * a different price from the one a broker would elect on their behalf. */
import { ArrowDown, ArrowUp, Lock } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { InfoHint } from "@/components/ui/tooltip";
import type {
  CohortTier,
  MemberLeaveOptions,
  ProductTierSet,
} from "@/api/enrollment";
import {
  type DependantRef,
  type FlexSummary,
  type ProductState,
  classifyRel,
  dependantPricing,
  leaveTrade,
  signedMoney,
} from "./electionCore";
import { cn } from "@/lib/cn";
import { fmtCurrency } from "@/lib/format";
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
        // Two-up on a phone, full width on desktop. At 3-4 fixed columns each
        // stat had ~90px, so "Flex drawn (changes)" wrapped to three lines and
        // its figure sat under a stack of label.
        flex.leaveImpact !== 0
          ? "grid-cols-2 sm:grid-cols-4"
          : "grid-cols-2 sm:grid-cols-3",
      )}
    >
      <FlexStat label="Flex wallet" value={signedMoney(flex.wallet)} />
      <FlexStat
        label={flex.onChange ? "Flex drawn (changes)" : "Price tags used"}
        value={signedMoney(flex.total)}
        tone={flex.total < 0 ? "good" : undefined}
      />
      {flex.leaveImpact !== 0 && (
        <FlexStat
          label={flex.leaveImpact < 0 ? "Leave bought" : "Leave sold"}
          value={`${flex.leaveImpact < 0 ? "-" : "+"}${signedMoney(
            Math.abs(flex.leaveImpact),
          )}`}
          tone={flex.leaveImpact < 0 ? "bad" : "good"}
        />
      )}
      <FlexStat
        label={flex.balance < 0 ? "Shortfall (top-up)" : "Balance remaining"}
        value={signedMoney(Math.abs(flex.balance))}
        tone={flex.balance < 0 ? "bad" : "good"}
      />
      {/* Said BEFORE the shortfall line, because it changes how to read every
          figure above it — a total that is missing a dependant's price is a
          floor, and a balance derived from it is a ceiling. Silence here let
          the strip contradict the card below it. */}
      {flex.incomplete && (
        <p className="col-span-full text-xs text-muted-foreground">
          A covered dependant is unpriced, so these figures are a lower bound
          until the option level resolves.
        </p>
      )}
      {flex.balance < 0 && (
        <p className="col-span-full text-xs">
          {allowOverdraft ? (
            <span className="text-muted-foreground">
              This enrolment period allows overdrafts — the shortfall can be submitted
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

/** Premium / covered-amount summary shown under a plan choice. The premium
 * fields are the point on this surface — `build_portal_enrollment` scrubs them
 * before the member's own page or the employee-view preview ever receives
 * them, so nothing here has to decide whether to show them. */
export function PlanFinancialsRow({ fin }: { fin: PlanFinancials }) {
  const stats: { label: string; value: string }[] = [];
  if (fin.sum_insured != null)
    stats.push({ label: "Covered amount", value: signedMoney(fin.sum_insured) });
  if (fin.premium_rate != null)
    stats.push({
      label: `Rate${fin.rate_basis === "per_1000_si" ? " (per $1k SI)" : ""}`,
      value: String(fin.premium_rate),
    });
  if (fin.annual_premium != null)
    stats.push({
      label: "Annual premium",
      value: signedMoney(fin.annual_premium),
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
  // Every rule `validate_leave` enforces, evaluated once and shared with the
  // member surface — a guard that catches one of three still lets the 422 it
  // exists to prevent through.
  const t = leaveTrade(action, days, leave, ratePerDay);

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
          worth. Without this the broker only learns the limit by exceeding it. */}
      {leave && (!t.buyBlocked || !t.sellBlocked) && (
        <p className="mt-0.5 text-xs text-muted-foreground">
          {[
            !t.buyBlocked &&
              `buy up to ${leave.max_buy_days} day${leave.max_buy_days === 1 ? "" : "s"}${
                t.rate > 0 ? ` (-${signedMoney(leave.max_buy_days * t.rate)})` : ""
              }`,
            !t.sellBlocked &&
              `sell up to ${leave.max_sell_days} day${leave.max_sell_days === 1 ? "" : "s"}${
                t.rate > 0 ? ` (+${signedMoney(leave.max_sell_days * t.rate)})` : ""
              }`,
          ]
            .filter(Boolean)
            .join(" · ")}
          {t.rate > 0 && ` · ${signedMoney(t.rate)} per day`}
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
              <SelectItem value="buy" disabled={!!t.buyBlocked}>
                Buy
              </SelectItem>
              <SelectItem value="sell" disabled={!!t.sellBlocked}>
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
            min={t.minDays}
            max={t.trading ? t.maxDays : undefined}
            step={t.step}
            className="w-[120px]"
            value={days}
            disabled={disabled || !t.trading}
            aria-invalid={!!t.daysError || undefined}
            onChange={(e) => onDaysChange(e.target.value)}
          />
          {t.trading && (
            <p
              className={cn(
                "mt-1 text-2xs",
                t.daysError ? "text-error" : "text-muted-foreground",
              )}
            >
              {t.daysError ?? (
                <>
                  {t.minDays > 0
                    ? `${t.minDays}–${t.maxDays} days`
                    : `Up to ${t.maxDays} day${t.maxDays === 1 ? "" : "s"}`}
                  {t.step !== 1 ? `, in ${t.step}-day steps` : ""}
                </>
              )}
            </p>
          )}
        </div>
        {/* The money view of the elected trade — day counts alone don't tell the
            member what leaving with 3 days costs their wallet. */}
        {t.trading && t.rate > 0 && (
          <div className="rounded-md border border-border bg-muted/20 px-2.5 py-1.5">
            <div className="text-2xs uppercase tracking-wider text-muted-foreground">
              {t.isBuy ? "Flex spent" : "Flex credited"}
            </div>
            {/* Exact figures, not compacted — this lands on payroll. */}
            <div
              className={cn(
                "text-sm font-semibold",
                t.isBuy ? "text-error" : "text-good",
              )}
            >
              {t.isBuy ? "-" : "+"}
              {signedMoney(Math.abs(t.impact))}
            </div>
            <div className="text-2xs text-muted-foreground">
              {t.enteredDays} × {signedMoney(t.rate)}/day · max{" "}
              {signedMoney(t.maxImpact)}
            </div>
          </div>
        )}
        <Button
          variant="outline"
          size="sm"
          disabled={disabled || saving || t.invalid}
          onClick={onSave}
        >
          Save leave
        </Button>
      </div>

      {/* Why an option is unavailable, and what a missing rate means. Both are
          silent server-side outcomes otherwise (a 422, or a $0 flex draw). */}
      {t.blockedReason && (
        <p className="mt-2 text-xs text-error">{t.blockedReason}</p>
      )}
      {t.trading && !t.blockedReason && t.rate <= 0 && (
        <p className="mt-2 text-xs text-warn">
          No leave rate is configured
          {leave?.rate_value ? ` for “${leave.rate_value}”` : ""} — trading leave
          won't change the flex wallet.
        </p>
      )}
      {!t.trading && (t.buyBlocked || t.sellBlocked) && (
        <p className="mt-2 text-xs text-muted-foreground">
          {t.buyBlocked && t.sellBlocked
            ? `${t.buyBlocked} ${t.sellBlocked}`
            : (t.buyBlocked ?? t.sellBlocked)}
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
      {/* Stacks below `sm`. As one justify-between row the right-hand group
          alone (a 240px Select plus the Decline control) measured ~310px, which
          pushed the page to 465px wide inside a 414px viewport. */}
      <div className="flex flex-col gap-2 sm:flex-row sm:items-center sm:justify-between sm:gap-3">
        <div className="min-w-0">
          <div className="flex flex-wrap items-center gap-2">
            <span className="text-sm font-medium text-foreground">
              {ts.product_code}
            </span>
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
        </div>
        <div className="flex items-center gap-2 sm:shrink-0">
          {!isCompulsory && !disabled && (
            <label className="flex shrink-0 cursor-pointer items-center gap-1.5 text-xs text-muted-foreground">
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
            <SelectTrigger className="w-full text-base sm:w-[240px] sm:text-sm">
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
            {signedMoney(selectedTier.price_tag)}
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
                {`Dependants · ${dependantScope}${depCompulsory ? " (auto-included)" : ""}`}
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
                className="flex cursor-pointer items-center gap-1.5 text-xs text-foreground"
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
                    classifyRel(dependants.find((d) => d.id === id)) === r.role,
                ),
              )
              .map((r) => (
                <div key={r.role} className="mt-2 flex flex-wrap items-center gap-2">
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
                    <SelectTrigger className="w-full text-base sm:w-[220px] sm:text-sm">
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
            const { total: cost, unresolved } = dependantPricing(
              ts.dependant, ps.tierKey, ps.dependantIds, dependants, ps.depOptionIds,
            );
            return (
              <div className="mt-2 flex items-center gap-1.5 text-xs">
                <span className="text-muted-foreground">Dependant flex:</span>
                <span className="font-medium text-foreground">
                  {unresolved ? "unpriced" : `+${fmtCurrency(cost)}`}
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
