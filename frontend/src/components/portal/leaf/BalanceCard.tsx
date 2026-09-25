/** One benefit's balance on "What's left", in the same world as Home's sheets:
 *  the benefit's tone and clay object, the amount LEFT set large beside a ring
 *  that fills as it is used (pending drawn lighter, on top of used), and three
 *  quiet stats. Anything else the product tracks sits below as slim rows.
 *
 *  Money only: a visits cap renders through `VisitRow`, never as dollars. */
import type { ReactNode } from "react";
import type { UtilizationBucket } from "@/types";
import { cn } from "@/lib/cn";
import { availableAfterPending } from "@/lib/claimLimits";
import { careArt, careTone, productRouteKey } from "./careTone";
import { currencySymbol, moneyText } from "./Figure";
import { productShortLabel } from "./glossary";

function Ring({ used, pending }: { used: number; pending: number }) {
  const r = 46;
  const c = 2 * Math.PI * r;
  const u = Math.min(1, Math.max(0, used));
  const p = Math.min(1 - u, Math.max(0, pending));
  return (
    <svg viewBox="0 0 110 110" className="size-24 shrink-0 -rotate-90 sm:size-32" aria-hidden>
      <circle cx="55" cy="55" r={r} fill="none" stroke="white" strokeOpacity="0.85" strokeWidth="12" />
      {p > 0 && (
        <circle cx="55" cy="55" r={r} fill="none" stroke="var(--tone-pill)" strokeWidth="12" strokeLinecap="round"
          strokeDasharray={`${(u + p) * c} ${c}`} />
      )}
      {u > 0 && (
        <circle cx="55" cy="55" r={r} fill="none" stroke="var(--tone-ink)" strokeWidth="12" strokeLinecap="round"
          strokeDasharray={`${u * c} ${c}`} className="transition-[stroke-dasharray] duration-700" />
      )}
    </svg>
  );
}

export function BalanceCard({
  productCode,
  productName,
  bucket,
  subLabel,
  children,
}: {
  productCode: string | null;
  productName: string | null;
  /** The balance this card leads with — the product cap, or its first sub-limit. */
  bucket: UtilizationBucket;
  /** Set when the lead balance is a sub-limit, so it is never read as the whole benefit. */
  subLabel?: string | null;
  /** Further tracked rows for the same product, and the pending breakdown. */
  children?: ReactNode;
}) {
  const route = productRouteKey(productCode);
  const art = careArt(route);
  const s = currencySymbol(null);
  const limit = bucket.limit ?? 0;
  const afterPending = availableAfterPending(bucket.remaining, bucket.pending, bucket.pending_unconverted);
  const left = afterPending ?? bucket.remaining ?? 0;
  const name = productCode ? productShortLabel(productCode, productName) : productName ?? "Benefit";
  const usedPct = limit ? Math.round((bucket.approved / limit) * 100) : 0;
  return (
    <section className={cn(`tone-${careTone(route)}`, "relative overflow-hidden rounded-[28px] bg-[var(--tone-wash)] text-[var(--tone-ink)]")}>
      <div className="grid grid-cols-[auto_minmax(0,1fr)] items-center gap-4 p-4 sm:grid-cols-[auto_minmax(0,1fr)_auto] sm:gap-7 sm:p-6">
        <div className="relative">
          <Ring used={limit ? bucket.approved / limit : 0} pending={limit ? bucket.pending / limit : 0} />
          <span className="absolute inset-0 grid place-items-center text-center leading-tight">
            <span>
              <span className="block text-lg font-bold text-record sm:text-xl">{usedPct}%</span>
              <span className="block text-2xs font-semibold uppercase tracking-wider">used</span>
            </span>
          </span>
        </div>

        <div className="min-w-0">
          <div className="flex flex-wrap items-center gap-x-3 gap-y-2">
            <span className="clay-tag">{name}</span>
            {subLabel && <span className="min-w-0 text-row font-semibold">{subLabel}</span>}
          </div>
          <p className="mt-3 flex flex-wrap items-baseline gap-x-2.5">
            <span className="text-[clamp(30px,4vw,44px)] font-bold leading-none tracking-[-0.04em] text-record">
              {s}{moneyText(left)}
            </span>
            <span className="text-row font-semibold">{bucket.pending > 0 ? "left after claims in review" : "left this year"}</span>
          </p>
          <dl className="mt-3 flex flex-wrap gap-2">
            {[
              ["Used", bucket.approved],
              ["In review", bucket.pending],
              ["Yearly limit", limit],
            ].map(([label, value]) => (
              <div key={label as string} className="flex items-baseline gap-1.5 rounded-full bg-white/80 px-3 py-1.5">
                <dt className="text-2xs font-bold uppercase tracking-wider">{label}</dt>
                <dd className="text-row font-bold text-record">{s}{moneyText(value as number)}</dd>
              </div>
            ))}
          </dl>
        </div>

        {art && <img src={art} alt="" className="pointer-events-none hidden size-32 object-contain sm:block lg:size-36" />}
      </div>
      {children && <div className="space-y-2 px-4 pb-4 sm:px-6 sm:pb-6">{children}</div>}
    </section>
  );
}

/** A further tracked sub-limit inside a balance card: name, slim meter, figure. */
export function SubBalanceRow({ bucket }: { bucket: UtilizationBucket }) {
  const s = currencySymbol(null);
  if (bucket.visit_limit != null && bucket.visits_remaining != null) {
    const cap = bucket.visit_limit;
    return (
      <div className="rounded-2xl bg-white/70 px-4 py-3">
        <div className="flex items-baseline justify-between gap-3">
          <span className="min-w-0 text-row font-semibold text-record">{bucket.benefit_key}</span>
          <span className="shrink-0 text-row font-bold text-record">{bucket.visits_remaining} of {cap} visits left</span>
        </div>
        <div className="mt-2 h-1.5 overflow-hidden rounded-full bg-white">
          <div className="h-full rounded-full bg-[var(--tone-ink)]" style={{ width: `${cap ? ((bucket.visits_used ?? 0) / cap) * 100 : 0}%` }} />
        </div>
      </div>
    );
  }
  const limit = bucket.limit ?? 0;
  return (
    <div className="rounded-2xl bg-white/70 px-4 py-3">
      <div className="flex items-baseline justify-between gap-3">
        <span className="min-w-0 text-row font-semibold text-record">{bucket.benefit_key}</span>
        <span className="shrink-0 text-row font-bold text-record">{s}{moneyText(bucket.remaining ?? 0)} left</span>
      </div>
      <div className="mt-2 h-1.5 overflow-hidden rounded-full bg-white">
        <div className="h-full rounded-full bg-[var(--tone-ink)]" style={{ width: `${limit ? Math.min(100, (bucket.approved / limit) * 100) : 0}%` }} />
      </div>
    </div>
  );
}

/** A product whose only tracked limits are visit counts: tag + rows, no ring. */
export function BalanceCardShell({
  productCode,
  productName,
  children,
}: {
  productCode: string | null;
  productName: string | null;
  children: ReactNode;
}) {
  const route = productRouteKey(productCode);
  const name = productCode ? productShortLabel(productCode, productName) : productName ?? "Benefit";
  return (
    <section className={cn(`tone-${careTone(route)}`, "rounded-[28px] bg-[var(--tone-wash)] p-5 text-[var(--tone-ink)] sm:p-7")}>
      <span className="clay-tag">{name}</span>
      <div className="mt-4 space-y-2">{children}</div>
    </section>
  );
}
