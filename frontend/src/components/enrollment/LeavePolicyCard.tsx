import { useState } from "react";
import { Loader2 } from "lucide-react";
import { toast } from "sonner";
import {
  type LeaveRates,
  useLeavePolicy,
  useUpsertLeavePolicy,
} from "@/api/enrollment";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { InfoHint } from "@/components/ui/tooltip";
import { LeaveRatesEditor } from "@/components/enrollment/LeaveRatesEditor";

/** Does ANY tier grant days in this direction? The company default of 0 means
 *  "not by default", not "never" — a tier override still has to work. */
function anyTierAllows(
  rates: LeaveRates,
  field: "max_buy_days" | "max_sell_days",
): boolean {
  return Object.values(rates.limits ?? {}).some((l) => (l?.[field] ?? 0) > 0);
}

// Standing per-year leave policy (buy/sell bounds + per-day price tag). Lives on
// the Leave tab of the enrollment page, beside the windows that switch trading
// on; the window form only opens/closes windows. Keyed by policyYearId at the
// call site so local edit state resets on a year switch.
export function LeavePolicyCard({
  policyYearId,
  readOnly = false,
}: {
  policyYearId: string;
  readOnly?: boolean;
}) {
  const { data: policy } = useLeavePolicy(policyYearId);
  const upsert = useUpsertLeavePolicy(policyYearId);
  const [maxBuy, setMaxBuy] = useState<string>("");
  const [maxSell, setMaxSell] = useState<string>("");
  const [increment, setIncrement] = useState<string>("");
  const [leaveRates, setLeaveRates] = useState<LeaveRates | null>(null);

  const buy = maxBuy !== "" ? Number(maxBuy) : (policy?.max_buy_days ?? 0);
  const sell = maxSell !== "" ? Number(maxSell) : (policy?.max_sell_days ?? 0);
  const inc = increment !== "" ? Number(increment) : (policy?.increment_days ?? 1);
  // The stored bag is untyped JSON server-side, so read it defensively — a
  // pre-per-tier row has no `limits` key at all.
  const stored = (policy?.leave_rates ?? {}) as Partial<LeaveRates>;
  const initialRates: LeaveRates = {
    attribute: typeof stored.attribute === "string" ? stored.attribute : null,
    rates: stored.rates ?? {},
    limits: stored.limits ?? {},
  };
  const ratesToSave = leaveRates ?? initialRates;

  return (
    <div className="rounded-lg border border-border bg-card p-4">
      <div className="flex items-center gap-1">
        <h2 className="text-sm font-semibold text-foreground">Leave policy</h2>
        <InfoHint>
          How much leave members may trade this benefit year, and what a day is
          worth. Buying spends the per-day rate from the member's flex wallet;
          selling credits it. The limits below are the company DEFAULT — each
          grade/designation tier can override them in the price tag table.
          Trading is only offered in windows that have leave trading switched on.
        </InfoHint>
      </div>
      <p className="mt-1 text-2xs uppercase tracking-wider text-muted-foreground">
        Company default
      </p>
      {readOnly && (
        <p className="mt-2 text-xs text-muted-foreground">
          This policy is locked while an enrolment period is open.
        </p>
      )}
      <fieldset disabled={readOnly} className="contents">
      <div className="mt-1.5 grid gap-3 sm:grid-cols-3">
        <div>
          <Label htmlFor="lp-buy">Max buy (days)</Label>
          <Input
            id="lp-buy"
            type="number"
            min={0}
            value={maxBuy !== "" ? maxBuy : (policy?.max_buy_days ?? 0)}
            onChange={(e) => setMaxBuy(e.target.value)}
          />
        </div>
        <div>
          <Label htmlFor="lp-sell">Max sell (days)</Label>
          <Input
            id="lp-sell"
            type="number"
            min={0}
            value={maxSell !== "" ? maxSell : (policy?.max_sell_days ?? 0)}
            onChange={(e) => setMaxSell(e.target.value)}
          />
        </div>
        <div>
          <Label htmlFor="lp-inc">Increment (days)</Label>
          <Input
            id="lp-inc"
            type="number"
            min={0.5}
            step={0.5}
            value={increment !== "" ? increment : (policy?.increment_days ?? 1)}
            onChange={(e) => setIncrement(e.target.value)}
          />
        </div>
      </div>

      <div className="mt-3">
        <Label>Leave price tag &amp; limits per tier</Label>
        {/* Mount only once the policy has RESOLVED (data !== undefined), so the
            editor seeds from the saved rates instead of capturing empties on the
            pre-load render and wiping them on save.

            The key is the YEAR, deliberately not `policy.id`: the first save
            CREATES the policy, so an id-based key remounted the editor mid-save,
            reseeded it from the not-yet-refetched (still null) policy, and its
            mount effect pushed `{attribute: null, rates: {}}` back up — the next
            save then wrote those empties over the rates just entered. */}
        {policy !== undefined && (
          <LeaveRatesEditor
            key={policyYearId}
            policyYearId={policyYearId}
            value={initialRates}
            maxBuyDays={buy}
            maxSellDays={sell}
            onChange={setLeaveRates}
          />
        )}
      </div>

      <div className="mt-3 flex justify-end">
        <Button
          variant="outline"
          disabled={upsert.isPending}
          onClick={() =>
            upsert.mutate(
              {
                // `allow_*` is the company-wide ON/OFF gate and `validate_leave`
                // checks it BEFORE the per-tier cap — so it must consider the
                // tiers too. Deriving it from the global max alone made
                // "nobody by default, Managers up to 10" impossible: the tier
                // cap saved and displayed, but every Manager was refused.
                allow_buy: buy > 0 || anyTierAllows(ratesToSave, "max_buy_days"),
                allow_sell: sell > 0 || anyTierAllows(ratesToSave, "max_sell_days"),
                // Preserve any stored minimum rather than silently resetting it
                // to 0 on every save; clamp so it can't exceed a lowered max
                // (the server rejects min > max).
                min_buy_days: Math.min(policy?.min_buy_days ?? 0, buy),
                max_buy_days: buy,
                min_sell_days: Math.min(policy?.min_sell_days ?? 0, sell),
                max_sell_days: sell,
                increment_days: inc || 1,
                leave_rates: ratesToSave,
                notes: null,
              },
              { onSuccess: () => toast.success("Leave policy saved.") },
            )
          }
        >
          {upsert.isPending && <Loader2 className="size-4 animate-spin" />}
          Save leave policy
        </Button>
      </div>
      </fieldset>
    </div>
  );
}
