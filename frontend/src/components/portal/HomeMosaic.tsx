/** The home — a mosaic of tiles, each answering one question completely.
 *
 * The questions a member actually arrives with, in order of frequency: is
 * anyone talking to me, what happened to my claim, how much is left, am I
 * covered for this, where do I go. Each is a tile sized to its answer.
 * **Hierarchy comes from tile span and figure scale**, which is what carries it
 * in a light-only world with no dark tile to do the work.
 *
 * **Messages hold the wide slot** (2026-08-01). It used to be the limits
 * figure, and the swap is deliberate: a limit is a number the member can go and
 * look up whenever they want, whereas a message is addressed TO them and is
 * only actionable while it is in front of them. Nothing else on this page is
 * addressed to the member personally. The limits tile keeps its figure and its
 * fill at single width — it lost the two runner-up buckets, which are one tap
 * away under "See all limits" and were never the reason anyone opened this
 * page.
 *
 * Three rules this file is easy to break:
 *
 * 1. **Never render a tile for something the member does not hold.** Fullness
 *    means utilisation, not entitlement; an empty tile has to mean "unused
 *    limit", so a benefit they were never issued must be absent, not empty.
 * 2. **No brand fill on this screen at all.** There is deliberately no "Submit
 *    a claim" action here — the home is a set of ANSWERS, and submitting belongs
 *    to the Claims destination. The enrolment deadline is likewise a NOTICE, a
 *    pending-ink strike and a text link. The result is that the member's own
 *    figures and verdicts are the only saturated things on the page, which is
 *    the world's colour thesis working rather than being talked about.
 * 3. **One anchor per tile.** The whole tile is the target, via the stretched-
 *    link pattern: the tile is a plain element and its onward link carries an
 *    `after:absolute after:inset-0` overlay. Making the tile itself a `<Link>`
 *    and keeping the "See all →" inside it nests anchors, which is invalid and
 *    gives screen-reader users two targets for one destination. */
import type { ReactNode } from "react";
import { Link, useNavigate } from "@tanstack/react-router";
import { MapPin } from "lucide-react";
import type {
  BenefitStatement,
  Dependant,
  Utilization,
  UtilizationBucket,
} from "@/types";
import {
  usePortalClaims,
  usePortalDependants,
  usePortalMe,
  usePortalStatement,
  usePortalUtilization,
  type PortalClaim,
} from "@/api/portal";
import {
  usePortalConversations,
  type Conversation,
  type ConversationList,
} from "@/api/portalMessages";
import { cn } from "@/lib/cn";
import { isNotFoundError } from "@/lib/errors";
import { PortalErrorState } from "./PortalErrorState";
import { glassHover, glassSurface, MountRule } from "./leaf/Mount";
import { goLinkClass, GoArrow } from "./leaf/Action";
import { FillRule } from "./leaf/FillRule";
import { Money, currencySymbol, moneyText } from "./leaf/Figure";
import { ConversationRows } from "./leaf/ConversationMount";
// One rule names a claim on every member surface — including the guard
// against the broker-side `LOG` sentinel, which this tile printed raw.
import { claimTitle } from "./leaf/ClaimMount";
import { ClaimStrike, Strike } from "./leaf/Strike";
import { LeafSkeleton } from "./leaf/LeafSkeleton";
import { formatDay } from "./leaf/date";
import { useCompany } from "./useCompany";

/** How many messages the home tile shows before deferring to the inbox. Three
 * is what fits the wide tile beside the claims tile without either column
 * driving the row's height. */
const HOME_MESSAGES = 3;

/** A home tile is a `Mount` in everything but its header furniture: the same
 * material, the same single hover response, no private treatment.
 *
 * **There is no `interactive` prop and no lift.** Five of these tiles used to
 * rise 3px on hover while every other pane in the portal did not, which made the
 * home the one page whose cards behaved differently — see `glassHover`. What
 * marks a tile as a target is its stretched anchor and the pointer cursor that
 * comes with it, not the geometry. */
