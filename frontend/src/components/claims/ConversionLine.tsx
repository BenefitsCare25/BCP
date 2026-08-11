/** What a foreign claim is worth in the currency it will actually be settled in.
 *
 * Renders nothing for a domestic claim — most claims — so it can sit under the
 * claimed amount everywhere without adding a blank row.
 *
 * The `unavailable` state is the one that earns this component. A foreign claim
 * with no rate has NO policy-currency value at all: it cannot be measured
 * against the member's remaining limit, it is missing from every utilization
 * sum, and the approve endpoint refuses it (422 `fx_amount_required`). Left to
 * the eye, `amount_converted: null` looks identical to "no conversion needed",
 * which is how a foreign figure came to be read as an SGD one in the first
 * place — so the state is stated in words rather than implied by an absence.
 */
import { AlertTriangle } from "lucide-react";
import type { BrokerClaim } from "@/api/claims";

export function policyAmount(claim: BrokerClaim): number | null {
  if (claim.fx_state === "not_required") return claim.amount_claimed;
  return claim.amount_converted ?? null;
}

export function ConversionLine({ claim }: { claim: BrokerClaim }) {
  if (claim.fx_state === "not_required") return null;

  if (claim.fx_state === "unavailable") {
    return (
      <p className="mt-1 flex items-start gap-1.5 text-xs text-warn">
        <AlertTriangle className="mt-0.5 size-3 shrink-0" aria-hidden />
        No exchange rate for {claim.incurred_date} — enter the{" "}
        {claim.policy_currency} value before approving.
      </p>
    );
  }

  return (
    <p className="mt-1 text-xs text-muted-foreground">
      <span className="tabular-nums text-foreground">
        = {claim.policy_currency} {(claim.amount_converted ?? 0).toFixed(2)}
      </span>
      {claim.fx_source === "broker" ? (
        " — entered by an assessor"
      ) : (
        <>
          {claim.fx_rate ? ` at ${claim.fx_rate.toFixed(4)}` : ""}
          {/* The publication date, and only when it differs from the receipt.
              It differs across every weekend and holiday, so stating it
              unconditionally would train assessors to ignore it — and stating
              it WITHOUT the reason reads as a discrepancy. */}
          {claim.fx_stale && claim.fx_rate_date
            ? ` (rate of ${claim.fx_rate_date} — none published for ${claim.incurred_date})`
            : ""}
        </>
      )}
    </p>
  );
}
