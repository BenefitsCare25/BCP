/** "How much is left" — usage, as mounts.
 *
 * Fullness is utilisation. Pending is drawn and named separately and is NEVER
 * subtracted from what's left (mirrors `utilization.py`) — a member must not
 * read an in-flight claim as already spent.
 *
 * Two broker diagnostics the shared view surfaced here are gone: "no longer on
 * statement" (an orphaned bucket is a reconciliation task, and telling a member
 * their claim points at coverage that has moved gives them nothing to do) and
 * "Limit not machine-readable — over-limit guard inactive" (that is a note
 * about our parser). Neither is dropped silently: an orphaned bucket still
 * renders its figures under a plain heading, and an unparsed limit still shows
 * its verbatim text, which is the only honest thing to show. */
import type {
  FlexUtilization,
  Utilization,
  UtilizationBucket,
} from "@/types";
import type { PortalClaim } from "@/api/portal";
import { Mount, MountRow, MountRule } from "./Mount";
import { insuredClaimTitle } from "./ClaimMount";
import { Limit, Money, currencySymbol } from "./Figure";
import { ClaimStrike, claimBucket } from "./Strike";
import { FillRule, drawnAgainst } from "./FillRule";
import { prorationReason } from "./FlexProrationNote";
import { glossBeside } from "./glossary";
import { formatDay } from "./date";

/** The amount a claim contributes, exactly as `utilization.py::_claim_amount`
 * computes it — the converted figure when the claim was filed in another
 * currency, else what was claimed. */
function claimAmount(c: PortalClaim): number {
  return c.amount_converted ?? c.amount_claimed;
}

/** What the member sent in, itemised, for a product whose total is not settled.
 * "S$303.48" answers nothing on its own — the question it provokes is
 * "which claims is that?", and the member is the only person who can tell us a
 * receipt is missing from it.
 *
 * **"Under review" is not true of every claim in the figure.** `needs_info` is
 * pending — it is not settled, so it is summed here — but it is waiting on the
 * MEMBER, not on us, and this list was the one place that told them otherwise:
 * a claim we had asked them a question about was filed under "the 3 claims
 * under review", i.e. as something being handled. So the heading no longer
 * names a state, and each row carries its own through `ClaimStrike` — the same
 * vocabulary the ledger uses (`Strike.tsx`), so a status added there appears
 * here already worded.
 *
 * No links, deliberately: the broker's employee-view frame renders this leaf
 * (`operations/PortalFrame.tsx`), and a portal route href inside it would
 * navigate the BROKER's app out of the page it is embedded in.
 *
 * **Rendered only when the rows RECONCILE with the bucket.** The total comes
 * from the utilisation service and the rows from the claims list — two
 * independent queries that can be a moment apart, and a breakdown that does not
 * add up to the figure above it reads as a fault in the number rather than in
 * the pairing. When they disagree the figure stands alone, which is what it did
 * before.
 *
 * **The rows are chosen by the SERVED ids**, not by re-filtering the claim list.
 * That drops two mirrors of server-side rules at once: which statuses count as
 * pending (`utilization.PENDING_STATUSES`, defined by subtraction from the
 * settled set, so a copy here grows a different member the day a status is
 * added) and which claims belong to this bucket (`_bucket_sums`' business —
 * the ids already encode it, product code and all). */
function PendingBreakdown({
  bucket,
  claims,
}: {
  bucket: UtilizationBucket;
  claims: PortalClaim[];
}) {
  const ids = new Set(bucket.pending_claim_ids);
  const mine = claims.filter((c) => ids.has(c.id));
  if (mine.length === 0) return null;

  const total = mine.reduce((sum, c) => sum + claimAmount(c), 0);
  if (Math.abs(total - bucket.pending) > 0.01) return null;

  return (
    <div className="flex flex-col gap-1">
      <h3 className="leaf-label">
        {mine.length === 1 ? "The claim in that figure" : "What's in that figure"}
      </h3>
      <dl className="divide-y divide-hairline/75">
        {mine.map((c) => (
          <div
            key={c.id}
            className="flex items-baseline justify-between gap-4 py-1.5"
          >
            <dt className="min-w-0 text-row text-record">
              {c.provider_name?.trim() || insuredClaimTitle(c.claim_type, null)}
              <span className="block text-row text-label">
                {formatDay(c.incurred_date)}
                {c.dependant_name ? ` · for ${c.dependant_name}` : ""}
              </span>
            </dt>
            <dd className="m-0 shrink-0 text-right">
              <Money value={claimAmount(c)} currency={currencySymbol(c.currency)} />
              {/* Under the amount, on its own line: at index width a figure and
                  a struck state cannot share a line (the same constraint
                  `ConversationMount` records). */}
              <span className="mt-0.5 block">
                <ClaimStrike status={c.status} />
              </span>
            </dd>
          </div>
        ))}
      </dl>
    </div>
  );
}

