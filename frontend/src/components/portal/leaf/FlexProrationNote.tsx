/** Why a member's flex allowance is not the full year's.
 *
 * ONE source of the wording, shared by "What's covered" (`FlexMount`), "What's
 * left" (`UsageLeaf`) and the enrollment review (`WalletLedger`), because all
 * three print the same allowance and a member who reads one explanation on one
 * screen and different words on another has no way to tell whether they are
 * looking at one figure or two. The fraction itself is SERVED
 * (`proration.note` — see `services/flex_proration.describe`); this only decides
 * the sentence around it.
 *
 * Two shapes, same words: `ProrationClause` is inline, for a card that states
 * its figures as one dot-separated run; `FlexProrationNote` is the standalone
 * line, for cards that state theirs as rows.
 *
 * The prop is typed on the two fields it uses rather than on `FlexProrationLine`:
 * the utilization endpoints send a narrower shape with no period bounds, and
 * demanding fields this never reads would make the shared component unusable on
 * exactly one of the three surfaces it exists to keep in step. */
import { Money } from "./Figure";

type Proration = { full_amount: number; note: string };

export function ProrationClause({
  proration,
  currency,
}: {
  proration: Proration;
  currency: string;
}) {
  return (
    <>
      <Money value={proration.full_amount} currency={currency} /> a year,
      pro-rated to {proration.note} of cover
    </>
  );
}

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
      <ProrationClause proration={proration} currency={currency} />
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
