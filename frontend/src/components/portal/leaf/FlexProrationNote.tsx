/** Why a member's flex dollars are not the full year's.
 *
 * Three surfaces answer that question — "What's covered" (`FlexMount`),
 * "What's left" (`UsageLeaf`) and the enrollment review (`WalletLedger`) — and
 * all three print the same wallet, so a member moving between them must not
 * meet three accounts of one figure.
 *
 * **What is shared is the WORDING, not the layout.** Each card lays its figures
 * out differently by design (a two-column row on the coverage and usage tabs, a
 * ledger row on the review step), so a single component would have to grow a
 * variant per caller — the shape that drifts. `prorationReason` is the sentence
 * itself, which is the part that must never differ; the fraction inside it is
 * SERVED (`proration.note`, from `services/flex_proration.describe`) and is
 * never recomputed in the client, because the month count has no exact JS
 * equivalent worth maintaining twice and a fraction that drifts from the figure
 * beside it is silent. */
import { Money } from "./Figure";

type Proration = { full_amount: number; note: string };

/** "Pro-rated to 3/6 months of cover" — the one phrasing, everywhere. */
export function prorationReason(note: string): string {
  return `Pro-rated to ${note} of cover`;
}

/** The reason plus the figure it was scaled from, as one line. Used where the
 * card states its figures as rows rather than as two columns. */
export function FlexProrationNote({
  proration,
  currency,
  className,
}: {
  proration: Proration;
  currency: string;
  className?: string;
}) {
  return (
    <p className={`text-row text-label${className ? ` ${className}` : ""}`}>
      {prorationReason(proration.note)} ·{" "}
      <Money value={proration.full_amount} currency={currency} /> a year
    </p>
  );
}

/** What the wallet figure is CALLED, everywhere it appears.
 *
 * The product's own word is **flex dollars**, so that is the only word for it
 * on any member screen. "Allowance" was a second name for the thing the card is
 * already titled after, and it carried a worse problem: as "Yearly allowance"
 * it labelled a PRO-RATED figure as the annual one, on a screen where the
 * annual one is a different number — the two-surfaces-disagreeing failure this
 * feature exists to prevent. "Flex dollars" is true in both cases, and the
 * pro-ration row beneath states the period. */
export const WALLET_LABEL = "Flex dollars";