function Tile({
  className,
  children,
}: {
  className?: string;
  children: React.ReactNode;
}) {
  return (
    <div
      className={cn(
        glassSurface,
        glassHover,
        "relative flex flex-col gap-3 rounded-tile p-4 sm:p-5",
        className,
      )}
    >
      {children}
    </div>
  );
}

function TileLabel({ children }: { children: React.ReactNode }) {
  return <p className="leaf-label">{children}</p>;
}

/** Where a tile goes. Named destinations rather than routes, because the broker
 * preview reaches the same places by switching ITS tabs — a real `<Link>` there
 * would walk a broker out of their own application and into the member portal. */
export type HomeDest =
  | "usage"
  | "benefits"
  | "dependants"
  | "claims"
  | "messages"
  | "clinics"
  | "enrollment";

const ROUTE: Record<HomeDest, { to: string; search?: { tab: string } }> = {
  usage: { to: "/portal/$company/coverage", search: { tab: "usage" } },
  benefits: { to: "/portal/$company/coverage", search: { tab: "benefits" } },
  dependants: { to: "/portal/$company/coverage", search: { tab: "dependants" } },
  claims: { to: "/portal/$company/claims" },
  messages: { to: "/portal/$company/messages" },
  clinics: { to: "/portal/$company/clinics" },
  enrollment: { to: "/portal/$company/enrollment" },
};

/** The tile's onward link — stretched across the whole tile by default.
 *
 * A `<button>` when the surface owns its own navigation (the preview) and a
 * real `<Link>` otherwise — never a `<Link>` wrapping the tile with this inside
 * it, which nests anchors and hands screen-reader users two targets for one
 * destination.
 *
 * `stretch={false}` is for a tile whose CONTENTS are the targets (Messages,
 * whose rows each go to their own claim). Stretching the overlay there would
 * lay an invisible anchor over every row and swallow their clicks. */
function Go({
  dest,
  onGo,
  brand,
  stretch = true,
  children,
}: {
  dest: HomeDest;
  onGo?: (dest: HomeDest) => void;
  brand?: boolean;
  stretch?: boolean;
  children: ReactNode;
}) {
  const className = goLinkClass({
    brand,
    stretch,
    className: "mt-auto",
  });
  if (onGo) {
    return (
      <button type="button" onClick={() => onGo(dest)} className={className}>
        {children}
        <GoArrow brand={brand} />
      </button>
    );
  }
  const route = ROUTE[dest];
  return (
    <Link to={route.to} search={route.search} className={className}>
      {children}
      <GoArrow brand={brand} />
    </Link>
  );
}

/** The bucket the display figure belongs to.
 *
 * Preference order is deliberate: attention goes to the limit a member is
 * actually spending against, so activity outranks size. Only buckets with a
 * PARSED numeric limit can carry a fill rule — "As charged" and "S$650 per day"
 * have no fullness to express — and an orphaned bucket (claims against coverage
 * no longer on the statement) is never the headline.
 *
 * **A bucket may only be the headline if it can ANSWER.** A cap answers "how
 * much is left"; failing that, money actually settled this year answers "how
 * much have I had back". A bucket with neither has nothing to say that the
 * claims tile beside it is not already saying — see `heroFigure`. */
function pickHeadline(buckets: UtilizationBucket[]): UtilizationBucket | null {
  const live = buckets.filter((b) => !b.orphaned);
  const withLimit = live.filter((b) => b.limit !== null && b.limit > 0);
  const pool =
    withLimit.length > 0 ? withLimit : live.filter((b) => b.approved > 0);
  if (pool.length === 0) return null;
  return [...pool].sort(
    (a, b) =>
      b.claim_count - a.claim_count ||
      b.approved - a.approved ||
      (b.limit ?? 0) - (a.limit ?? 0),
  )[0];
}

