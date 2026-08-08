/** Flexible benefits — ONE panel, from two sources.
 *
 * The wallet used to be drawn twice on this pane: `FlexCoverageCard` (wallet /
 * price tags used / balance, plus the claimable categories and their
 * sub-limits) and, seven hundred pixels later, the utilization section's own
 * flex card (available / wallet / coverage price tags / balance / claims
 * approved, plus the same categories and the same sub-limits). The figures come
 * from one wallet — `FlexCoverageLine` and `FlexUtilization` are the same
 * arithmetic stopped at different points — so the two cards could only ever
 * agree, and printing SGD 2,680 seven times across two cards is what made this
 * page read as duplicated.
 *
 * It is one ledger here: allowance, what has been drawn from it, what is left.
 * A term that hasn't moved isn't printed — a wallet with no price tags and no
 * claims used to render eight product lines of "SGD 0" and a "Balance" equal to
 * the wallet directly above it.
 */
import { useState } from "react";
import { AlertTriangle, CheckCircle2, ChevronRight, Wallet, XCircle } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { InfoHint } from "@/components/ui/tooltip";
import { SectionLabel } from "@/components/ui/section-label";
import { formatWallet } from "@/lib/flex";
import { cn } from "@/lib/cn";
import {
  FAMILY_STATUS_LABELS,
  type FamilyStatusCode,
  type FlexCategoryUtilization,
  type FlexCoverageLine,
  type FlexUtilization,
} from "@/types";
import { PendingSwatch, UtilizationBar } from "./usage";

const SOURCE_LABEL: Record<string, string> = {
  dependants: "family status from dependant records",
  roster: "family status from the roster",
  none: "family status not resolved",
};

function familyLabel(code: string | null): string | null {
  if (!code) return null;
  return FAMILY_STATUS_LABELS[code as FamilyStatusCode] ?? code;
}

interface CategoryRow {
  name: string;
  claimable: boolean;
  note: string | null;
  subLimit: number | null;
  use: FlexCategoryUtilization | null;
}

/** The statement names the categories and says which are claimable; the
 * utilization says what has been spent against them. Joined on the name, which
 * is the key `utilization.py` buckets flex claims on. */
function mergeCategories(
  flex: FlexCoverageLine,
  usage: FlexUtilization | null | undefined,
): CategoryRow[] {
  const spend = new Map<string, FlexCategoryUtilization>();
  for (const c of usage?.categories ?? []) {
    spend.set(c.name.trim().toLowerCase(), c);
  }
  const rows: CategoryRow[] = flex.benefit_categories.map((c) => {
    const key = c.name.trim().toLowerCase();
    const use = spend.get(key) ?? null;
    spend.delete(key);
    return {
      name: c.name,
      claimable: c.claimable,
      note: c.note,
      subLimit: c.sub_limit ?? use?.sub_limit ?? null,
      use,
    };
  });
  // A category the member has claimed against but the scheme no longer lists.
  for (const use of spend.values()) {
    rows.push({
      name: use.name,
      claimable: true,
      note: "no longer in the scheme",
      subLimit: use.sub_limit,
      use,
    });
  }
  return rows;
}

/** One category's position, as a figure the LEDGER ABOVE CAN BE ADDED FROM.
 *
 * **Every row leads with what was CLAIMED**, which is the term the ledger sums
 * ("SGD 1,340 flex dollars − SGD 680 claims approved"). This list used to
 * change shape by row: a category with a sub-limit reported what was LEFT
 * ("SGD 120 left / of SGD 300") while one without reported what was CLAIMED
 * ("SGD 500 claimed"). Two adjacent rows answering different questions, and the
 * total stated directly above them derivable from neither — a reader looking at
 * 500 and 120 against a stated 680 has to notice the second row is a remainder
 * and do 300 − 120 in their head to find the missing 180. What's left is still
 * carried, as the secondary line, where it belongs to the cap beside it.
 *
 * The claimed figure is CAPPED at the sub-limit (`drawnAgainst`'s rule): the
 * bar below it is already capped, so an uncapped caption would contradict its
 * own fullness. Where an acknowledged over-limit approval makes that bite, the
 * row is short of the ledger's raw total by the overage — the full approved
 * amount is on the claim record and in the reports, which is where an override
 * is reconciled. */
