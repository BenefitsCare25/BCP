import type { BrokerClaim } from "@/api/claims";
import { SectionLabel } from "@/components/ui/section-label";
import { fmtDate } from "@/lib/format";

/**
 * The insurer leg's dates and its three SLA counters.
 *
 * All three counters are DERIVED server-side from the dates
 * (`services/claim_settlement.py`) and were being served on every broker claim
 * payload while nothing rendered them. "Days over deadline" in particular is
 * the number a broker works the queue by — an unpaid claim's copy changes every
 * night, which is exactly why it is computed rather than stored, and exactly
 * why it has to be on screen rather than only in a spreadsheet.
 *
 * Renders nothing until a claim reaches the settlement leg. Before dispatch
 * every row here is blank, and a block of dashes reads as missing data rather
 * than as a stage that has not happened yet.
 */

function Fact({
  label,
  children,
  tone,
}: {
  label: string;
  children: React.ReactNode;
  tone?: "warn";
}) {
  return (
    <div>
      <SectionLabel as="dt">{label}</SectionLabel>
      <dd
        className={
          tone === "warn"
            ? "mt-0.5 text-sm font-medium tabular-nums text-warn"
            : "mt-0.5 text-sm tabular-nums text-foreground"
        }
      >
        {children}
      </dd>
    </div>
  );
}

/** "3 days late" / "in 2 days" / "due today" — a signed day count is only
 *  readable once it says which side of the deadline it is on. */
function deadlineText(days: number): string {
  if (days > 0) return `${days} ${days === 1 ? "day" : "days"} late`;
  if (days === 0) return "due today";
  const left = Math.abs(days);
  return `${left} ${left === 1 ? "day" : "days"} left`;
}

/** Statuses that mean the claim has reached the insurer leg. Tested ALONGSIDE
 *  the dates, not instead of them: a claim recorded as paid without a dispatch
 *  timestamp (a LOG case settled outside the normal flow, or a migrated row)
 *  still has a payment date and amount, and gating on `sent_to_insurer_at`
 *  alone hid exactly the two figures a broker opens a paid claim to check. */
const SETTLEMENT_STATUSES = new Set(["sent_to_insurer", "paid"]);

/** Whether this claim ever went to the insurer.
 *
 * **Must mirror `claim_settlement.was_dispatched`.** It gates the amendment
 * form, and a wider gate here renders four editable fields whose save the
 * server then 409s. In particular an INSURER-DECLINED claim
 * (`sent_to_insurer → rejected`) has a dispatch timestamp and status
 * `rejected` — it was sent, its recorded dates are correctable, and it is
 * precisely the "wrong date, no way back" case the feature exists for. */
export function hasSettlement(claim: BrokerClaim): boolean {
  return (
    SETTLEMENT_STATUSES.has(claim.status) ||
    Boolean(claim.sent_to_insurer_at) ||
    Boolean(claim.paid_on)
  );
}

/** Whether the PAYMENT figures may be corrected. Only on a claim recorded as
 *  paid: writing `paid_on` onto one still with the insurer does not move the
 *  status but does stop the SLA clock, dropping an unpaid claim off the
 *  overdue list. Mirrors the server's own refusal. */
export function canAmendPayment(claim: BrokerClaim): boolean {
  return claim.status === "paid";
}

export function ClaimSettlementFacts({ claim }: { claim: BrokerClaim }) {
  if (!hasSettlement(claim)) return null;
  const over = claim.days_over_deadline;
  return (
    <dl className="grid grid-cols-1 gap-x-6 gap-y-4 sm:grid-cols-2">
      <Fact label="Sent to insurer">
        {claim.sent_to_insurer_at ? fmtDate(claim.sent_to_insurer_at) : "—"}
      </Fact>
      <Fact label="Insurer deadline">
        {claim.insurer_deadline_on ? fmtDate(claim.insurer_deadline_on) : "—"}
      </Fact>
      {over != null && (
        <Fact label="Deadline" tone={over > 0 ? "warn" : undefined}>
          {deadlineText(over)}
        </Fact>
      )}
      {claim.insurer_days != null && (
        <Fact label="Days with insurer">{claim.insurer_days}</Fact>
      )}
      {claim.servicer_days != null && (
        <Fact label="Days we held it">{claim.servicer_days}</Fact>
      )}
      {claim.paid_on && <Fact label="Paid on">{fmtDate(claim.paid_on)}</Fact>}
      {claim.payment_amount != null && (
        <Fact label="Amount paid">
          {/* The POLICY currency, not the claim's. What the insurer paid is
              denominated the same way as what we approved it for — on a foreign
              claim, labelling it `claim.currency` restates an SGD settlement in
              the currency of the receipt and overstates it by the rate. */}
          {claim.policy_currency} {claim.payment_amount.toFixed(2)}
          {/* A shortfall is the whole reason payment_amount is stored apart
              from amount_approved — say so rather than leaving two numbers to
              be compared by eye. */}
          {claim.amount_approved != null &&
            claim.payment_amount < claim.amount_approved && (
              <span className="ml-2 text-xs font-normal text-warn">
                short by {claim.policy_currency}{" "}
                {(claim.amount_approved - claim.payment_amount).toFixed(2)}
              </span>
            )}
        </Fact>
      )}
    </dl>
  );
}
