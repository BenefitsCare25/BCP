/** The flexible-benefits wallet, as a mount.
 *
 * Three things the broker card did are deliberately not done here:
 *
 * 1. **Non-claimable categories are not rendered.** The broker card lists them
 *    with a crossed-out mark, which is precisely the empty mount DESIGN.md
 *    bans — it advertises cover the member cannot obtain. The leaf shows only
 *    what was issued to them.
 * 2. **`source` is not surfaced.** "Resolved from dependant records" describes
 *    our pipeline, not the member's benefits.
 * 3. **The stale-assignment warning is rewritten.** "Re-assign wallets from the
 *    Flex tab" is an instruction to a broker. The member still needs to know
 *    the list may be out of date — that part is honest and stays — but pointed
 *    at someone they can actually ask. */
import type { FlexCoverageLine } from "@/types";
import { FAMILY_STATUS_LABELS, type FamilyStatusCode } from "@/types";
import { Mount, MountRow, MountRule } from "./Mount";
import { Money } from "./Figure";
import { WALLET_LABEL } from "./FlexProrationNote";
import { familyGloss } from "./glossary";

function familyLabel(code: string | null): string | null {
  if (!code) return null;
  return (
    familyGloss(code) ?? FAMILY_STATUS_LABELS[code as FamilyStatusCode] ?? code
  );
}

