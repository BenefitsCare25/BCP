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
import { Mount, MountRule } from "./Mount";
import { Limit, Money, currencySymbol } from "./Figure";
import { FillRule } from "./FillRule";
import { glossBeside } from "./glossary";
import { formatDay } from "./date";

/** In-flight claims — the ones whose amounts make up a bucket's `pending`.
 *
 * Mirrors `utilization.py::PENDING_STATUSES`, which is "every status except
 * draft, rejected and approved". Spelled out rather than derived, because the
 * two lists have to agree for the breakdown below to reconcile, and a set
 * defined by subtraction silently grows a new member the day a status is added
 * server-side. */
const IN_FLIGHT = new Set([
  "submitted",
  "ai_review_pending",
  "ai_verified",
  "ai_flagged",
  "needs_info",
]);

/** The amount a claim contributes, exactly as `utilization.py::_claim_amount`
 * computes it — the converted figure when the claim was filed in another
 * currency, else what was claimed. */
function claimAmount(c: PortalClaim): number {
  return c.amount_converted ?? c.amount_claimed;
}

/** What the member sent in, itemised, for a product whose total is under
 * review. "S$303.48" answers nothing on its own — the question it provokes is
 * "which claims is that?", and the member is the only person who can tell us a
 * receipt is missing from it.
 *
 * **Rendered only when the rows RECONCILE with the bucket.** The total comes
 * from the utilisation service and the rows from the claims list — two
 * independent queries that can be a moment apart, and a breakdown that does not
 * add up to the figure above it reads as a fault in the number rather than in
 * the pairing. When they disagree the figure stands alone, which is what it did
 * before. */
function PendingBreakdown({
  bucket,
  claims,
}: {
  bucket: UtilizationBucket;
  claims: PortalClaim[];
}) {
  const mine = claims.filter(
    (c) =>
      c.claim_kind === "insured" &&
      c.product_code === bucket.product_code &&
      IN_FLIGHT.has(c.status),
  );
  if (mine.length === 0) return null;

  const total = mine.reduce((sum, c) => sum + claimAmount(c), 0);
  if (Math.abs(total - bucket.pending) > 0.01) return null;

  return (
    <div className="flex flex-col gap-1">
      <h3 className="leaf-label">
        {mine.length === 1
          ? "The claim under review"
          : `The ${mine.length} claims under review`}
      </h3>
      <dl className="divide-y divide-hairline/75">
        {mine.map((c) => (
          <div
            key={c.id}
            className="flex items-baseline justify-between gap-4 py-1.5"
          >
            <dt className="min-w-0 text-row text-record">
              {c.provider_name?.trim() || c.claim_type}
              <span className="block text-row text-label">
                {formatDay(c.incurred_date)}
                {c.dependant_name ? ` · for ${c.dependant_name}` : ""}
              </span>
            </dt>
            <dd className="m-0 shrink-0 text-right">
              <Money value={claimAmount(c)} currency={currencySymbol(c.currency)} />
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

function FlexBlock({ flex }: { flex: FlexUtilization }) {
  const currency = flex.currency ?? "S$";
  const base = flex.flex_balance ?? flex.wallet_amount;

  // A lone category with no cap of its own and nothing claimed against it is
  // the wallet restated under a second name — the row can only repeat the bar
  // directly above it. Two or more categories, a sub-limit or any activity all
  // make the breakdown say something the wallet doesn't.
  const only = flex.categories.length === 1 ? flex.categories[0] : null;
  const categories =
    only && only.sub_limit === null && only.approved <= 0 && only.pending <= 0
      ? []
      : flex.categories;

  return (
    <Mount
      label="Flexible benefits"
      gloss="Your yearly allowance and what you've claimed against it."
      aside={
        flex.available !== null ? (
          <div className="text-right">
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
            <div className="leaf-label mt-0.5">
              {flex.available < 0 ? "Short by" : "Left to claim"}
            </div>
          </div>
        ) : undefined
      }
    >
      {/* `remaining` is deliberately NOT passed. The mount's own aside already
          states what is left, in the largest type on the tile — passing it here
          too printed the same figure twice under two labels ("Left to claim"
          above, "left" in the legend), which reads as two facts that happen to
          agree rather than one stated once. The legend keeps the half the aside
          does not carry: how much of the allowance has gone. */}
      <FillRule
        limit={base}
        approved={flex.approved}
        pending={flex.pending}
        remaining={null}
        currency={currency}
      />

      {categories.length > 0 && (
        <>
          <MountRule className="mt-4" />
          <div className="divide-y divide-hairline/75">
            {categories.map((c) => (
              <div key={c.name} className="py-3">
                <div className="mb-2 flex items-baseline justify-between gap-4">
                  <span className="min-w-0 break-words text-row text-record">
                    {c.name}
                  </span>
                  {c.sub_limit === null && (
                    <span className="shrink-0 text-row text-label">
                      No separate cap
                    </span>
                  )}
                </div>
                <FillRule
                  limit={c.sub_limit}
                  approved={c.approved}
                  pending={c.pending}
                  remaining={c.remaining}
                  currency={currency}
                />
              </div>
            ))}
          </div>
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
  /** The member's claims, for itemising what is under review. Optional: the
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
          Claims still under review are shown separately and aren't taken off
          your remaining balance until they're approved.
        </p>
      )}
    </div>
  );
}