function BucketBlock({
  bucket,
  sub,
}: {
  bucket: UtilizationBucket;
  sub?: boolean;
}) {
  const title = sub
    ? bucket.benefit_key
    : (bucket.product_name ?? bucket.product_code ?? "Benefit");

  return (
    <div className={sub ? "py-3 pl-4" : "py-3"}>
      <div className="mb-2 flex items-baseline justify-between gap-4">
        <span
          className={`min-w-0 break-words text-row ${
            sub ? "text-label" : "font-medium text-record"
          }`}
        >
          {title}
        </span>
        {/* Through `Limit`, not raw: it is the one place that decides how a
            cap prints, so the tab agrees with `FillRule` directly beneath it. */}
        {(bucket.limit_display || bucket.limit === null) && (
          <span className="shrink-0 text-row text-label">
            <Limit
              amount={null}
              display={bucket.limit_display}
              currency={currencySymbol(null)}
            />
          </span>
        )}
      </div>
      <FillRule
        limit={bucket.limit}
        approved={bucket.approved}
        pending={bucket.pending}
        remaining={bucket.remaining}
      />
    </div>
  );
}

/** What a flex category reports as drawn.
 *
 * A category with NO cap of its own draws against the WALLET, so that is the
 * limit its figure is capped at — the same rule `FillRule` applies to every
 * other limit, reached here because a capless category renders no bar and so
 * used to print `approved` raw. On a S$1,340 wallet against which S$2,000 of
 * claims were approved, the card said "S$0 left to claim" and, two lines below,
 * "Approved and paid S$2,000": the member is told the wallet paid more than it
 * holds. A wallet pays UP TO its allowance; the S$660 above it is the member's
 * own. The claim record and the reports still carry the full approved figure —
 * this cap applies only where a figure is read against a limit. */
function drawnFromWallet(
  approved: number,
  subLimit: number | null,
  wallet: number | null,
): number {
  const limit = subLimit ?? wallet;
  // Only a POSITIVE limit can cap anything. See `hasLimit` in `FlexBlock`:
  // `flex_balance` is deliberately signed when elected cover is priced past the
  // wallet, and `Math.min(approved, -100)` would report −100 as the amount
  // drawn — turning a real approval into a negative one.
  return limit == null || limit <= 0 ? approved : drawnAgainst(approved, limit);
}