/** What the one monumental figure on this screen actually says, or `null` when
 * this bucket has no answer and the tile must not render at all.
 *
 * A capped benefit answers "how much is left". An uncapped one — "as charged",
 * "S$650 per day" — cannot, and printing its remaining figure anyway rendered an
 * em-dash for every member whose benefits carry no annual cap.
 *
 * **There is deliberately no "Under review" fallback.** It used to print the
 * PENDING total when a member had no cap and nothing settled — which put a
 * second card on the home screen struck "UNDER REVIEW", directly under the
 * claims tile that was already struck "UNDER REVIEW", showing a different
 * number (the sum of pending claims against that one claim's amount). Two
 * cards, one heading, two figures, and the one that looked like a limit was
 * not a limit. Money in review belongs to the CLAIMS tile, which is where a
 * member goes to ask what happened to it; this tile is about limits, and when
 * there is no limit and nothing settled it has nothing to say. */
function heroFigure(b: UtilizationBucket, cur: string) {
  if (b.limit !== null) {
    return { label: "Left to claim", value: b.remaining ?? b.limit };
  }
  if (b.approved > 0) {
    return {
      label: "Claimed this year",
      value: b.approved,
      note:
        b.pending > 0
          ? `${cur}${moneyText(b.pending)} still under review`
          : undefined,
    };
  }
  return null;
}

/** What the mosaic needs, independent of who fetched it. `Loadable` rather than
 * `UseQueryResult` so the broker preview can pass its own query results (from
 * the `/employees/{id}/portal-preview/*` endpoints) without either surface
 * knowing about the other's client. */
interface Loadable<T> {
  data?: T | null;
  isLoading?: boolean;
  isError?: boolean;
  error?: unknown;
}

export interface HomeMosaicSource {
  enrollmentOpen: boolean;
  /** The SERVED capability list (`PortalMe.access.capabilities`). The home
   *  tiles are entry points to the same destinations the nav offers, so they
   *  have to close on the same rule — a leaver whose Clinics tab is gone but
   *  whose "Find a clinic" tile is still there just reaches a 403 by a
   *  different route, which is the "the app is broken" outcome the whole
   *  access notice exists to prevent. Undefined until `me` resolves: show
   *  everything rather than blink tiles away on a cold load. */
  capabilities?: string[];
  utilization: Loadable<Utilization>;
  claims: Loadable<{ items: PortalClaim[] }>;
  statement: Loadable<BenefitStatement>;
  dependants: Loadable<Dependant[]>;
  messages: Loadable<ConversationList>;
  /** Refetch every query behind the mosaic. Wired by the surface that owns the
   * hooks, so the error state can offer the one useful action. */
  onRetry?: () => void;
}

/** The member's own home: hooks in, view out. */
export function HomeMosaic() {
  const { data: me } = usePortalMe();
  const utilization = usePortalUtilization();
  const claims = usePortalClaims();
  const statement = usePortalStatement();
  const dependants = usePortalDependants();
  const messages = usePortalConversations();

  return (
    <HomeMosaicView
      source={{
        enrollmentOpen: Boolean(me?.enrollment_open),
        capabilities: me?.access.capabilities,
        utilization,
        claims,
        statement,
        dependants,
        messages,
        onRetry: () => {
          void utilization.refetch();
          void claims.refetch();
          void statement.refetch();
          void dependants.refetch();
          void messages.refetch();
        },
      }}
    />
  );
}

