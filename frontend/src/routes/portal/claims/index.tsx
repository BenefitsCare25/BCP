/** "My claims" — the member's claims for the current benefit year. */
import { Link } from "@tanstack/react-router";
import { FilePlus2, Loader2 } from "lucide-react";
import { usePortalClaimPages, usePortalMe } from "@/api/portal";
import { holds } from "@/components/portal/capabilities";
import { ClaimList } from "@/components/portal/leaf/ClaimMount";
import { LeafSkeleton } from "@/components/portal/leaf/LeafSkeleton";
import { Mount } from "@/components/portal/leaf/Mount";
import { actionClass } from "@/components/portal/leaf/Action";
import { PortalErrorState } from "@/components/portal/PortalErrorState";
import { isNotFoundError } from "@/lib/errors";
import { useDocumentTitle } from "@/lib/useDocumentTitle";
import { useCompany } from "@/components/portal/useCompany";

/** THE primary action of the member portal, and the page's one brand fill —
 * floating, centred, and present at every scroll position.
 *
 * It used to sit in the flow above the list, where on a desktop it was one pill
 * in an otherwise empty 1180px row, and on a long ledger it was gone by the
 * second screenful — a member who had just finished reading what happened to
 * last month's claim had to scroll back up to file this month's.
 *
 * Three details are load-bearing:
 *
 * 1. **It lives in the ROUTE, never in `ClaimList`.** The broker's employee-view
 *    preview renders that component inside a bounded frame, and a `fixed`
 *    element there would escape the frame and float over the broker's own app.
 *    The preview keeps its inline (disabled) pill.
 * 2. **The wrapper is `pointer-events-none` and the pill re-enables them.**
 *    Otherwise a full-width fixed strip sits over the bottom of the ledger and
 *    swallows clicks on the rows beneath it.
 * 3. **On a phone it clears the dock.** The dock is 64px of floating glass at
 *    `bottom-3` plus the home-bar inset, so this sits 96px up and carries the
 *    same inset — and the page grows enough bottom padding that the last row
 *    can always be scrolled clear of it. */
function MakeClaimAction() {
  const company = useCompany();
  return (
    <div
      className={
        "pointer-events-none fixed inset-x-0 z-30 flex justify-center px-4 " +
        "bottom-[calc(6rem_+_env(safe-area-inset-bottom))] sm:bottom-6"
      }
    >
      <Link
        to="/portal/$company/claims/new"
        params={{ company }}
        className={actionClass("primary", {
          className: "pointer-events-auto",
        })}
      >
        <FilePlus2 className="size-4" aria-hidden />
        Make a claim
      </Link>
    </div>
  );
}

/** The same measure the claim's own page uses.
 *
 * A ledger row is a term on the left and a figure on the right, and at the
 * shell's full 1024px that put ~500px of nothing between them — the eye has to
 * traverse it to pair a claim with its amount. Sharing the detail page's
 * `max-w-3xl` also means opening a claim expands the row in place instead of
 * reflowing the column it came from. */
const MEASURE = "mx-auto max-w-3xl";

export function PortalClaimsPage() {
  const company = useCompany();
  const claims = usePortalClaimPages();
  // The FOURTH entry point that closes on the served capability list, beside
  // the shell nav, the home mosaic and the broker's preview frame. A settling
  // leaver keeps this page — reading their claims and answering us is the whole
  // point of it — but not the right to start a new one, and the pill shipped
  // ungated: their own banner said the window had closed while the page's one
  // brand-filled action invited them through it, into a 403.
  const canClaim = holds(usePortalMe().data?.access.capabilities, "claim");
  useDocumentTitle("My claims");

  if (claims.isLoading) return <LeafSkeleton label="Loading your claims" />;

  // A 404 keeps the confident empty state below; anything else is a fetch
  // failure and must not read as "no claims yet".
  if (claims.isError && !isNotFoundError(claims.error)) {
    return <PortalErrorState onRetry={() => void claims.refetch()} />;
  }

  const rows = claims.data?.pages.flatMap((page) => page.items) ?? [];
  const total = claims.data?.pages[0]?.total ?? 0;

  // Nothing to scroll past, so nothing to float over: the action belongs IN the
  // empty state, where it is the only thing on the screen to do. Full width on
  // a phone — a right-aligned small button is the hardest thing to hit
  // one-handed.
  if (rows.length === 0) {
    return (
      <Mount label="No claims yet" className={MEASURE}>
        <p className="text-row text-label">
          When you pay for treatment that your benefits cover, send us the
          receipt here and we&rsquo;ll tell you where it&rsquo;s up to.
        </p>
        {canClaim && (
          <div>
            <Link
              to="/portal/$company/claims/new"
              params={{ company }}
              className={actionClass("primary", { block: "phone" })}
            >
              <FilePlus2 className="size-4" aria-hidden />
              Make a claim
            </Link>
          </div>
        )}
      </Mount>
    );
  }

  return (
    <>
      {/* FIRST in the DOM, though it is painted at the bottom of the viewport:
          it is fixed, so its position owes nothing to document order, and
          rendering it after the ledger put the portal's primary action behind
          every claim link in the tab order — 40 tab stops to reach a button
          that never left the screen. */}
      {canClaim && <MakeClaimAction />}
      {/* Clearance for the floating pill, on top of the shell's own padding.
          The pill's top edge is 144px off the floor on a phone (dock + gap +
          pill) against the shell's 112px, and 72px from `sm` up against its
          40px — so one value covers both and lands the last row ~30px clear on
          either. Measured, not guessed: at `pb-10` the phone left 14px and the
          ledger read as though the pill were resting on it. */}
      <div className={`${MEASURE} pb-14`}>
        <ClaimList items={rows} total={total} interactive />
        {claims.hasNextPage && (
          <div className="mt-4 flex justify-center">
            <button
              type="button"
              className={actionClass("quiet")}
              disabled={claims.isFetchingNextPage}
              onClick={() => void claims.fetchNextPage()}
            >
              {claims.isFetchingNextPage && (
                <Loader2 className="size-4 animate-spin" aria-hidden />
              )}
              Load older claims
            </button>
          </div>
        )}
      </div>
    </>
  );
}