function FlexBlock({ flex }: { flex: FlexUtilization }) {
  const currency = flex.currency ?? "S$";
  const base = flex.flex_balance ?? flex.wallet_amount;
  // **There is not always a limit to read the drawn figure against**, and this
  // is the guard `FillRule` used to apply before this card stated its own
  // figures. Two ways it fails: `wallet_amount` can be absent outright, and
  // `flex_balance` is deliberately SIGNED when the member holds elected cover
  // priced above their wallet (`utilization._flex_utilization`, pinned by
  // `test_cover_costing_more_than_the_wallet_stays_signed`) — the one overdrawn
  // state the product still reports, because the enrolment guard exists for it.
  // Capping against a negative made `used` NEGATIVE, printing "S$-100 of S$-100
  // used" beneath an aside already reading "Short by S$100", and hiding any
  // real approval behind it. With no positive limit the drawn figure is simply
  // what was approved, and the "of Y" clause is not printed at all.
  const hasLimit = base != null && base > 0;
  const used = hasLimit ? drawnAgainst(flex.approved, base) : flex.approved;
  const showDrawn = used > 0 || flex.pending > 0 || hasLimit;

  // A lone category with NO cap of its own can only restate the wallet: with
  // one category the wallet's approved and pending totals ARE that category's,
  // so the row repeats the two figures directly above it under a second name.
  // It used to be kept whenever it had any activity — which is exactly when the
  // repetition is visible. A sub-limit, or a second category, makes the
  // breakdown say something the wallet doesn't.
  const only = flex.categories.length === 1 ? flex.categories[0] : null;
  const categories = only && only.sub_limit === null ? [] : flex.categories;

  return (
    <Mount
      label="Flexible benefits"
      // No gloss. The aside states what is left and the rows beneath state the
      // allowance and what has gone — "your allowance and what you've claimed
      // against it" is a table of contents for three figures already on screen.
      // The figure and its label sit on ONE baseline beside the title, not
      // stacked — stacked, a two-word caption under a four-character figure
      // built a third band of height into a card that has two rows of content.
      aside={
        flex.available !== null ? (
          <span className="flex items-baseline justify-end gap-1.5">
            {/* Label FIRST, then the figure — it reads as a sentence, and it
                puts the number at the card's right edge where every other
                figure on the tab is aligned. */}
            <span className="leaf-label">
              {flex.available < 0 ? "Short by" : "Left to claim"}
            </span>
            {/* A wallet spent past its allowance (an upgrade priced above it)
                leaves this negative, and "S$-450 left to claim" is not a
                sentence anyone reads correctly — it is a shortfall, said the
                same way the coverage tab says it. */}
            <Money
              value={Math.abs(flex.available)}
              currency={currency}
              emphasis="display"
              className={flex.available < 0 ? "text-strike-pending" : undefined}
            />
          </span>
        ) : undefined
      }
    >
      {/* **The whole card is TWO rows.** Row one is the title with what's left
          beside it; row two is why the allowance is what it is on the left, and
          what has gone from it on the right — each half sitting under the
          heading half it belongs to.

          It was a bar, a legend, a row each for allowance and used, and a line
          for the pro-ration: five bands of vertical space for four numbers, on
          a card whose entire content is four numbers. The bar is gone because a
          track that is either empty or solid adds a graphic to a purely numeric
          card, and on a fully-drawn wallet it was a block of colour saying what
          "S$0 left to claim" already says in the largest type here.

          `remaining` is deliberately NOT restated: the aside carries it. And
          pending is a TERM OF ITS OWN, never folded into "used" — it is not
          subtracted from what is left (a product rule from `utilization.py`),
          so presenting it as spent would misreport the balance. The bar drew
          that distinction as a texture; the words carry it now. */}
      <div className="flex flex-wrap items-baseline justify-between gap-x-4 gap-y-1 text-row text-label">
        {/* Left: why the figure on the right is smaller than a full year's.
            The annual amount is deliberately not repeated here — "What's
            covered" states it in full ("S$2,680 a year, pro-rated to 3/6
            months of cover"); this tab is about what is left, so it carries
            the reason and not the arithmetic. An empty span holds the right
            half in place for a member who is covered the whole period. */}
        <span>{flex.proration && prorationReason(flex.proration.note)}</span>
        {/* Right: "X of Y used" — the same words `FillRule` captions every
            other limit with, and the same words the category rows below use, so
            the wallet and its subdivisions read as one vocabulary. It replaced
            "S$1,340 allowance · S$1,340 used", which introduced a synonym for
            the card's own title to label a figure the sentence already
            explains. */}
        {showDrawn && (
          <span className="shrink-0">
            <Money value={used} currency={currency} />
            {hasLimit && (
              <>
                {" of "}
                <Money value={base} currency={currency} />
              </>
            )}
            {" used"}
            {flex.pending > 0 && (
              <>
                {" · "}
                <Money value={flex.pending} currency={currency} /> not settled
              </>
            )}
          </span>
        )}
      </div>

      {/* One LINE per category, not a titled block with its own bar. Each is a
          subdivision of the wallet stated directly above, so it needs to be
          readable as a detail of it rather than as another card. */}
      {categories.length > 0 && (
        <>
          <MountRule className="mt-1" />
          <dl>
            {categories.map((c) => (
              <MountRow key={c.name} term={c.name}>
                <span className="text-label">
                  <Money
                    value={drawnFromWallet(c.approved, c.sub_limit, base)}
                    currency={currency}
                  />
                  {c.sub_limit !== null && (
                    <>
                      {" of "}
                      <Money value={c.sub_limit} currency={currency} />
                    </>
                  )}
                  {" used"}
                  {c.remaining !== null && (
                    <>
                      {" · "}
                      <Money
                        value={Math.max(0, c.remaining)}
                        currency={currency}
                      />
                      {" left"}
                    </>
                  )}
                </span>
              </MountRow>
            ))}
          </dl>
        </>
      )}
    </Mount>
  );
}