/** One category's position, as a SINGLE non-wrapping line.
 *
 * It was a 160px-wide stack — a figure, a remainder, a mini bar — which on a
 * wide card left the name and its numbers at opposite ends of an empty row and
 * still wrapped "SGD 180 of SGD 300 claimed" onto two lines inside its own
 * column. A category is one fact about one benefit; it gets one row, and the
 * row does not break mid-phrase.
 *
 * The per-category bar is gone with it. The wallet keeps ONE bar, at the top,
 * because that is the figure a broker scans for; a 160px bar under a wrapped
 * caption measures a sub-limit the caption has already stated exactly.
 *
 * **Every row leads with what was CLAIMED**, which is the term the ledger sums
 * ("SGD 2,680 flex dollars − SGD 680 claims approved"). This list used to
 * change shape by row: a category with a sub-limit reported what was LEFT while
 * one without reported what was CLAIMED — two adjacent rows answering different
 * questions, with the total above derivable from neither. A reader seeing 500
 * and "120 left" against a stated 680 had to notice the second was a remainder
 * and compute 300 − 120 to find the missing 180.
 *
 * The claimed figure is CAPPED at the sub-limit (`drawnAgainst`'s rule). Where
 * an acknowledged over-limit approval makes that bite, the row is short of the
 * ledger's raw total by the overage — the full approved amount is on the claim
 * record and in the reports, which is where an override is reconciled. */
function CategoryPosition({
  row,
  currency,
}: {
  row: CategoryRow;
  currency: string | null;
}) {
  const use = row.use;
  const capped =
    use && row.subLimit != null
      ? Math.min(use.approved, row.subLimit)
      : (use?.approved ?? 0);

  return (
    <span className="shrink-0 whitespace-nowrap text-xs tabular-nums text-muted-foreground">
      {/* Gated on something HAVING been claimed, not merely on a cap existing.
          A capped category with nothing settled rendered "SGD 0 of SGD 250
          claimed · SGD 250 left" — two figures that are only ever zero and the
          cap restated as its own remainder. It is the cap, and nothing else. */}
      {capped > 0 && row.subLimit != null && use?.remaining != null ? (
        <>
          <span className="font-medium text-foreground">
            {formatWallet(capped, currency)} of{" "}
            {formatWallet(row.subLimit, currency)}
          </span>{" "}
          claimed · {formatWallet(use.remaining, currency)} left
        </>
      ) : /* Approved only. "SGD 0 claimed" beside a pending figure states a
           number that isn't a fact about anything — the pending term is the
           whole story for a category with nothing settled yet. */
      capped > 0 ? (
        <>
          <span className="font-medium text-foreground">
            {formatWallet(capped, currency)}
          </span>{" "}
          claimed
        </>
      ) : row.subLimit != null ? (
        <>{formatWallet(row.subLimit, currency)} sub-limit</>
      ) : null}
      {use && use.pending > 0 && (
        <span className="text-warn">
          {capped > 0 || row.subLimit != null ? " · " : ""}
          <PendingSwatch />
          {formatWallet(use.pending, currency)} pending
        </span>
      )}
    </span>
  );
}

function Notice({ children }: { children: React.ReactNode }) {
  return (
    <div className="flex items-start gap-1.5 rounded-md border border-warn/40 bg-warn-soft/30 p-2 text-2xs text-foreground">
      <AlertTriangle className="mt-0.5 size-3.5 shrink-0 text-warn" aria-hidden />
      <span>{children}</span>
    </div>
  );
}

