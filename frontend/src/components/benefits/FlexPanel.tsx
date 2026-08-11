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
 * It is one panel here, and every figure on it has the SAME shape: **used /
 * total**, for the wallet in the heading and for each claimable benefit under
 * it, so a row can be checked against the wallet without translating between
 * "claimed", "left" and "available". A figure that is only ever zero is not
 * printed — this pane used to render eight product lines of "SGD 0" and a
 * "Balance" equal to the wallet directly above it.
 */
import { useState } from "react";
import { useNavigate } from "@tanstack/react-router";
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
 * **Every row is USED / TOTAL**, the same shape as the headline, so the column
 * reads down as fractions of one kind and each row can be checked against the
 * wallet above it. This list used to change shape by row — a capped category
 * reported what was LEFT while an uncapped one reported what was CLAIMED, two
 * adjacent rows answering different questions with the wallet total derivable
 * from neither.
 *
 * The remainder is deliberately NOT printed beside the fraction: it is the
 * subtraction of the two figures already on the line.
 *
 * The used figure is CAPPED at the sub-limit (`drawnAgainst`'s rule). Where an
 * acknowledged over-limit approval makes that bite, the row understates by the
 * overage — the full approved amount is on the claim record and in the reports,
 * which is where an override is reconciled. */
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
      {row.subLimit != null ? (
        <>
          <span className="font-medium text-foreground">
            {formatWallet(capped, currency)}
          </span>
          <span className="text-subtle"> / </span>
          {formatWallet(row.subLimit, currency)}
        </>
      ) : capped > 0 ? (
        /* **No slash without a cap.** A capless category draws against the
           WALLET, so its only honest denominator is the whole wallet — which
           would print an identical "/ SGD 2,680" on every such row and, where
           a scheme has exactly one (CDL's real shape), restate the headline
           verbatim one line below it. The slash appears where there is
           something to divide by, and nowhere else. */
        <>
          <span className="font-medium text-foreground">
            {formatWallet(capped, currency)}
          </span>{" "}
          used
        </>
      ) : null}
      {use && use.pending > 0 && (
        <span className="text-warn">
          {capped > 0 || row.subLimit != null ? " · " : ""}
          <PendingSwatch />
          {formatWallet(use.pending, currency)} pending
        </span>
      )}
      {/* Nothing used, no cap, nothing pending: the row is the benefit's NAME
          and that is the whole fact. Printing "SGD 0" against it would be the
          only-ever-zero figure this panel keeps removing. */}
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
  employeeId,
}: {
  flex: FlexCoverageLine;
  usage?: FlexUtilization | null;
  /** Whose panel this is — carried to the queue so opening the pending figure
   *  lands on THEIR claims. Optional: a surface that has no employee in hand
   *  still gets the panel, it just opens the queue unfiltered. */
  employeeId?: string;
}) {
  const [showTags, setShowTags] = useState(false);
  const navigate = useNavigate();
  const currency = flex.currency ?? usage?.currency ?? null;

  // The claims BEHIND the pending figure, so it can be opened. SERVED with the
  // figure (`utilization._flex_utilization` collects them as it sums), never a
  // second query filtered by a status list copied into TypeScript: "pending" is
  // defined server-side by SUBTRACTION from the settled statuses, so a mirror
  // would start offering a different set the day a status is added.
  const pendingClaimIds = usage?.pending_claim_ids ?? [];
  const openPending = () =>
    navigate({
      to: "/claims/review",
      // One claim opens ITS sheet; several open the queue FILTERED TO THIS
      // MEMBER, because picking one of them here would be an arbitrary choice
      // presented as the answer — but landing on the whole firm's queue is no
      // better. The figure says "2 claims"; the destination has to be those
      // claims, and on a 467-member client an unfiltered queue buried them
      // among hundreds of rows the broker did not ask for.
      search:
        pendingClaimIds.length === 1
          ? { tab: "queue", claim: pendingClaimIds[0] }
          : { tab: "queue", employee: employeeId },
    });

  const wallet = flex.wallet_amount ?? usage?.wallet_amount ?? null;
  const balance = flex.flex_balance ?? usage?.flex_balance ?? null;
  const approved = usage?.approved ?? 0;
  const pending = usage?.pending ?? 0;
  // Excluded from `pending` by the server: their policy-currency value is not
  // yet established (`utilization.py`). Counted, never guessed at.
  const pendingUnconverted = usage?.pending_unconverted ?? 0;
  const available = usage?.available ?? balance ?? wallet;

  const tagLines = flex.price_tag_lines.filter(
    (l) => l.price_tag != null && l.price_tag !== 0,
  );
  const hasLeave = flex.leave_flex_amount != null && flex.leave_flex_amount !== 0;
  const categories = mergeCategories(flex, usage);

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
  // What has been DRAWN, as the numerator of the headline fraction. Derived
  // from `available` rather than summed from `drawn` so the headline and the
  // "What the wallet paid for" list cannot disagree: `available` is the one
  // figure `flex_pricing_resolver` and `utilization` both resolve.
  const used =
    wallet != null && available != null
      ? Math.round((wallet - available) * 100) / 100
      : null;

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
        {/* **USED / TOTAL**, the same shape as every category row beneath it,
            so the wallet and its parts are read the same way and each row can
            be checked against this one.

            `used` is everything DRAWN, not just claims: `available` already
            nets off the price tags of elected cover and any leave traded, so
            `wallet − available` is the whole draw. Sizing this on claims alone
            would print "SGD 0 / SGD 2,680" for a member whose wallet is fully
            committed to upgrades.
            Overdrawn needs no separate word here — used exceeding total says it
            on its face — but it keeps the error colour, because it is the state
            the enrolment guard and the bulk `flex_overdraft` warning exist
            for. */}
        <div className="shrink-0 text-right">
          <div
            className={cn(
              "text-lg font-semibold tabular-nums",
              shortfall ? "text-error" : "text-foreground",
            )}
          >
            {used != null && wallet != null ? (
              <>
                {formatWallet(used, currency)}
                <span className="font-normal text-subtle"> / </span>
                {formatWallet(wallet, currency)}
              </>
            ) : (
              formatWallet(wallet, currency)
            )}
          </div>
          <div className="flex items-center justify-end gap-1 text-2xs text-muted-foreground">
            {pendingClaimIds.length > 0 ? (
              /* **The pending figure is the way IN to the claims behind it.**
                 It used to read "available" — a word for a number stated three
                 inches away. What a broker does with a pending total is open
                 it, so it opens: one claim goes straight to its sheet, several
                 to the queue. */
              <button
                type="button"
                onClick={openPending}
                className="flex items-center gap-1 text-warn hover:underline"
              >
                <PendingSwatch />
                {formatWallet(pending, currency)} pending
                {pendingClaimIds.length > 1 && ` · ${pendingClaimIds.length} claims`}
                {/* Foreign claims with no resolved SGD value are NOT in the
                    figure to their left — a policy-currency total cannot
                    absorb a foreign amount. They ARE in `pendingClaimIds`, so
                    the count beside it would otherwise be the only hint that
                    the two disagree. */}
                {pendingUnconverted > 0 &&
                  ` · ${pendingUnconverted} awaiting conversion`}
                <ChevronRight aria-hidden className="size-3" />
              </button>
            ) : (
              <>{usage ? "used" : "annual wallet"}</>
            )}
            <InfoHint>
              What this member has drawn — the price tags of the cover they hold
              plus any claim already approved — against their flex dollars.
              Pending claims are never deducted.
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

      {/* **No ledger line.** It spelled out "SGD 2,680 flex dollars − SGD 680
        * claims approved · SGD 90 pending, not deducted" — three figures that
        * are now all on screen already: the wallet and the draw are the
        * headline fraction, and the pending total is the link beneath it. What
        * the draw was SPENT ON is the "What the wallet paid for" disclosure
        * below, which is where a broker who needs the itemisation looks. */}
      {wallet == null && (
        <p className="mt-3 text-xs text-muted-foreground">
          No wallet assigned yet — confirm and assign the Flex scheme to see a
          balance.
        </p>
      )}
      {/* Where the flex dollars above came from. Rendered only when they were
        * pro-rated, so a full-year member sees nothing. A reduced entitlement
        * with nothing explaining it is the number members dispute. */}
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
