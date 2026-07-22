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

// Standing per-year leave policy (buy/sell bounds + price tag). Lives on the
// company Settings page; the enrollment window form only opens/closes windows.
// Keyed by policyYearId at the call site so local edit state resets on a year
// switch.
export function LeavePolicyCard({ policyYearId }: { policyYearId: string }) {
  const { data: policy } = useLeavePolicy(policyYearId);
  const upsert = useUpsertLeavePolicy(policyYearId);
  const [maxBuy, setMaxBuy] = useState<string>("");
  const [maxSell, setMaxSell] = useState<string>("");
  const [increment, setIncrement] = useState<string>("");
  const [leaveRates, setLeaveRates] = useState<LeaveRates | null>(null);

  const buy = maxBuy !== "" ? Number(maxBuy) : (policy?.max_buy_days ?? 0);
  const sell = maxSell !== "" ? Number(maxSell) : (policy?.max_sell_days ?? 0);
  const inc = increment !== "" ? Number(increment) : (policy?.increment_days ?? 1);
  const initialRates: LeaveRates = {
    attribute:
      policy && "attribute" in (policy.leave_rates ?? {})
        ? (policy.leave_rates as LeaveRates).attribute
        : null,
    rates:
      policy && "rates" in (policy.leave_rates ?? {})
        ? (policy.leave_rates as LeaveRates).rates
        : {},
  };
  const ratesToSave = leaveRates ?? initialRates;

  return (
    <div className="rounded-lg border border-border bg-card p-4">
      <div className="flex items-center gap-1">
        <h3 className="text-sm font-semibold text-foreground">Leave policy</h3>
        <InfoHint>
          Buy/sell-leave bounds for this policy year. Members can buy extra days
          or sell days back — day counts only, no pricing is applied here.
        </InfoHint>
      </div>
      <div className="mt-3 grid gap-3 sm:grid-cols-3">
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
        <Label>Leave price tag</Label>
        {/* Mount only once the policy has resolved (data !== undefined) and key it
            to the loaded policy, so the editor seeds from the saved rates instead
            of capturing empties on the pre-load render and wiping them on save. */}
        {policy !== undefined && (
          <LeaveRatesEditor
            key={policy?.id ?? "new"}
            policyYearId={policyYearId}
            value={initialRates}
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
                allow_buy: buy > 0,
                allow_sell: sell > 0,
                min_buy_days: 0,
                max_buy_days: buy,
                min_sell_days: 0,
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
    </div>
  );
}