export function FlexPanel({
  flex,
  usage,
}: {
  flex: FlexCoverageLine;
  usage?: FlexUtilization | null;
}) {
  const [showTags, setShowTags] = useState(false);
  const currency = flex.currency ?? usage?.currency ?? null;

  const wallet = flex.wallet_amount ?? usage?.wallet_amount ?? null;
  const priceTags = flex.price_tags_total ?? usage?.price_tags_total ?? null;
  const balance = flex.flex_balance ?? usage?.flex_balance ?? null;
  const approved = usage?.approved ?? 0;
  const pending = usage?.pending ?? 0;
  const available = usage?.available ?? balance ?? wallet;

  const tagLines = flex.price_tag_lines.filter(
    (l) => l.price_tag != null && l.price_tag !== 0,
  );
  const hasLeave = flex.leave_flex_amount != null && flex.leave_flex_amount !== 0;
  const categories = mergeCategories(flex, usage);

  // Only the terms that actually moved — anything else is a line of zeroes
  // restating the wallet. The set must RECONCILE, so the leave trade is one of
  // them: `flex_pricing_resolver` computes `balance = wallet − price_tags +
  // leave_flex_amount` (signed: buying spends, selling credits), and printing
  // only tags and claims left the stated terms not adding up to the stated
  // total on any member who traded a day.
  const drawn: { label: string; value: number; add: boolean }[] = [];
  if (priceTags) {
    drawn.push({ label: "coverage price tags", value: priceTags, add: false });
  }
  if (hasLeave) {
    drawn.push({
      label: `leave ${flex.leave_action === "buy" ? "bought" : "sold"}`,
      value: Math.abs(flex.leave_flex_amount!),
      add: flex.leave_flex_amount! > 0,
    });
  }
  if (approved) {
    drawn.push({ label: "claims approved", value: approved, add: false });
  }

  // Overdrawn is a property of what is LEFT, and `available` already falls back
  // through `flex_balance` to `wallet`, so it is the only figure the headline
  // and the equation total ever show.
  //
  // It now means ONE thing: the member holds elected cover priced above their
  // allowance. Claims can no longer produce it — a flex wallet pays up to the
  // limit, so `utilization` floors the claims half at zero (and the leaver
  // sheets split identically). Don't "restore" a negative from claims here; it
  // would be an indication of something that cannot happen.
  const shortfall = available != null && available < 0;
  // Magnitude + the word "overdrawn"; "SGD -300 overdrawn" states the sign
  // twice and "SGD -300 available" is not a quantity anyone has.
  const headline = available != null ? Math.abs(available) : null;

  // **The ledger prints its TERMS only — the total is the headline.** It used
  // to close with "= SGD 2,000 left", which is the same number, in the same
  // panel, six pixels below the figure already labelled "available": the
  // restatement this panel exists to remove.
  //
  // Dropping it also retires a whole correctness problem rather than managing
  // one. The terms stop summing to the total in exactly one case — claims
  // already reimbursed exceed the allowance, which pro-ration can produce by
  // shrinking a leaver's allowance below what they had drawn — and `available`
  // floors at 0 there, so the panel used to need a `reconciles` check to avoid
  // printing "500 − 200 − 700 = 0", an equals sign in front of arithmetic false
  // on its face. A line that never claims to be a sum cannot make that claim
  // falsely. Every term is still on screen and each is true.

  return (
    <section className="rounded-lg border border-border bg-card p-4">
      <div className="flex flex-wrap items-start justify-between gap-x-4 gap-y-2">
        <div className="min-w-0">
          <div className="flex items-center gap-2">
            <Wallet className="size-4 shrink-0 text-muted-foreground" aria-hidden />
            <h3 className="text-sm font-semibold text-foreground">
              Flexible benefits
            </h3>
            {flex.tier_name && <Badge variant="outline">{flex.tier_name}</Badge>}
          </div>
          <p className="mt-1 text-xs text-muted-foreground">
            {[
              flex.scheme_name,
              familyLabel(flex.family_status),
              flex.employer_pct != null || flex.employee_pct != null
                ? `${flex.employer_pct ?? "—"}% employer / ${flex.employee_pct ?? "—"}% employee`
                : null,
              flex.source ? SOURCE_LABEL[flex.source] : null,
            ]
              .filter(Boolean)
              .join(" · ")}
          </p>
        </div>
        <div className="shrink-0 text-right">
          <div
            className={cn(
              "text-lg font-semibold tabular-nums",
              shortfall ? "text-error" : "text-foreground",
            )}
          >
            {formatWallet(headline, currency)}
          </div>
          <div className="flex items-center justify-end gap-1 text-2xs text-muted-foreground">
            {usage ? (shortfall ? "overdrawn" : "available") : "annual wallet"}
            <InfoHint>
              The flex dollars, less the price tags of the cover this member
              holds and any claim already approved. Pending claims are shown
              separately and never deducted.
            </InfoHint>
          </div>
        </div>
      </div>

      {(approved > 0 || pending > 0) && wallet != null && (
        <UtilizationBar
          limit={balance ?? wallet}
          approved={approved}
          pending={pending}
          className="mt-3"
        />
      )}

      {/* The ledger, as one sentence of arithmetic rather than a grid of tiles
       * repeating figures that are already above. */}
      <p className="mt-3 flex flex-wrap items-baseline gap-x-1.5 text-xs text-muted-foreground">
        {wallet == null ? (
          <span>
            No wallet assigned yet — confirm and assign the Flex scheme to see a
            balance.
          </span>
        ) : drawn.length === 0 ? (
          // The headline already states the figure and calls it available;
          // repeating it in the sentence beneath is the restatement this panel
          // exists to stop.
          <span>No flex dollars have been drawn yet.</span>
        ) : (
          <>
            <span className="tabular-nums">
              {formatWallet(wallet, currency)} flex dollars
            </span>
            {drawn.map((d) => (
              <span key={d.label} className="tabular-nums">
                <span aria-hidden className="mr-1 text-subtle">
                  {d.add ? "+" : "−"}
                </span>
                {formatWallet(d.value, currency)} {d.label}
              </span>
            ))}
          </>
        )}
        {pending > 0 && (
          <span className="tabular-nums text-warn">
            <PendingSwatch />
            {formatWallet(pending, currency)} pending, not deducted
          </span>
        )}
      </p>
      {/* Where the allowance above came from. Rendered only when it was
        * pro-rated, so a full-year member sees nothing — and never folded into
        * the ledger line, because the fraction is not another movement, it is
        * the derivation of that line's FIRST term. A reduced allowance with
        * nothing explaining it is the number members dispute. */}
      {flex.proration && (
        <p className="mt-1 text-xs text-muted-foreground">
          <span className="tabular-nums">
            {formatWallet(flex.proration.full_amount, currency)}
          </span>{" "}
          annual, pro-rated {flex.proration.note} of cover
        </p>
      )}

      {(tagLines.length > 0 || hasLeave) && (
        <div className="mt-2">
          <button
            type="button"
            onClick={() => setShowTags((s) => !s)}
            aria-expanded={showTags}
            className="flex items-center gap-1 text-2xs font-medium text-muted-foreground hover:text-foreground hover:underline"
          >
            <ChevronRight
              aria-hidden
              className={cn("size-3 transition-transform duration-150", showTags && "rotate-90")}
            />
            {/* "What the WALLET paid for", not "what the price tags paid for":
              * a bought/sold leave day is in this list too, and it is not a
              * price tag. */}
            What the wallet paid for ({tagLines.length + (hasLeave ? 1 : 0)})
          </button>
          {showTags && (
            <div className="mt-1.5 flex flex-col gap-0.5 border-t border-border pt-1.5">
              {tagLines.map((l, i) => (
                <div
                  key={`${l.product_code}-${l.plan_code ?? i}`}
                  className="flex items-baseline justify-between gap-3 text-xs text-muted-foreground"
                >
                  <span>
                    {l.product_code}
                    {l.plan_code ? ` · Plan ${l.plan_code}` : ""}
                  </span>
                  <span className="tabular-nums text-foreground">
                    {formatWallet(l.price_tag, currency)}
                  </span>
                </div>
              ))}
              {hasLeave && (
                <div className="flex items-baseline justify-between gap-3 text-xs text-muted-foreground">
                  <span>
                    Leave {flex.leave_action === "buy" ? "bought" : "sold"}
                    {flex.leave_days != null
                      ? ` (${flex.leave_days} ${flex.leave_days === 1 ? "day" : "days"})`
                      : ""}
                  </span>
                  <span
                    className={cn(
                      "tabular-nums",
                      flex.leave_flex_amount! < 0 ? "text-error" : "text-good",
                    )}
                  >
                    {flex.leave_flex_amount! < 0 ? "−" : "+"}
                    {formatWallet(Math.abs(flex.leave_flex_amount!), currency)}
                  </span>
                </div>
              )}
            </div>
          )}
        </div>
      )}

      {categories.length > 0 && (
        <div className="mt-3 border-t border-border pt-3">
          <SectionLabel as="h4" className="mb-1.5">
            Claimable benefits
          </SectionLabel>
          {/* One row per category, baseline-aligned: the NAME may wrap (it is
              prose and can be long), the figures may not (`whitespace-nowrap`
              in `CategoryPosition`) — a phrase like "SGD 180 of SGD 300" broken
              across two lines reads as two amounts. `items-baseline` sits the
              two halves on one line instead of top-aligning a wrapped name
              against a single-line figure. */}
          <div className="flex flex-col gap-1">
            {categories.map((row) => (
              <div
                key={row.name}
                className="flex items-baseline justify-between gap-6"
              >
                <span className="flex min-w-0 items-baseline gap-1.5 text-sm">
                  {row.claimable ? (
                    <CheckCircle2
                      className="size-3.5 shrink-0 translate-y-0.5 text-good"
                      aria-label="Claimable"
                    />
                  ) : (
                    <XCircle
                      className="size-3.5 shrink-0 translate-y-0.5 text-muted-foreground"
                      aria-label="Not claimable"
                    />
                  )}
                  <span className="min-w-0 break-words text-foreground">
                    {row.name}
                    {row.note && (
                      <span className="ml-1.5 text-xs text-muted-foreground">
                        {row.note}
                      </span>
                    )}
                  </span>
                </span>
                <CategoryPosition row={row} currency={currency} />
              </div>
            ))}
          </div>
        </div>
      )}

      {(flex.assignment_stale || !flex.price_age_known) && (
        <div className="mt-3 flex flex-col gap-2">
          {flex.assignment_stale && (
            <Notice>
              The Flex scheme changed after this wallet was assigned, so the
              claimable benefits above may be out of date. Re-assign wallets from
              the Flex tab to refresh.
            </Notice>
          )}
          {!flex.price_age_known && (
            <Notice>
              This member's age couldn't be determined (no date of birth on
              file), so price tags weren't applied — the balance may overstate
              what's available.
            </Notice>
          )}
        </div>
      )}
    </section>
  );
}
