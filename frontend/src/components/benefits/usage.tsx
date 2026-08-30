/** Claim-usage primitives shared by the broker coverage table and the flex
 * panel.
 *
 * This replaces the old `UtilizationView`, which rendered a "Claims
 * utilization" section listing every product bucket a second time. Those
 * product buckets are emitted 1:1 with the statement's coverage lines
 * (`utilization.py::_insured_buckets`), so the section was a verbatim copy of
 * the coverage list — and because a bucket exists whether or not anyone has
 * claimed, a roster with no claims yet rendered one dead row per product
 * reading "No annual limit · Approved 0", with no bar (the bar draws nothing
 * without a numeric limit). Usage now lives ON the coverage row it belongs to,
 * and a row with nothing to report says nothing.
 *
 * The one thing the old section carried that a coverage row cannot is a bucket
 * with no coverage line behind it — see `orphanBuckets`.
 */
import type { UtilizationBucket, Utilization } from "@/types";
import { fmtMoney } from "@/lib/format";
import { cn } from "@/lib/cn";
import { availableAfterPending } from "@/lib/claimLimits";
import { AlertTriangle } from "lucide-react";

const PENDING_HATCH = {
  backgroundImage:
    "repeating-linear-gradient(45deg, var(--color-warn) 0 4px, var(--color-warn-soft) 4px 8px)",
} as const;

/** Approved (solid) / pending (hatched) / remaining (track). Renders nothing
 * without a numeric limit — there is no proportion to draw. */
export function UtilizationBar({
  limit,
  approved,
  pending,
  className,
}: {
  limit: number | null;
  approved: number;
  pending: number;
  className?: string;
}) {
  if (limit === null || limit <= 0) return null;
  const approvedPct = Math.min(100, (approved / limit) * 100);
  const pendingPct = Math.min(100 - approvedPct, (pending / limit) * 100);
  return (
    <div
      className={`flex h-1.5 w-full overflow-hidden rounded-full bg-muted ${className ?? ""}`}
    >
      <div
        className="h-full bg-good"
        style={{ width: `${approvedPct}%` }}
        title={`Approved ${fmtMoney(approved)}`}
      />
      <div
        className="h-full"
        style={{ width: `${pendingPct}%`, ...PENDING_HATCH }}
        title={`Pending ${fmtMoney(pending)}`}
      />
    </div>
  );
}

/** The small hatched square used to gloss "pending" in a legend. */
export function PendingSwatch() {
  return (
    <span
      aria-hidden
      className="mr-1 inline-block size-2 rounded-sm align-[-1px]"
      style={PENDING_HATCH}
    />
  );
}

export function hasActivity(b: UtilizationBucket | null | undefined): boolean {
  return Boolean(b && (b.approved > 0 || b.pending > 0));
}

/**
 * One product's claim position, for the coverage table's Claims column.
 *
 * Every branch here exists to avoid printing a row that says nothing:
 * no limit and no claims renders `—`, and a limit that is only expressible as
 * text ("As charged", "$650/day") is printed as that text rather than as the
 * old, false "No annual limit".
 */