export function HomeMosaicView({
  source,
  onGo,
  onOpenClaim,
  onOpenQuestion,
}: {
  source: HomeMosaicSource;
  /** Supplied by the broker preview, which navigates by switching its own tabs. */
  onGo?: (dest: HomeDest) => void;
  /** Opens ONE claim without navigating — the broker preview's drill-in. A
   *  message row's destination is its claim, and `onGo` only names tabs. */
  onOpenClaim?: (claimId: string) => void;
  /** The question half of the same drill-in. Paired with `onOpenClaim`:
   *  a Messages row is one of two kinds and the tile must not send an
   *  enquiry id to a claim view. */
  onOpenQuestion?: (enquiryId: string) => void;
}) {
  const navigate = useNavigate();
  const { utilization, claims, statement, dependants, messages } = source;
  // Undefined until `me` resolves — show everything then, the same rule the
  // shell nav uses. The endpoints refuse whatever they must regardless.
  const can = (capability: string) =>
    !source.capabilities || source.capabilities.includes(capability);

  const buckets = utilization.data?.insured ?? [];
  const headline = pickHeadline(buckets);
  // Every live bucket that isn't the headline. Not rendered here since the
  // limits tile moved to single width — counted, so the tile can say how much
  // is behind its link rather than implying the headline is everything.
  const otherCount = headline
    ? buckets.filter((b) => !b.orphaned && b !== headline).length
    : 0;

  const cur = currencySymbol(utilization.data?.flex?.currency);
  const hero = headline ? heroFigure(headline, cur) : null;

  const company = useCompany();
  const claimItems = claims.data?.items ?? [];
  const latest = claimItems[0];
  const earlier = claimItems.slice(1, 3);

  const messageItems = messages.data?.items ?? [];
  const unread = messages.data?.unread_total ?? 0;
  // A message row lands on its own claim, on BOTH surfaces — the member by
  // route, the broker's preview frame by drilling into its own claim detail.
  // It must never navigate a broker into the live member portal, so `onGo`
  // (which marks that frame) falls back to an inert row if no drill-in handler
  // was passed rather than to a `<Link>`.
  // **A row opens what it is ABOUT, and the two kinds are not the same thing.**
  // This sent every row to the claim route with `subject.id` — so a question,
  // which sorts to the top of this tile the moment it is asked, loaded the
  // claim page with an enquiry id and rendered "We couldn't find that claim".
  // Both the Messages page and the preview frame branch on `kind`; only this
  // did not.
  const openMessage = onOpenClaim
    ? (c: Conversation) =>
        c.subject.kind === "enquiry"
          ? onOpenQuestion?.(c.subject.id)
          : onOpenClaim(c.subject.id)
    : onGo
      ? undefined
      : (c: Conversation) =>
          void navigate(
            c.subject.kind === "enquiry"
              ? {
                  to: "/portal/$company/questions/$enquiryId",
                  params: { company, enquiryId: c.subject.id },
                }
              : {
                  to: "/portal/$company/claims/$claimId",
                  params: { company, claimId: c.subject.id },
                },
          );

  const coverage = statement.data?.coverage ?? [];
  const activeDependants = (dependants.data ?? []).filter(
    (d) => d.status === "active",
  );

  // OR, not AND. Every query here carries `localErrorHandling` + `retry:false`,
  // so nothing else announces a failure — and with an AND gate the tiles render
  // as soon as the FIRST query resolves, which printed "You haven't made a claim
  // yet" at a member whose claims were still in flight. A tile's empty state is
  // a statement of fact about the member, so it may only be shown once the query
  // behind it has actually answered.
  const loading =
    utilization.isLoading ||
    claims.isLoading ||
    statement.isLoading ||
    dependants.isLoading ||
    messages.isLoading;
  if (loading) {
    return <LeafSkeleton label="Loading your benefits" mounts={4} />;
  }

  // A 404 is the honest "nothing on record" case and falls through to the tiles;
  // anything else is a fetch failure and must not read as "no claims yet". Same
  // rule the claims list and every coverage tab already follow.
  const failed = [utilization, claims, statement, dependants, messages].filter(
    (q) => q.isError && !isNotFoundError(q.error),
  );
  if (failed.length > 0) {
    return <PortalErrorState onRetry={source.onRetry} />;
  }

  return (
    <div className="grid grid-cols-2 items-start gap-3 sm:grid-cols-3 sm:gap-4">
      {/* ── Your claims ───────────────────────────────────────────────────
          The verdict is the answer members come back for, so it leads. The
          strike does NOT animate here — twenty rules drawing at once is the
          uniform page-load flourish this world refuses everywhere else. */}
      <Tile
        className="leaf-rise col-span-2 h-full sm:col-span-1"
      >
        <TileLabel>Your claims</TileLabel>
        {latest ? (
          <>
            <ClaimStrike status={latest.status} />
            <div className="flex items-baseline justify-between gap-3">
              <Money
                value={latest.amount_approved ?? latest.amount_claimed}
                currency={currencySymbol(latest.currency)}
                className="text-2xl font-semibold tracking-title"
              />
              <span className="shrink-0 text-row text-label">
                {formatDay(latest.incurred_date)}
              </span>
            </div>
            <p className="text-row text-label">{claimTitle(latest)}</p>
            {earlier.length > 0 && (
              <>
                <MountRule />
                <ul className="flex flex-col gap-2.5">
                  {earlier.map((c) => (
                    <li
                      key={c.id}
                      className="flex items-center justify-between gap-3"
                    >
                      <span className="min-w-0">
                        <span className="block text-row font-medium text-record">
                          <Money
                            value={c.amount_approved ?? c.amount_claimed}
                            currency={currencySymbol(c.currency)}
                          />
                        </span>
                        <span className="block truncate text-2xs text-label">
                          {claimTitle(c)} · {formatDay(c.incurred_date)}
                        </span>
                      </span>
                      <ClaimStrike status={c.status} />
                    </li>
                  ))}
                </ul>
              </>
            )}
            <Go dest="claims" onGo={onGo}>
              See all claims
            </Go>
          </>
        ) : (
          <>
            <p className="text-md font-semibold text-record">
              You haven&rsquo;t made a claim yet
            </p>
            <p className="text-row text-label">
              Photograph your receipt and we&rsquo;ll read the amount, date and
              clinic off it for you.
            </p>
          </>
        )}
      </Tile>

      {/* ── Messages ──────────────────────────────────────────────────────
          The wide slot, because this is the only thing on the page addressed
          to the member personally.

          **Each ROW is its own target, and it goes to that message's CLAIM** —
          not to the inbox. A message is always ABOUT a claim, and answering
          "what is this about?" needs the claim beside it: the amount, the
          documents, whether anything is being asked of them. Sending every row
          to a longer list of the same rows just asks the member to find it
          again.

          So this tile is NOT stretched and NOT `interactive`: the tile is not a
          single target, and a surface that lifts as one thing while containing
          four separate destinations promises something it can't keep. */}
      <Tile className="leaf-rise col-span-2 h-full sm:col-span-2">
        <div className="flex items-baseline justify-between gap-3">
          <TileLabel>Messages</TileLabel>
          {/* Struck in the pending ink, not the brand — see MessageMount. */}
          {unread > 0 && (
            <Strike tone="pending">
              {unread} unread
            </Strike>
          )}
        </div>
        {messageItems.length > 0 ? (
          <>
            <ConversationRows
              items={messageItems.slice(0, HOME_MESSAGES)}
              className="-mt-1"
              // Opens the message's own claim on either surface — see
              // `openMessage`, which resolves the two ways of getting there.
              onOpen={openMessage}
            />
            <Go dest="messages" onGo={onGo} stretch={false}>
              See all messages
            </Go>
          </>
        ) : (
          <>
            <p className="text-md font-semibold text-record">
              Nothing to read
            </p>
            <p className="text-row text-label">
              When we have news about a claim — that we&rsquo;ve received it,
              that it&rsquo;s settled, or that we need something else — it will
              appear here, and you can reply to us on the claim itself.
            </p>
          </>
        )}
      </Tile>

      {/* No "Submit a claim" action here, by decision. The home is a set of
          ANSWERS; submitting belongs to the Claims destination, which is one tap
          away in the dock and one click in the bar. Keeping it off this screen
          also leaves the home with no brand fill at all, so the member's own
          figures and verdicts are the only saturated things on it — which is the
          world's colour thesis working rather than being talked about. */}

      {/* ── What's left ───────────────────────────────────────────────────
          Single width now that Messages holds the wide slot. It keeps its
          figure and its fill — the two things that answer "how much is left" —
          and defers the runner-up buckets to the usage page it links to.
          Rendered only when the member actually holds a limit; the empty case
          is the tile's absence. */}
      {headline && hero && (
        <Tile className="leaf-rise col-span-2 h-full sm:col-span-1">
          <TileLabel>{hero.label}</TileLabel>
          <Money
            value={hero.value}
            currency={cur}
            className="text-3xl font-semibold tracking-title"
          />
          {/* The bucket this figure belongs to. At single width the product
              name can no longer sit opposite the label, so it goes here — and
              the benefit name is preferred when there is one, since it is the
              narrower, more specific fact. */}
          <p className="truncate text-row text-label">
            {headline.benefit_key ||
              headline.product_name ||
              headline.product_code}
          </p>
          {headline.limit !== null ? (
            <FillRule
              limit={headline.limit}
              approved={headline.approved}
              pending={headline.pending}
              remaining={headline.remaining}
              currency={cur}
            />
          ) : (
            // No parsed limit means no fullness to draw. The figure already
            // carries the amount, so this is the one line of context it needs —
            // and it must never restate it, which would read as two different
            // amounts.
            <p className="text-row text-label">
              {hero.note ?? "No yearly cap on this benefit"}
            </p>
          )}
          <Go dest="usage" onGo={onGo}>
            {otherCount > 0 ? `See all ${otherCount + 1} limits` : "See all limits"}
          </Go>
        </Tile>
      )}

      {coverage.length > 0 && (
        <Tile className="leaf-rise h-full">
          <TileLabel>What&rsquo;s covered</TileLabel>
          <p className="text-2xl font-semibold tracking-title text-record">
            {coverage.length} {coverage.length === 1 ? "benefit" : "benefits"}
          </p>
          <p className="text-row text-label">
            {coverage
              .slice(0, 4)
              .map((c) => c.product_name || c.product_code)
              .join(" · ")}
            {coverage.length > 4 && ` · and ${coverage.length - 4} more`}
          </p>
          <Go dest="benefits" onGo={onGo}>
            See what&rsquo;s covered
          </Go>
        </Tile>
      )}

      {activeDependants.length > 0 && (
        <Tile className="leaf-rise h-full">
          <TileLabel>My family</TileLabel>
          <p className="text-2xl font-semibold tracking-title text-record">
            {activeDependants.length} covered
          </p>
          <Go dest="dependants" onGo={onGo}>
            See my family
          </Go>
        </Tile>
      )}

      {can("entitlement") && (
      <Tile
        className="leaf-rise col-span-2 h-full sm:col-span-1"
      >
        <div className="flex items-start justify-between gap-3">
          <div className="min-w-0">
            <TileLabel>Panel clinics</TileLabel>
            <p className="mt-1.5 text-md font-semibold text-record">
              Find a clinic near you
            </p>
            <p className="mt-0.5 text-row text-label">
              GP · Dental · TCM · Specialist
            </p>
          </div>
          <span
            aria-hidden
            className="flex size-10 shrink-0 items-center justify-center rounded-pill bg-shade text-label"
          >
            <MapPin className="size-5" />
          </span>
        </div>
        <Go dest="clinics" onGo={onGo}>
          Find a clinic
        </Go>
      </Tile>
      )}

      {/* ── Enrolment ─────────────────────────────────────────────────────
          A deadline is a NOTICE, never a second brand fill, and it exists only
          while a window is open. */}
      {source.enrollmentOpen && can("elect") && (
        <Tile
          className="leaf-rise col-span-2 sm:col-span-3"
        >
          <Strike tone="pending">Enrolment open</Strike>
          <p className="text-md font-semibold text-record">
            Your benefit choices for next year
          </p>
          <p className="text-row text-label">
            If the window closes before you choose, this year&rsquo;s cover
            carries over.
          </p>
          <Go dest="enrollment" onGo={onGo} brand>
            Review my choices
          </Go>
        </Tile>
      )}
    </div>
  );
}