export function FlexMount({
  flex,
  rise = true,
}: {
  flex: FlexCoverageLine;
  /** Off inside a coverage-deck slide, whose own transition owns the arrival. */
  rise?: boolean;
}) {
  const family = familyLabel(flex.family_status);
  const currency = flex.currency ?? "S$";

  // A lone claimable category with no cap and no note of its own is the wallet
  // restated under a second name — "What you can claim for: Flexible Benefits ·
  // No separate cap", inside a mount already titled "Flexible benefits". A
  // second category, a sub-limit or a note all make the breakdown say something
  // the allowance above it doesn't.
  //
  // `UsageLeaf.FlexBlock` drops a lone row on the same REASONING, deliberately
  // not the same TEST — and the two are not interchangeable. That tab lists
  // `FlexCategoryUtilization` (which carries `approved`/`pending` and no note)
  // over every category; this one lists `FlexBenefitCategoryLine` (which carries
  // a note and no usage) over the CLAIMABLE ones only, because a member is never
  // shown cover they cannot obtain. Each drops the row when its own list has
  // nothing left to add, which is the shared rule; asserting a stronger parity
  // than the data shapes allow is how a comment starts lying.
  const allClaimable = flex.benefit_categories.filter((c) => c.claimable);
  const lone = allClaimable.length === 1 ? allClaimable[0] : null;
  const claimable =
    lone && lone.sub_limit === null && !lone.note ? [] : allClaimable;

  // The wallet ledger: allowance − what your choices cost ± a leave trade.
  //
  // Gated on the BALANCE existing, not on the price tags being non-zero: the
  // balance is `wallet − price_tags + leave_flex_amount`, so a member with no
  // priced upgrades but a confirmed leave trade has a balance that differs from
  // their allowance, and the old gate hid exactly that case — leaving this tab
  // stating one allowance while "What's left" stated another.
  const spent = flex.price_tags_total ?? 0;
  const leave = flex.leave_flex_amount ?? 0;
  const balance = flex.flex_balance;
  const showLedger = balance != null && (spent !== 0 || leave !== 0);
  // A choice priced past the wallet leaves a NEGATIVE balance. "Left to spend
  // S$-450" is not a sentence anyone reads correctly; it is a shortfall, and it
  // is said as one (the broker card has always done this).
  const shortfall = balance != null && balance < 0;

  return (
    <Mount
      as="article"
      rise={rise}
      label="Flexible benefits"
      // No gloss. "Your allowance to spend across the benefits listed here" is
      // a table of contents for a card that is already a labelled figure over a
      // labelled list — it cost a line above the fold and named the card twice.
      //
      // Label and figure share ONE baseline beside the title, matching the
      // usage tab's wallet: stacked, a two-word caption under a short figure
      // built a third band of height into the card's head.
      aside={
        <span className="flex items-baseline justify-end gap-1.5">
          <span className="leaf-label">
            {WALLET_LABEL}
          </span>
          <Money
            value={flex.wallet_amount}
            currency={currency}
            emphasis="display"
          />
        </span>
      }
    >
      {/* Why the allowance is what it is, when it is not the full year's, in
          the same two-column shape as the usage tab: the reason on the left,
          the figure it was scaled from on the right, under the heading half
          each belongs to.

          This tab used to print the pro-rated figure under the words "Yearly
          allowance" with nothing to explain it — naming a reduced number as the
          annual one on a screen where the annual one is a different number.
          Both tabs show the same wallet, so both say the same thing about it;
          this one keeps the annual amount, since "what's left" is about what
          has gone rather than about the arithmetic. */}
      {flex.proration && (
        <div className="flex flex-wrap items-baseline justify-between gap-x-4 gap-y-1 text-row text-label">
          <span>Pro-rated to {flex.proration.note} of cover</span>
          <span className="shrink-0">
            <Money value={flex.proration.full_amount} currency={currency} /> a
            year
          </span>
        </div>
      )}

      {(family || flex.tier_name) && (
        <dl>
          {family && <MountRow term="Covers">{family}</MountRow>}
          {flex.tier_name && (
            <MountRow term="Flex dollar band">{flex.tier_name}</MountRow>
          )}
        </dl>
      )}

      {/* No margins on any block below. `Mount` is a flex column with `gap-3`;
          a margin here ADDS to that gap rather than replacing it, which is what
          left this mount's rules sitting 16px from one neighbour and 24px from
          the other. Grouping that needs to be tighter than the gap gets its own
          flex wrapper, so there is still exactly one spacing mechanism. */}
      {showLedger && (
        <>
          <MountRule />
          <dl>
            {spent !== 0 && (
              <MountRow
                term="Spent on your cover"
                gloss="Flex dollars already used to pay for the plans above."
              >
                {/* Stored positive; the direction is in the term, not a sign
                    wedged between the currency symbol and the digits. */}
                <Money value={spent} currency={currency} />
              </MountRow>
            )}
            {/* Without this row the three figures on screen don't reconcile:
                allowance − spent ≠ left. Signed on the wire — buying leave
                spends, selling credits — and the direction is put in the term
                here too, so the figure stays a plain amount. */}
            {leave !== 0 && (
              <MountRow
                term={`Leave you ${leave < 0 ? "bought" : "sold back"}${
                  flex.leave_days != null
                    ? ` (${flex.leave_days} ${flex.leave_days === 1 ? "day" : "days"})`
                    : ""
                }`}
                gloss={
                  leave < 0
                    ? "Taken from your flex dollars."
                    : "Added to your flex dollars."
                }
              >
                <Money value={Math.abs(leave)} currency={currency} />
              </MountRow>
            )}
            {balance != null && (
              <MountRow term={shortfall ? "Short by" : "Left to spend"}>
                <Money
                  value={Math.abs(balance)}
                  currency={currency}
                  emphasis="strong"
                  className={shortfall ? "text-strike-pending" : undefined}
                />
              </MountRow>
            )}
          </dl>
          {shortfall && (
            <p className="text-row text-label">
              Your choices cost more than your flex dollars. Your HR team can tell
              you how the difference is settled.
            </p>
          )}
        </>
      )}

      {/* The allowance is knowingly overstated when we couldn't work out the
          member's age — the price tags for their choices were never applied.
          Saying nothing would let them plan against a figure we already know is
          wrong, and claim against it. */}
      {!flex.price_age_known && (
        <p className="text-row text-label">
          We don&rsquo;t have your date of birth on file, so what your plan
          choices cost hasn&rsquo;t been taken off your flex dollars yet — the
          figure above may be higher than what you can actually spend. Your HR
          team can add your date of birth.
        </p>
      )}

      {claimable.length > 0 && (
        <>
          <MountRule />
          {/* The label belongs to the list it heads, so the two are one group
              rather than two siblings spaced identically to everything else. */}
          <div className="flex flex-col gap-1">
            <h3 className="leaf-label">What you can claim for</h3>
            <dl className="divide-y divide-hairline/75">
              {claimable.map((cat, i) => (
                <MountRow key={i} term={cat.name} gloss={cat.note ?? undefined}>
                  {cat.sub_limit != null ? (
                    <>
                      <Money value={cat.sub_limit} currency={currency} />
                      <span className="text-label"> cap</span>
                    </>
                  ) : (
                    <span className="text-label">No separate cap</span>
                  )}
                </MountRow>
              ))}
            </dl>
          </div>
        </>
      )}

      {flex.assignment_stale && (
        <p className="border-t border-hairline/75 pt-3 text-row text-label">
          Your company recently changed this scheme, so the list above may not
          be up to date yet. Your HR team can confirm what you can claim for.
        </p>
      )}
    </Mount>
  );
}
