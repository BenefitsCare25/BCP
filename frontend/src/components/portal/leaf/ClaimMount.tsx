/** The member's claims, as a LEDGER.
 *
 * "What happened to my claim?" is the question members come back for, so the
 * state is struck and everything else is subordinate to it.
 *
 * **Why a ledger and not a stack of cards.** Every claim carries the same four
 * facts, so a pane per claim spent ~105px repeating the shape of the one above
 * it — and two of those lines were furniture: the words "Amount claimed" over a
 * column in which every figure is an amount, and "1 document attached" on a
 * line of its own. A year of claiming was a featureless wall. Rows inside one
 * pane per month put the figures in a single column you can run an eye down,
 * and cost ~64px each.
 *
 * Three things carry the structure, and each answers a question the old list
 * could not:
 *
 *   the FILTER STRIP  — which stage are we looking at? Four FIXED tabs with
 *                       bare labels: counts were dropped by decision, because
 *                       a tab whose number moves under the reader's finger
 *                       reads as a different tab. It is portalled into the
 *                       page's heading row (see `HeadRail`) exactly as
 *                       Coverage's tabs are — one strip, one place.
 *   the PENDING pin   — which of these is waiting on ME? A draft and a
 *                       "more info needed" are the only two states where the
 *                       member has work to do, and in date order they sat
 *                       wherever the calendar put them.
 *   the MONTH heading — which receipt was this? Grouped and sorted on the
 *                       INCURRED date, not `created_at`: a member remembers the
 *                       visit, not the evening they got round to filing it.
 *
 * **Everything here is shared with the broker's employee-view preview**
 * (`components/operations/PortalFrame`), which is why the filter lives in
 * component state rather than in a route search param — the preview has no
 * route of its own to hold one, and a filter that worked on one surface and
 * not the other would be exactly the divergence that page exists to prevent.
 * The floating "Make a claim" pill is the one thing the two surfaces do NOT
 * share; it is `position: fixed` and belongs to the member's route, never to
 * this component. See `routes/portal/claims/index.tsx`.
 */
import { useState } from "react";
import { Link } from "@tanstack/react-router";
import type { PortalClaim } from "@/api/portal";
import { cn } from "@/lib/cn";
import { glassSurface } from "./Mount";
import { HeadRail } from "./HeadRail";
import { Money, currencySymbol, moneyText } from "./Figure";
import { ClaimStrike } from "./Strike";
import { formatDay, monthLabel } from "./date";

/** What the claim is FOR, in the member's words. */
export function claimTitle(claim: PortalClaim): string {
  if (claim.claim_kind === "flex") {
    return claim.flex_category_name || "Flexible benefit";
  }
  return claim.claim_type || claim.product_code || "Claim";
}

/** Who, where, when and how much evidence — the facts that let a member
 * recognise which receipt this was, on one line and without repeating the
 * title. The document count is part of this line rather than a row of its own:
 * it matters when it is zero or when a claim was sent back for more, and never
 * enough to spend a line on. */
function claimContext(claim: PortalClaim): string {
  const docs = claim.documents.length;
  return [
    claim.dependant_name ? `For ${claim.dependant_name}` : null,
    claim.provider_name,
    formatDay(claim.incurred_date),
    docs > 0 ? `${docs} document${docs === 1 ? "" : "s"}` : null,
  ]
    .filter(Boolean)
    .join(" · ");
}

/** The four readings of a claim, from the member's side.
 *
 * `attention` is "you have to do something", NOT "it is unfinished" — which is
 * why a draft sits beside a claim sent back for more information and why
 * everything the team is working on collapses into one `review`. Unknown states
 * fall to `review`, mirroring `Strike`'s own fallback: a state we cannot read
 * must never be filed as decided.
 *
 * `closed` — rejected, and anything cancelled or withdrawn — is a bucket with
 * NO TAB, by decision: those claims are reachable under All and nowhere else. A
 * standing Rejected tab announces an outcome most members never have. It still
 * has to be its own bucket rather than falling through, or a rejected claim
 * would be filed as `review` and a member would read a settled refusal as
 * something we are still working on. Cancel-like states are listed here for the
 * same reason — the API has none today, and the day one arrives it must not
 * appear under "In Review". */
