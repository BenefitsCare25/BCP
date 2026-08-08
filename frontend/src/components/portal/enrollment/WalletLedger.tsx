/** The member's flexible-benefits allowance, as a ledger — allowance in, choices
 * out, what is left.
 *
 * **It is not a mount of its own.** It used to be the enrollment deck's first
 * slide, which put a near-empty pane in front of a member who had not yet
 * changed anything and made the page open on context instead of on a decision.
 * The running balance now lives in the page's heading row (`DeckHeader`), where
 * it is visible from every slide — so what is left here is the WORKING, and
 * working belongs with the total it explains: the review step.
 *
 * Deliberately built to read as the same object as `leaf/FlexMount`, which
 * states this wallet on the coverage tab: the same terms in the same order, the
 * same "Short by" for a negative. A member who moves between the two must not
 * meet two presentations of one wallet.
 *
 * The difference from `FlexMount` is that these figures are LIVE — they move as
 * the member changes a choice — which is why the bar is here and not there: it
 * is the glance that says how much of the allowance the current selection
 * consumes. It is `compact`, i.e. the bar alone with no sentence, because the
 * ledger under it already states every figure the sentence would (The
 * One-Description Rule). */
import { type FlexSummary, flexShort } from "@/components/enrollment/electionCore";
import { FillRule } from "@/components/portal/leaf/FillRule";
import { Money } from "@/components/portal/leaf/Figure";
import { FlexProrationNote } from "@/components/portal/leaf/FlexProrationNote";
import { MountRow } from "@/components/portal/leaf/Mount";

export function WalletLedger({
  flex,
  allowOverdraft,
}: {
  flex: FlexSummary;
  allowOverdraft: boolean;
}) {
  const currency = flex.currency ?? "S$";
  // `flexShort`, not `balance < 0`: the same predicate the heading row's figure
  // and the send gate use, so a sub-cent residue can't print "Short by S$0"
  // here while those two call it settled.
  const shortfall = flexShort(flex);
  // What the current selection NETS out of the wallet — the price tags less any
  // leave sold back. Derived from the balance rather than from `total` alone so
  // the bar and the "Left to spend" row below it are two readings of one
  // subtraction; sizing the bar on `total` while labelling the remainder
  // `balance` puts two figures on screen that do not reconcile the moment a
  // leave trade exists.
  const consumed = flex.wallet - flex.balance;

  return (
    <>
      {flex.wallet > 0 && consumed > 0 && (
        <FillRule
          limit={flex.wallet}
          approved={consumed}
          pending={0}
          remaining={flex.balance}
          currency={currency}
          compact
        />
      )}

      <dl>
        {/* The opening figure of the ledger. It was the mount's aside while this
            was a slide of its own; as a row it sits in the same subtraction as
            everything under it, which is what makes the four figures readable
            as one sum rather than as a headline and three details. */}
        {/* The gloss must not say "this year" for a PRO-RATED member: their
            year's allowance is a different, larger number, and this is the
            screen they commit from. The derivation goes under the row, in the
            same words the coverage and usage tabs use. */}
        <MountRow
          term="Flex dollars"
          gloss={
            flex.proration
              ? "What you have to spend for your period of cover."
              : "What you have to spend this year."
          }
        >
          <Money value={flex.wallet} currency={currency} />
        </MountRow>
        {/* Wrapped: `<dl>` permits only dt/dd/div, and every other row here
            goes through `MountRow`, which supplies its own div. A bare <p>
            child painted fine and put a non-dd into the list a screen reader
            walks. */}
        {flex.proration && (
          <div>
            <FlexProrationNote
              proration={flex.proration}
              currency={currency}
              className="pb-3"
            />
          </div>
        )}
        {flex.total !== 0 && (
          // **The direction is in the TERM, so the term has to follow the
          // sign.** A downgrade returns money — `total` goes negative and the
          // balance goes UP — and a fixed "Spent on your changes" then printed
          // "Spent S$82.84" directly above "Left to spend S$2,762.84" on a
          // S$2,680 allowance: three figures that cannot all be true. The
          // broker's strip got away with a fixed label because it printed the
          // sign; this ledger doesn't print signs (the same rule `FlexMount`
          // follows on the coverage tab), so the wording carries it.
          <MountRow
            term={
              flex.total > 0
                ? flex.onChange
                  ? "Spent on your changes"
                  : "Spent on your cover"
                : "Added back by your changes"
            }
            gloss={
              flex.total < 0
                ? "Your choices cost less than your current plans."
                : flex.onChange
                  ? "The difference between your choices and your current plans."
                  : "What the plans you've chosen cost."
            }
          >
            <Money value={Math.abs(flex.total)} currency={currency} />
          </MountRow>
        )}
        {flex.leaveImpact !== 0 && (
          <MountRow
            term={`Leave you ${flex.leaveImpact < 0 ? "bought" : "sold back"}`}
            gloss={
              flex.leaveImpact < 0
                ? "Taken from your flex dollars."
                : "Added to your flex dollars."
            }
          >
            <Money value={Math.abs(flex.leaveImpact)} currency={currency} />
          </MountRow>
        )}
        <MountRow term={shortfall ? "Short by" : "Left to spend"}>
          <Money
            value={Math.abs(flex.balance)}
            currency={currency}
            emphasis="strong"
            className={shortfall ? "text-strike-pending" : undefined}
          />
        </MountRow>
      </dl>

      {/* Said BEFORE the shortfall line, because it changes how to read every
          figure above it — a total missing a dependant's price is a floor, and a
          balance derived from it is a ceiling. Silence here let this ledger
          contradict the product slides it summarises. */}
      {flex.incomplete && (
        <p className="text-row text-label">
          One of the people you&rsquo;ve covered doesn&rsquo;t have a price yet,
          so this is the most we can work out so far — it will go up once that
          choice is made.
        </p>
      )}

      {shortfall && (
        <p
          className={
            allowOverdraft ? "text-row text-label" : "text-row text-strike-pending"
          }
        >
          {allowOverdraft
            ? "Your choices cost more than your flex dollars. Your company allows this — your HR team can tell you how the difference is settled."
            : "Your choices cost more than your flex dollars. Change one of them to bring it back, or ask your HR team."}
        </p>
      )}
    </>
  );
}
