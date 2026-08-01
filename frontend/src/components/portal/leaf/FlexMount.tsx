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
import { familyGloss } from "./glossary";

function familyLabel(code: string | null): string | null {
  if (!code) return null;
  return (
    familyGloss(code) ?? FAMILY_STATUS_LABELS[code as FamilyStatusCode] ?? code
  );
}

export function FlexMount({ flex }: { flex: FlexCoverageLine }) {
  const family = familyLabel(flex.family_status);
  const claimable = flex.benefit_categories.filter((c) => c.claimable);
  const currency = flex.currency ?? "S$";

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
      label="Flexible benefits"
      gloss="Your yearly allowance to spend across the benefits listed here."
      aside={
        <div className="text-right">
          <Money
            value={flex.wallet_amount}
            currency={currency}
            emphasis="display"
          />
          <div className="leaf-label mt-0.5">Yearly allowance</div>
        </div>
      }
    >
      {(family || flex.tier_name) && (
        <dl>
          {family && <MountRow term="Covers">{family}</MountRow>}
          {flex.tier_name && (
            <MountRow term="Your allowance band">{flex.tier_name}</MountRow>
          )}
        </dl>
      )}

      {showLedger && (
        <>
          <MountRule className="my-1" />
          <dl>
            {spent !== 0 && (
              <MountRow
                term="Spent on your cover"
                gloss="Allowance already used to pay for the plans above."
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
                    ? "Taken from your allowance."
                    : "Added to your allowance."
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
              Your choices cost more than your allowance. Your HR team can tell
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
          choices cost hasn&rsquo;t been taken off this allowance yet — the
          figure above may be higher than what you can actually spend. Your HR
          team can add your date of birth.
        </p>
      )}

      {claimable.length > 0 && (
        <>
          <MountRule className="my-1" />
          <h3 className="leaf-label mb-1 mt-3">What you can claim for</h3>
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
        </>
      )}

      {flex.assignment_stale && (
        <p className="mt-3 border-t border-hairline/75 pt-3 text-row text-label">
          Your company recently changed this scheme, so the list above may not
          be up to date yet. Your HR team can confirm what you can claim for.
        </p>
      )}
    </Mount>
  );
}
