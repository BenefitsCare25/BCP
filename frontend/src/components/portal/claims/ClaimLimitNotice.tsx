/** The live balance for the claim type selected on the submission form.
 *
 * This is advisory at filing time, not a false client-side reimbursement
 * decision. A receipt may legitimately exceed what the plan can reimburse, so
 * the member sends the full receipt and the assessor approves up to the live
 * balance. The server's approval guard remains authoritative and serialized.
 */
import { Limit, Money } from "@/components/portal/leaf/Figure";
import {
  availableAfterPending,
  CLAIM_LIMIT_BASIS_LABELS,
} from "@/lib/claimLimits";
import type { NewClaimForm } from "./useNewClaimForm";

function LimitRow({
  row,
  currency,
}: {
  row: NewClaimForm["limitRows"][number];
  currency: string;
}) {
  const hasComputedLimit = row.limit !== null && row.remaining !== null;
  const available = availableAfterPending(
    row.remaining,
    row.pending,
    row.pendingUnconverted,
  );
  return (
    <div className="border-t border-hairline pt-2 first:border-t-0 first:pt-0">
      <div className="flex flex-wrap items-baseline justify-between gap-x-4 gap-y-1">
        <span className="text-row text-label">{row.label}</span>
        {hasComputedLimit ? (
          <span className="text-row text-record">
            <Money
              value={
                row.pending > 0 && available !== null
                  ? available
                  : Math.max(0, row.remaining ?? 0)
              }
              currency={currency}
              emphasis="strong"
            />{" "}
            {row.pending > 0 && available !== null
              ? "available after pending "
              : "left "}
            <span className="text-label">of </span>
            <Money value={row.limit} currency={currency} />
          </span>
        ) : row.limitDisplay || row.limit !== null ? (
          <Limit amount={row.limit} display={row.limitDisplay} currency={currency} />
        ) : (
          <span className="text-row text-label">No numeric yearly limit recorded</span>
        )}
      </div>
      {row.pending > 0 && hasComputedLimit && (
        <p className="mt-0.5 text-2xs text-label">
          <Money value={row.remaining} currency={currency} /> confirmed balance;{" "}
          <Money value={row.pending} currency={currency} /> submitted and not
          settled yet.
        </p>
      )}
      {row.pending > 0 && !hasComputedLimit && (
        <p className="mt-0.5 text-2xs text-label">
          <Money value={row.pending} currency={currency} /> submitted and not
          settled yet; this policy condition has no annual balance.
        </p>
      )}
      {row.pendingUnconverted > 0 && (
        <p className="mt-0.5 text-2xs text-label">
          {row.pendingUnconverted} pending foreign-currency claim
          {row.pendingUnconverted === 1 ? " is" : "s are"} still awaiting conversion.
        </p>
      )}
      {row.limitBasis && row.limitBasis !== "policy_year" && (
        <p className="mt-0.5 text-2xs text-label">
          {CLAIM_LIMIT_BASIS_LABELS[row.limitBasis]} condition; this is policy wording,
          not a yearly balance.
        </p>
      )}
    </div>
  );
}

export function ClaimLimitNotice({ form }: { form: NewClaimForm }) {
  if (!form.effectiveKind) return null;

  const currency = form.policyCurrency;
  return (
    <section
      aria-labelledby="claim-limit-heading"
      className="space-y-2.5 rounded-control border border-hairline bg-bar/55 px-3 py-3"
    >
      <div>
        <h2 id="claim-limit-heading" className="leaf-label">
          Limit for this claim
        </h2>
        <p className="text-row text-label">
          Verified annual balances and applicable policy conditions for your
          selected claim type.
        </p>
      </div>

      {form.utilization.isLoading ? (
        <p className="text-row text-label">Checking what you have left…</p>
      ) : form.utilization.isError ? (
        <p className="text-row text-label">
          We couldn&apos;t load your current balance. You can still submit the full
          receipt; your claim will be assessed against the plan on file.
        </p>
      ) : form.limitRows.length > 0 ? (
        <div className="space-y-2">
          {form.limitRows.map((row) => (
            <LimitRow key={row.key} row={row} currency={currency} />
          ))}
        </div>
      ) : (
        <p className="text-row text-label">
          No verified annual balance is configured for this claim type. Visit,
          day and treatment conditions under What&rsquo;s covered still apply.
        </p>
      )}

    </section>
  );
}