type View = "all" | "attention" | "review" | "approved" | "closed";
type Bucket = Exclude<View, "all">;

const ATTENTION = new Set(["draft", "needs_info"]);
const CLOSED = new Set(["rejected", "cancelled", "canceled", "withdrawn"]);

function bucketOf(status: string): Bucket {
  if (ATTENTION.has(status)) return "attention";
  if (status === "approved") return "approved";
  if (CLOSED.has(status)) return "closed";
  return "review";
}

const BUCKET_LABEL: Record<Bucket, string> = {
  attention: "Pending Doc",
  review: "In Review",
  approved: "Approved",
  closed: "Closed",
};

/** What a tab says when it holds nothing.
 *
 * A tab that is always present WILL be opened empty, and an empty tab with no
 * sentence in it reads as a page that failed to load. This is also why the
 * strip no longer silently falls back to "All" when the current bucket empties
 * — a filter that moves on its own is worse than one that says "nothing here". */
const EMPTY_VIEW: Record<Bucket, string> = {
  attention: "Nothing is waiting on you right now.",
  review: "Nothing is with us for review right now.",
  approved: "No claims have been approved yet.",
  // `closed` has no tab, so this is unreachable today — it is here because the
  // map is keyed on the bucket type, which is what makes adding a tab later a
  // one-line change that cannot ship without its empty state.
  closed: "No claims have been closed.",
};

/** The calendar day a claim belongs to. Missing dates sort last rather than
 * first — an undated row at the head of the ledger reads as the newest claim. */
function dayKey(claim: PortalClaim): string {
  const [datePart] = String(claim.incurred_date ?? "").split(/[ T]/);
  return /^\d{4}-\d{2}-\d{2}$/.test(datePart) ? datePart : "";
}

function byRecency(a: PortalClaim, b: PortalClaim): number {
  const day = dayKey(b).localeCompare(dayKey(a));
  if (day !== 0) return day;
  return String(b.created_at).localeCompare(String(a.created_at));
}

type Group = { key: string; label: string; items: PortalClaim[] };

/** Needs-you first, then one group per month.
 *
 * The month runs merge only when CONSECUTIVE, which is safe precisely because
 * the rows are sorted by the same date they are grouped on — grouping a
 * `created_at`-ordered list by its incurred month emits the same month twice. */
function buildGroups(rows: PortalClaim[]): Group[] {
  const sorted = [...rows].sort(byRecency);
  const groups: Group[] = [];

  const pinned = sorted.filter((c) => bucketOf(c.status) === "attention");
  if (pinned.length > 0) {
    groups.push({ key: "attention", label: BUCKET_LABEL.attention, items: pinned });
  }

  for (const claim of sorted) {
    if (bucketOf(claim.status) === "attention") continue;
    const key = dayKey(claim).slice(0, 7) || "undated";
    const last = groups[groups.length - 1];
    if (last && last.key === key) {
      last.items.push(claim);
    } else {
      groups.push({
        key,
        label: monthLabel(claim.incurred_date) || "No date",
        items: [claim],
      });
    }
  }
  return groups;
}