export function ClaimPosition({
  bucket,
  align = "right",
}: {
  bucket: UtilizationBucket | null | undefined;
  align?: "right" | "left";
}) {
  const side = align === "right" ? "text-right" : "text-left";
  const nothing = <span className={cn("block text-subtle", side)}>—</span>;
  if (!bucket) return nothing;

  const {
    approved,
    pending,
    pending_unconverted,
    limit,
    remaining,
    limit_display,
    limit_status,
    limit_is_enforceable,
  } = bucket;
  const quiet = !hasActivity(bucket);

  if (limit_status === "needs_review") {
    return (
      <div className={cn("flex flex-col gap-0.5", side)}>
        <span className="text-xs font-medium text-warn">Needs review · not live</span>
        {limit_display && (
          <span className="text-2xs text-muted-foreground">{limit_display}</span>
        )}
      </div>
    );
  }

  // Nothing claimed and no stated limit: there is no fact to report.
  if (quiet && limit === null && !limit_display) return nothing;

  // No `items-end` below: the bar is `w-full`, and cross-axis end-alignment
  // would shrink it to its content width, i.e. to nothing.
  return (
    <div className={cn("flex flex-col gap-0.5", side)}>
      {limit !== null && remaining !== null && limit_is_enforceable === true ? (
        <>
          {/* Floored, matching `utilization.py` — a benefit pays UP TO its
            * limit, so "$300 over limit" is not a position anyone is in. The
            * clamp is repeated here so a stale payload cannot reintroduce
            * "-$300 left". What was actually approved stays on the claim
            * record and in the reports. */}
          <span className="font-medium tabular-nums text-foreground">
            {pending > 0 && pending_unconverted === 0
              ? `${fmtMoney(
                  availableAfterPending(remaining, pending) ?? remaining,
                )} available after pending`
              : `${fmtMoney(Math.max(0, remaining))} left`}
          </span>
          <span className="text-2xs tabular-nums text-muted-foreground">
            of {fmtMoney(limit)}
            {pending > 0 && ` · ${fmtMoney(remaining)} confirmed`}
          </span>
          <UtilizationBar
            limit={limit}
            approved={approved}
            pending={pending}
            className="mt-0.5"
          />
        </>
      ) : (
        <>
          {/* "$0 claimed" beside "$8,703.48 pending" is the dead phrasing this
            * redesign removes: nothing has been approved, so there is no
            * approved figure to state. The pending line below carries it. */}
          {approved > 0 && (
            <span className="font-medium tabular-nums text-foreground">
              {fmtMoney(approved)} claimed
            </span>
          )}
          {limit_display && (
            <span className="text-2xs text-muted-foreground">
              {quiet ? limit_display : `limit ${limit_display}`}
            </span>
          )}
        </>
      )}
      {pending > 0 && (
        <span className="text-2xs tabular-nums text-warn">
          <PendingSwatch />
          {fmtMoney(pending)} pending
        </span>
      )}
      {bucket.limit_unparsed === true && (
        <span
          className="inline-flex items-center gap-1 text-2xs text-warn"
          title={
            limit_display
              ? `The over-limit guard can't read "${limit_display}" as an annual amount, so approvals aren't checked against it.`
              : "The over-limit guard is inactive for this benefit."
          }
        >
          <AlertTriangle className="size-3 shrink-0" />
          Limit not machine-readable
        </span>
      )}
    </div>
  );
}

export interface ProductUsage {
  /** The product-level roll-up (`benefit_key === null`). */
  product: UtilizationBucket | null;
  /** Per-benefit buckets, keyed by the lowercased benefit name — the same join
   * key `lib/benefitSchedule.ts::usageFor` uses to merge usage into a schedule
   * row. */
  byBenefit: Map<string, UtilizationBucket>;
}

/** Group the flat bucket list by product once, so a statement with eleven
 * coverage lines doesn't filter the same array eleven times. */
export function indexUsage(
  utilization: Utilization | null | undefined,
): Map<string, ProductUsage> {
  const out = new Map<string, ProductUsage>();
  for (const b of utilization?.insured ?? []) {
    if (b.orphaned || !b.product_code) continue;
    let entry = out.get(b.product_code);
    if (!entry) {
      entry = { product: null, byBenefit: new Map() };
      out.set(b.product_code, entry);
    }
    // FIRST product bucket wins, never the last. `product_code` is not unique
    // across a statement (`hydrate_plans` emits a line per matched CATEGORY),
    // and `_insured_buckets` pops the claim sums on the first line it sees for
    // a code — so every LATER line gets a bucket of zeroes with the limit
    // un-drawn. Overwriting would show "nothing claimed, full limit left" on
    // both rows of a doubled product, which is the one wrong answer here.
    if (b.benefit_key === null) entry.product ??= b;
    else if (!entry.byBenefit.has(b.benefit_key.trim().toLowerCase())) {
      entry.byBenefit.set(b.benefit_key.trim().toLowerCase(), b);
    }
  }
  return out;
}

/** Buckets whose coverage is no longer on the statement (coverage changed after
 * the claim was submitted). These have no row to attach to, so they are the one
 * part of the retired utilization section that still needs its own place.
 *
 * ROLL-UPS ONLY. `_bucket_sums` records every insured claim twice — once under
 * `(product, None)` and once under `(product, benefit_key)` — and for a product
 * that left the statement neither is popped, so both come back flagged
 * `orphaned`. Listed flat, one $500 claim printed as "GHS $500" AND "GHS ·
 * Room & Board $500", which reads as $1,000. The roll-up carries the whole
 * amount for the product, so filtering to it is complete, not lossy. (The old
 * `UtilizationView` nested the benefit rows under the roll-up, which is why it
 * could show both.) */
export function orphanBuckets(
  utilization: Utilization | null | undefined,
): UtilizationBucket[] {
  return (utilization?.insured ?? []).filter(
    (b) => b.orphaned && b.benefit_key === null,
  );
}
