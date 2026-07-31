/** Utilization bars — approved (solid) / pending (hatched) / remaining track.
 * Shared by the member portal page and the broker benefit-statement panel. */
import { Badge } from "@/components/ui/badge";
import { InfoHint } from "@/components/ui/tooltip";
import { fmtAmount } from "@/lib/format";
import type {
  FlexUtilization,
  Utilization,
  UtilizationBucket,
} from "@/types";

const BUCKET_HINT = (
  <>
    Approved = claims already paid. Pending = claims still under review, shown
    for awareness and not subtracted from the balance. Remaining = limit minus
    approved.
  </>
);

const PENDING_HATCH = {
  backgroundImage:
    "repeating-linear-gradient(45deg, var(--color-warn) 0 4px, var(--color-warn-soft) 4px 8px)",
} as const;

export function UtilizationBar({
  limit,
  approved,
  pending,
}: {
  limit: number | null;
  approved: number;
  pending: number;
}) {
  if (limit === null || limit <= 0) return null;
  const approvedPct = Math.min(100, (approved / limit) * 100);
  const pendingPct = Math.min(100 - approvedPct, (pending / limit) * 100);
  return (
    <div className="h-2 w-full overflow-hidden rounded-full bg-muted flex">
      <div
        className="h-full bg-good"
        style={{ width: `${approvedPct}%` }}
        title={`Approved: ${fmtAmount(approved)}`}
      />
      <div
        className="h-full"
        style={{ width: `${pendingPct}%`, ...PENDING_HATCH }}
        title={`Pending: ${fmtAmount(pending)}`}
      />
    </div>
  );
}

function AmountRow({
  approved,
  pending,
  remaining,
}: {
  approved: number;
  pending: number;
  remaining: number | null;
}) {
  return (
    <div className="flex items-center gap-3 text-2xs text-muted-foreground">
      <span>
        <span className="inline-block size-2 rounded-sm bg-good align-[-1px] mr-1" />
        Approved {fmtAmount(approved)}
      </span>
      {pending > 0 && (
        <span>
          <span
            className="inline-block size-2 rounded-sm align-[-1px] mr-1"
            style={PENDING_HATCH}
          />
          Pending {fmtAmount(pending)}
        </span>
      )}
      {remaining !== null && (
        <span className="ml-auto font-medium text-foreground">
          {fmtAmount(remaining)} remaining
        </span>
      )}
    </div>
  );
}

function BucketRow({ bucket, sub }: { bucket: UtilizationBucket; sub?: boolean }) {
  return (
    <div className={sub ? "ml-4 border-l border-border pl-3 py-1.5" : "py-1.5"}>
      <div className="flex items-baseline justify-between gap-2">
        <div className="text-sm text-foreground min-w-0 truncate">
          {sub
            ? bucket.benefit_key
            : (bucket.product_name ?? bucket.product_code)}
          {!sub && bucket.product_name && (
            <span className="text-muted-foreground text-xs ml-1.5">
              {bucket.product_code}
            </span>
          )}
          {bucket.orphaned && (
            <Badge variant="warn" className="ml-2">
              no longer on statement
            </Badge>
          )}
          {bucket.limit_unparsed === true && (
            <Badge
              variant="warn"
              className="ml-2"
              title={
                bucket.limit_display
                  ? `Limit text: ${bucket.limit_display}`
                  : undefined
              }
            >
              Limit not machine-readable — over-limit guard inactive
            </Badge>
          )}
        </div>
        <div className="text-2xs text-muted-foreground shrink-0">
          {bucket.limit_display
            ? `Limit ${bucket.limit_display}`
            : "No annual limit"}
        </div>
      </div>
      <div className="mt-1 space-y-1">
        <UtilizationBar
          limit={bucket.limit}
          approved={bucket.approved}
          pending={bucket.pending}
        />
        <AmountRow
          approved={bucket.approved}
          pending={bucket.pending}
          remaining={bucket.remaining}
        />
      </div>
    </div>
  );
}