function ClaimRow({
  claim,
  interactive,
}: {
  claim: PortalClaim;
  interactive?: boolean;
}) {
  // An approved figure is the OUTCOME, so it takes the headline and the
  // requested one moves beneath it — a member whose claim was partly approved
  // needs both numbers to see the difference, and one of them has to be the
  // answer. Equal figures print once: "of S$88.40 claimed" under S$88.40 is a
  // difference being announced where there is none.
  const approved = claim.amount_approved != null;
  const partial = approved && claim.amount_approved !== claim.amount_claimed;

  const body = (
    <div className="flex flex-col gap-1.5 sm:flex-row sm:items-start sm:justify-between sm:gap-6">
      <div className="min-w-0">
        <h3 className="text-md font-semibold leading-5 text-record">
          {claimTitle(claim)}
        </h3>
        <p className="mt-0.5 text-row text-label">{claimContext(claim)}</p>
      </div>
      {/* On a phone the figure and the state share one baseline row beneath the
          title — the title needs the full width there, and a right-hand column
          at 390px leaves ~130px for a wrapped claim type. From `sm` up they
          stack into the right column, which is what puts every amount in the
          ledger on one vertical line. */}
      <div className="flex items-baseline justify-between gap-3 sm:shrink-0 sm:flex-col sm:items-end sm:gap-1">
        <p className="sm:text-right">
          {/* The column's meaning is carried by its position, which a screen
              reader does not have. */}
          <span className="sr-only">
            {approved ? "Approved" : "Amount claimed"}{" "}
          </span>
          <Money
            value={approved ? claim.amount_approved : claim.amount_claimed}
            currency={claim.currency}
            emphasis={approved ? "strong" : "normal"}
          />
          {partial && (
            <span className="block text-2xs text-label">
              of {currencySymbol(claim.currency)}
              {moneyText(claim.amount_claimed)} claimed
            </span>
          )}
        </p>
        <ClaimStrike status={claim.status} />
      </div>
    </div>
  );

  // The padding is on the LINK, not the `li`, so the whole row is the target
  // rather than a text-shaped hole in the middle of one. Inert rows (the broker
  // preview) carry it themselves and take no hover: the response is the
  // affordance, and a row that responds and then does nothing breaks the
  // promise the surface made.
  const pad = "block rounded-control px-3 py-3 sm:px-3.5";
  if (!interactive) return <li className={pad}>{body}</li>;
  return (
    <li>
      <Link
        to="/portal/claims/$claimId"
        params={{ claimId: claim.id }}
        className={cn(
          pad,
          "leaf-focus transition-colors duration-200 ease-leaf hover:bg-shade/70",
        )}
      >
        {body}
      </Link>
    </li>
  );
}

/** The state filter.
 *
 * Neutral, never terracotta: it PICKS a view rather than doing anything to the
 * member's record (leaf/Action.tsx's Do-vs-Pick Rule), and the current one is
 * marked the way the nav, the dock and the coverage tabs mark it — a `bg-shade`
 * pill with ink text.
 *
 * **The tabs are FIXED, not derived from the data.** All / Pending Doc / In
 * Review / Approved are the stages every claim passes through, so the strip
 * reads the same on every visit and a tab does not appear and disappear
 * underneath a member's finger as their claims move. Rejected is the exception
 * and joins only when there is one: a permanently displayed Rejected tab
 * announces an outcome most members never have.
 *
 * **One row on a phone**, which is what the tight `px-1.5` buys: at the `px-3.5`
 * the chips carry from `sm` up, four labels overflow a 390px pane by a few
 * pixels and "Approved" drops onto a line of its own — a two-line strip with
 * one orphan on it reads as a mistake. Padding is the right thing to give up
 * because the 44px hit area comes from `min-h-11`, not from the horizontal
 * inset, and `flex-auto` hands the slack straight back to the chips, so they
 * still fill the row edge to edge. Measured: one row from 358px up.
 *
 * Below that (a 320px screen) the four labels total more than the pane is wide
 * at any padding, so `flex-wrap` stays as the safety valve — shrinking the type
 * to force one line would cost legibility on the smallest screen there is, and
 * nothing a member needs may be reachable only by horizontal scroll. Hence the
 * tile radius below `sm`: a wrapped pill looks broken, a wrapped tile does not.
 *
 * The `lg:` tightening is the same decision as the rail's breakpoint: `lg` is
 * exactly where the strip moves INTO the heading row, and a full-size band
 * seated beside a name and a date range reads as a second header rather than
 * as one control within one. */
const FIXED_TABS: Bucket[] = ["attention", "review", "approved"];