export function UsageLeaf({
  data,
  claims = [],
}: {
  data: Utilization;
  /** The member's claims, for itemising what is not settled. Optional: the
   * balances are still the answer if the claims query is slow or fails, so this
   * never gates rendering. */
  claims?: PortalClaim[];
}) {
  const products = data.insured.filter((b) => b.benefit_key === null);
  const subsFor = (product: string | null) =>
    data.insured.filter(
      (b) => b.product_code === product && b.benefit_key !== null,
    );

  // **A product with no cap, nothing claimed and no sub-limits is not shown.**
  // It has no fullness to draw and nothing to count down, so the only thing it
  // could state is that it has no yearly cap — which is a fact about the policy,
  // and the policy is the other tab. Eight of them collapsed into one mount
  // ("Nothing claimed yet · These are still fully available to you") and that
  // mount was still eight rows of "No yearly cap": the largest object on a page
  // about what is left, carrying nothing that is left.
  const active = products.filter(
    (b) =>
      b.limit !== null ||
      b.approved > 0 ||
      b.pending > 0 ||
      subsFor(b.product_code).length > 0,
  );

  // Gated on what there is to RENDER, not on what exists. With the caps gone, a
  // member holding nine uncapped products and no claims has an empty page, and
  // an empty page must still say why.
  if (active.length === 0 && data.flex === null) {
    return (
      <Mount label="Nothing to track yet">
        <p className="text-row text-label">
          Once you've made a claim, you'll see how much of each benefit you've
          used here.
        </p>
      </Mount>
    );
  }

  const anyPending =
    data.insured.some((b) => b.pending > 0) || (data.flex?.pending ?? 0) > 0;
  // How many unsettled claims are waiting on the MEMBER. It changes the
  // footnote's verb: "still with us" is a promise we are working on it, and for
  // a claim we have asked a question about it is the opposite of true.
  const waitingOnMember = claims.filter(
    (c) => claimBucket(c.status) === "attention",
  ).length;

  return (
    <div className="space-y-3">
      {active.map((b) => (
        <Mount
          key={b.product_code ?? "unknown"}
          label={b.product_name ?? b.product_code ?? "Benefit"}
          gloss={
            b.product_code
              ? glossBeside(
                  b.product_name ?? b.product_code,
                  b.product_code,
                  b.product_name,
                )
              : null
          }
          aside={
            b.limit_display ? (
              <span className="text-row text-label">
                <Limit
                  amount={null}
                  display={b.limit_display}
                  currency={currencySymbol(null)}
                />
              </span>
            ) : undefined
          }
        >
          <FillRule
            limit={b.limit}
            approved={b.approved}
            pending={b.pending}
            remaining={b.remaining}
          />
          {b.pending > 0 && (
            <PendingBreakdown bucket={b} claims={claims} />
          )}
          {subsFor(b.product_code).length > 0 && (
            <>
              <MountRule className="mt-4" />
              <div className="divide-y divide-hairline/75">
                {subsFor(b.product_code).map((s) => (
                  <BucketBlock
                    key={`${s.product_code}/${s.benefit_key}`}
                    bucket={s}
                    sub
                  />
                ))}
              </div>
            </>
          )}
        </Mount>
      ))}

      {data.flex && <FlexBlock flex={data.flex} />}

      {anyPending && (
        <p className="px-1 text-row text-label">
          Claims that aren&rsquo;t settled yet are shown separately and
          aren&rsquo;t taken off your remaining balance until they&rsquo;re
          approved.
          {waitingOnMember > 0 &&
            (waitingOnMember === 1
              ? " One of them needs something from you — open it under Claims."
              : ` ${waitingOnMember} of them need something from you — open them under Claims.`)}
        </p>
      )}
    </div>
  );
}