function FlexSection({ flex }: { flex: FlexUtilization }) {
  const base = flex.flex_balance ?? flex.wallet_amount;
  return (
    <div className="rounded-lg border border-border bg-card p-4 space-y-3">
      <div className="flex items-baseline justify-between gap-2">
        <div className="flex items-center gap-1 text-sm font-medium text-foreground">
          Flexible benefits wallet
          <InfoHint>{BUCKET_HINT}</InfoHint>
        </div>
        {flex.available !== null && (
          <div className="text-sm font-semibold text-foreground">
            {flex.currency ?? ""} {fmtAmount(flex.available)}{" "}
            <span className="text-xs font-normal text-muted-foreground">
              available
            </span>
          </div>
        )}
      </div>

      <UtilizationBar limit={base} approved={flex.approved} pending={flex.pending} />
      <AmountRow
        approved={flex.approved}
        pending={flex.pending}
        remaining={flex.available}
      />

      <div className="grid grid-cols-2 gap-x-4 gap-y-1 text-2xs text-muted-foreground sm:grid-cols-4">
        {flex.wallet_amount !== null && (
          <span>Wallet {fmtAmount(flex.wallet_amount)}</span>
        )}
        {flex.price_tags_total !== null && flex.price_tags_total !== 0 && (
          <span>Coverage price tags −{fmtAmount(flex.price_tags_total)}</span>
        )}
        {flex.flex_balance !== null && (
          <span>Balance {fmtAmount(flex.flex_balance)}</span>
        )}
        <span>Claims approved −{fmtAmount(flex.approved)}</span>
      </div>

      {flex.categories.length > 0 && (
        <div className="border-t border-border pt-2 space-y-1">
          {flex.categories.map((c) => (
            <div key={c.name} className="ml-1 py-1">
              <div className="flex items-baseline justify-between gap-2">
                <span className="text-sm text-foreground">{c.name}</span>
                <span className="text-2xs text-muted-foreground">
                  {c.sub_limit !== null
                    ? `Sub-limit ${fmtAmount(c.sub_limit)}`
                    : "No sub-limit"}
                </span>
              </div>
              <div className="mt-1 space-y-1">
                <UtilizationBar
                  limit={c.sub_limit}
                  approved={c.approved}
                  pending={c.pending}
                />
                <AmountRow
                  approved={c.approved}
                  pending={c.pending}
                  remaining={c.remaining}
                />
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

export function UtilizationView({ data }: { data: Utilization }) {
  const products = data.insured.filter((b) => b.benefit_key === null);
  const subsFor = (product: string | null) =>
    data.insured.filter(
      (b) => b.product_code === product && b.benefit_key !== null,
    );

  if (products.length === 0 && data.flex === null) {
    return (
      <div className="text-sm text-muted-foreground p-6 text-center border border-dashed border-border rounded-md">
        No coverage to track utilization against.
      </div>
    );
  }

  return (
    <div className="space-y-4">
      {products.length > 0 && (
        <div className="rounded-lg border border-border bg-card p-4">
          <div className="flex items-center gap-1 text-sm font-medium text-foreground mb-1">
            Insured benefits
            <InfoHint>{BUCKET_HINT}</InfoHint>
          </div>
          <div className="divide-y divide-border/60">
            {products.map((b) => (
              <div key={`${b.product_code}`}>
                <BucketRow bucket={b} />
                {subsFor(b.product_code).map((s) => (
                  <BucketRow
                    key={`${s.product_code}/${s.benefit_key}`}
                    bucket={s}
                    sub
                  />
                ))}
              </div>
            ))}
          </div>
        </div>
      )}
      {data.flex && <FlexSection flex={data.flex} />}
      <p className="text-2xs text-muted-foreground">
        Pending claims are shown for awareness and aren't subtracted from your
        remaining balance.
      </p>
    </div>
  );
}