function FilterStrip({
  active,
  onPick,
}: {
  active: View;
  onPick: (view: View) => void;
}) {
  const options: { key: View; label: string }[] = [
    { key: "all", label: "All" },
    ...FIXED_TABS.map((k) => ({ key: k as View, label: BUCKET_LABEL[k] })),
  ];

  return (
    <div
      role="group"
      aria-label="Filter claims"
      className={cn(
        glassSurface,
        "flex flex-wrap gap-0.5 rounded-tile p-1 sm:inline-flex sm:gap-1 sm:rounded-pill sm:p-1.5 lg:p-1",
      )}
    >
      {options.map((option) => {
        const on = option.key === active;
        return (
          <button
            key={option.key}
            type="button"
            aria-pressed={on}
            onClick={() => onPick(option.key)}
            className={cn(
              "leaf-focus min-h-11 flex-auto whitespace-nowrap rounded-pill px-1.5 text-row sm:flex-none sm:px-5 lg:px-4",
              "transition-colors duration-200 ease-leaf",
              on
                ? "bg-shade font-semibold text-record"
                : "text-label hover:bg-shade/60 hover:text-record",
            )}
          >
            {option.label}
          </button>
        );
      })}
    </div>
  );
}

export function ClaimList({
  items,
  interactive = false,
  total,
}: {
  items: PortalClaim[];
  /** Omitted on the broker preview, where rows are inert. */
  interactive?: boolean;
  /** The server's count. The request asks for the whole year (200, the API's
   * cap), so this only differs on a member who has claimed more than that —
   * and then the ledger is a window onto their year, which it has to say. */
  total?: number;
}) {
  const [active, setActive] = useState<View>("all");

  // Below three claims the strip is four controls for a list you can already
  // see whole.
  const showFilter = items.length >= 3;

  const rows =
    active === "all" ? items : items.filter((c) => bucketOf(c.status) === active);
  const groups = buildGroups(rows);
  // A single month heading over the only group on the page names nothing the
  // dates in it don't. "Pending Doc" always earns its heading — it is a claim
  // about the rows, not a restatement of them.
  //
  // Suppressed VISUALLY, never removed: each claim title is an `h3`, so
  // dropping the `h2` would take a member with one month of claims from the
  // shell's `h1` straight to `h3` and put every claim a level out of reach of
  // heading navigation.
  const showHeadings = groups.length > 1 || groups[0]?.key === "attention";

  return (
    <div className="space-y-4">
      {/* Into the centre of the page's heading row, beside the member's name
          and the benefit year — the same seam Coverage's tab strip uses, and
          for the same reason: a control that scopes the whole page belongs in
          that page's header, not in a band of its own above the content.
          `HeadRail` renders it in place wherever there is no rail (below `lg`,
          and in the broker's employee-view preview), so the strip exists
          exactly once on every surface. */}
      {showFilter && (
        <HeadRail>
          <FilterStrip active={active} onPick={setActive} />
        </HeadRail>
      )}

      {/* A fixed tab WILL be opened empty. Saying which view is empty beats an
          empty column, which reads as a page that failed to load. */}
      {groups.length === 0 && active !== "all" && (
        <p
          className={cn(
            glassSurface,
            "leaf-rise rounded-tile px-4 py-5 text-row text-label sm:px-5",
          )}
        >
          {EMPTY_VIEW[active]}
        </p>
      )}

      {groups.map((group) => (
        // `leaf-rise` on the SECTION, not the pane: the stagger is a
        // `:nth-of-type` rule over siblings, so it has to sit on the repeated
        // element for the second group to arrive after the first.
        <section key={group.key} className="leaf-rise space-y-1.5">
          <h2 className={showHeadings ? "leaf-label px-1" : "sr-only"}>
            {group.label}
          </h2>
          <ul
            className={cn(
              glassSurface,
              "divide-y divide-hairline/75 rounded-tile p-1.5 sm:p-2",
            )}
          >
            {group.items.map((claim) => (
              <ClaimRow key={claim.id} claim={claim} interactive={interactive} />
            ))}
          </ul>
        </section>
      ))}

      {/* Only under the unfiltered ledger. Under a filtered one it would state
          a third number — neither the rows on screen nor the record — beneath
          a list of rows it does not describe. */}
      {active === "all" && total !== undefined && total > items.length && (
        <p className="px-1 text-row text-label">
          Showing your {items.length} most recent claims of {total}.
        </p>
      )}
    </div>
  );
}
