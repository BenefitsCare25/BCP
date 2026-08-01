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
import { Mount, MountRow, MountRule } from "./Mount";
import { Limit, Money, currencySymbol } from "./Figure";
import { FillRule } from "./FillRule";
import { glossBeside } from "./glossary";

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
      <FillRule
        limit={base}
        approved={flex.approved}
        pending={flex.pending}
        remaining={flex.available}
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

export function UsageLeaf({ data }: { data: Utilization }) {
  const products = data.insured.filter((b) => b.benefit_key === null);
  const subsFor = (product: string | null) =>
    data.insured.filter(
      (b) => b.product_code === product && b.benefit_key !== null,
    );

  if (products.length === 0 && data.flex === null) {
    return (
      <Mount label="Nothing to track yet">
        <p className="text-row text-label">
          Once you've made a claim, you'll see how much of each benefit you've
          used here.
        </p>
      </Mount>
    );
  }

  // A product with no cap, nothing claimed and no sub-limits has NO fullness to
  // draw — it can only print "Nothing claimed yet" under its own name. Eight of
  // those is eight glass tiles carrying one repeated sentence, which is noise
  // rather than information, and it buries the one product that does have
  // something to report.
  //
  // So the tab is partitioned by whether a product has an answer to its own
  // question. The ones that do keep their mount; the ones that don't collapse
  // into a single mount that states the sentence ONCE and spends the space on
  // the caps — the half of "how much is left" that still has an answer.
  const quiet = products.filter(
    (b) =>
      b.limit === null &&
      b.approved <= 0 &&
      b.pending <= 0 &&
      subsFor(b.product_code).length === 0,
  );
  const active = products.filter((b) => !quiet.includes(b));
  const anyPending =
    data.insured.some((b) => b.pending > 0) || (data.flex?.pending ?? 0) > 0;
  // Nothing anywhere has been claimed, so the collapsed mount is the whole page
  // and can say so plainly instead of reading as a leftovers bin. The flex
  // wallet counts: a member who has spent their allowance HAS claimed this
  // year, and telling them otherwise contradicts the mount directly above.
  const nothingClaimed =
    active.length === 0 &&
    (data.flex === null ||
      (data.flex.approved <= 0 && data.flex.pending <= 0));

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

      {quiet.length > 0 && (
        <Mount
          label={
            nothingClaimed
              ? "You haven't claimed anything yet this year"
              : "Nothing claimed yet"
          }
          gloss="These are still fully available to you."
        >
          <dl className="divide-y divide-hairline/75">
            {quiet.map((b) => (
              <MountRow
                key={b.product_code ?? "unknown"}
                term={b.product_name ?? b.product_code ?? "Benefit"}
                gloss={
                  b.product_code
                    ? (glossBeside(
                        b.product_name ?? b.product_code,
                        b.product_code,
                        b.product_name,
                      ) ?? undefined)
                    : undefined
                }
              >
                <Limit
                  amount={b.limit}
                  display={b.limit_display}
                  currency={currencySymbol(null)}
                />
              </MountRow>
            ))}
          </dl>
          <p className="text-row text-label">
            &ldquo;No yearly cap&rdquo; means there is no fixed yearly amount —
            the plan pays eligible costs as they come in, on the terms in your
            schedule.
          </p>
        </Mount>
      )}

      {anyPending && (
        <p className="px-1 text-row text-label">
          Claims still under review are shown separately and aren't taken off
          your remaining balance until they're approved.
        </p>
      )}
    </div>
  );
}
