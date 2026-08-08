/** Fullness — the mount's state, and the rule most likely to be got wrong.
 *
 * **Fullness means UTILISATION, never entitlement.** A mount that reads empty
 * means an unused limit, not a missing benefit. The portal only ever shows what
 * was actually issued to the member, so this bar can never advertise cover they
 * cannot obtain. If you find yourself rendering one for a benefit the member
 * does not hold, the bug is upstream: don't render the mount.
 *
 * Approved is a solid ink and pending is a hatch, so the two are separable by
 * TEXTURE and not by hue alone (WCAG 1.4.1); the figures beneath restate both
 * in words. Pending is shown for awareness and is **never subtracted from what
 * is left** — that is a product rule from `utilization.py`, and drawing it as
 * though it were spent would misreport the member's balance.
 *
 * The fill draws itself from zero on load. This is one of the four things that
 * animate in this world, and it earns it: the motion IS the datum. It is a
 * `scaleX` on the painted bar, never a width transition, so nothing reflows —
 * and `.leaf-grow` resolves to `scaleX(1)` under reduced motion rather than
 * leaving an invisible bar. */
import { cn } from "@/lib/cn";
import { Money, currencySymbol, moneyText } from "./Figure";

/** What was drawn AGAINST this limit, which can never exceed it.
 *
 * A benefit pays UP TO its limit: a member with S$300 left who presents a S$700
 * bill draws S$300 and pays the rest themselves. `approved` is the full sum of
 * approved claims — the right figure on a claim record and in the reports — but
 * against a limit it printed "S$700 of S$300 used", which is not a sentence
 * anyone reads correctly, and drew a bar whose words contradicted its own
 * fullness. The bar and its caption both use this. */
export function drawnAgainst(approved: number, limit: number): number {
  return Math.min(approved, limit);
}

function sentence(
  limit: number,
  approved: number,
  pending: number,
  remaining: number | null,
  currency: string,
): string {
  const parts = [
    `${currency}${moneyText(drawnAgainst(approved, limit))} of ` +
      `${currency}${moneyText(limit)} used`,
  ];
  if (pending > 0)
    parts.push(`${currency}${moneyText(pending)} still under review`);
  if (remaining !== null)
    parts.push(`${currency}${moneyText(Math.max(0, remaining))} left`);
  return `${parts.join(", ")}.`;
}

export function FillRule({
  limit,
  approved,
  pending,
  remaining,
  currency = "S$",
  compact = false,
}: {
  limit: number | null;
  approved: number;
  pending: number;
  remaining: number | null;
  currency?: string | null;
  /** The thinner bar used for secondary limits stacked under the headline one. */
  compact?: boolean;
}) {
  // Symbolised once here: the accessible sentence below is plain text, so it
  // cannot lean on <Money> to do the conversion.
  const cur = currencySymbol(currency);

  // No parsed limit means there is no fullness to express. Saying so plainly
  // beats drawing an empty track, which would read as "nothing is covered".
  //
  // Approved and pending are stated as SEPARATE lines here, never summed. A
  // combined "claimed so far" figure reads as money already settled, and a
  // member whose claims are all still in review would see the total presented
  // as though it had been paid — the same mistake as subtracting pending from
  // a balance, in the one place there is no bar to distinguish them by texture.
  if (limit === null || limit <= 0) {
    if (approved <= 0 && pending <= 0) {
      return <p className="text-row text-label">Nothing claimed yet</p>;
    }
    return (
      <div className="space-y-1">
        {approved > 0 && (
          <div className="flex items-baseline justify-between gap-4">
            <span className="text-row text-label">Approved and paid</span>
            <Money value={approved} currency={cur} emphasis="strong" />
          </div>
        )}
        {pending > 0 && (
          <div className="flex items-baseline justify-between gap-4">
            <span className="text-row text-label">Still under review</span>
            <Money value={pending} currency={cur} />
          </div>
        )}
      </div>
    );
  }

  const drawn = drawnAgainst(approved, limit);
  const approvedPct = Math.max(0, Math.min(100, (drawn / limit) * 100));
  const pendingPct = Math.max(
    0,
    Math.min(100 - approvedPct, (pending / limit) * 100),
  );

  return (
    <div className={compact ? "space-y-2" : "space-y-2.5"}>
      <div
        className={cn(
          "flex w-full overflow-hidden rounded-pill bg-track",
          "shadow-[inset_0_1px_2px_rgb(30_28_24/0.08)]",
          compact ? "h-1.5" : "h-2.5",
        )}
        role="img"
        aria-label={sentence(limit, approved, pending, remaining, cur)}
      >
        <div
          className="leaf-grow h-full bg-strike-approved"
          style={{ width: `${approvedPct}%` }}
        />
        <div
          className="leaf-grow-late h-full bg-[repeating-linear-gradient(45deg,var(--color-strike-pending)_0_3px,transparent_3px_7px)]"
          style={{ width: `${pendingPct}%` }}
        />
      </div>

      {!compact && (
        // Restates the bar in words and figures. The bar is the glance; this is
        // the answer — and it is what a screen reader or a monochrome print
        // falls back to.
        <div className="flex flex-wrap items-baseline justify-between gap-x-4 gap-y-1">
          <span className="text-row text-label">
            <Money value={drawn} currency={cur} className="text-record" /> of{" "}
            <Money value={limit} currency={cur} className="text-record" /> used
            {pending > 0 && (
              <>
                {" · "}
                <Money
                  value={pending}
                  currency={cur}
                  className="text-record"
                />{" "}
                under review
              </>
            )}
          </span>
          {remaining !== null && (
            // Floored, matching `utilization.py`. A limit CAN be drawn past —
            // an acknowledged broker override, or pro-ration shrinking a
            // leaver's allowance below what they already drew — but a member is
            // never "S$200 over the limit": the wallet paid its cap and they
            // paid the rest. The clamp is repeated here rather than trusted
            // from the payload so one stale caller cannot reintroduce
            // "S$-120 left".
            <span className="text-row text-label">
              <Money
                value={Math.max(0, remaining)}
                currency={cur}
                emphasis="strong"
              />{" "}
              left
            </span>
          )}
        </div>
      )}
    </div>
  );
}
