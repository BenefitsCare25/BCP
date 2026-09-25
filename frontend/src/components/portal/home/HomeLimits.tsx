import { useMemo, useState } from "react";
import { ArrowUpRight, ChevronDown } from "lucide-react";
import { Link } from "@tanstack/react-router";
import type { Utilization, UtilizationBucket } from "@/types";
import { availableAfterPending } from "@/lib/claimLimits";
import { isNotFoundError } from "@/lib/errors";
import { currencySymbol, moneyText } from "../leaf/Figure";
import { productShortLabel } from "../leaf/glossary";
import { PortalErrorState } from "../PortalErrorState";

type HomeBucket = Omit<UtilizationBucket, "limit_basis"> & {
  limit_basis?: UtilizationBucket["limit_basis"] | "visits_per_year";
  visit_limit?: number | null;
  visits_used?: number;
  visits_remaining?: number | null;
};
type LimitGroup = { code: string; name: string; buckets: HomeBucket[] };

function verifiedBucket(bucket: HomeBucket): boolean {
  if (bucket.orphaned || bucket.limit_is_enforceable !== true) return false;
  return (
    (bucket.limit_basis === "policy_year" && bucket.limit !== null && bucket.limit > 0 && bucket.remaining !== null) ||
    (bucket.limit_basis === "visits_per_year" && (bucket.visit_limit ?? 0) > 0 && bucket.visits_remaining != null)
  );
}

function groupsFor(utilization: Utilization | undefined): LimitGroup[] {
  const groups = new Map<string, LimitGroup>();
  for (const bucket of (utilization?.insured ?? []) as HomeBucket[]) {
    if (!verifiedBucket(bucket)) continue;
    const code = bucket.product_code ?? "other";
    const existing = groups.get(code);
    if (existing) {
      existing.buckets.push(bucket);
    } else {
      groups.set(code, {
        code,
        name: productShortLabel(code, bucket.product_name),
        buckets: [bucket],
      });
    }
  }
  return [...groups.values()];
}

function figureFor(bucket: HomeBucket, currency: string) {
  if (bucket.limit_basis === "visits_per_year") {
    return {
      label: "Visits left",
      value: String(bucket.visits_remaining),
      suffix: "visits",
      detail: `${bucket.visits_used ?? 0} used · ${bucket.visit_limit} annual limit`,
      ratio: bucket.visit_limit ? (bucket.visits_used ?? 0) / bucket.visit_limit : 0,
    };
  }
  const confirmed = bucket.remaining ?? bucket.limit ?? 0;
  const afterPending = availableAfterPending(
    bucket.remaining,
    bucket.pending,
    bucket.pending_unconverted,
  );
  const value = afterPending ?? confirmed;
  return {
    label: afterPending !== null && bucket.pending > 0 ? "Available after pending" : "Available",
    value: `${currency}${moneyText(value)}`,
    suffix: "",
    detail: bucket.pending_unconverted > 0
      ? `${currency}${moneyText(confirmed)} confirmed · currency conversion pending`
      : `${currency}${moneyText(bucket.approved)} used · ${currency}${moneyText(bucket.limit ?? 0)} annual limit`,
    ratio: bucket.limit ? bucket.approved / bucket.limit : 0,
  };
}

export function HomeLimits({
  company,
  utilization,
  isLoading,
  error,
  onRetry,
}: {
  company: string;
  utilization: Utilization | undefined;
  isLoading: boolean;
  error: unknown;
  onRetry: () => void;
}) {
  const groups = useMemo(() => groupsFor(utilization), [utilization]);
  const [productCode, setProductCode] = useState<string | null>(null);
  const [benefitIndex, setBenefitIndex] = useState(0);
  const [allOpen, setAllOpen] = useState(false);
  const selected = groups.find((group) => group.code === productCode) ?? groups[0];
  const bucket = selected?.buckets[Math.min(benefitIndex, selected.buckets.length - 1)];
  const currency = currencySymbol(utilization?.flex?.currency);
  const figure = bucket ? figureFor(bucket, currency) : null;

  if (isLoading) {
    return <section className="portal-glass portal-limits" aria-label="Your limits" aria-busy="true"><div className="portal-panel-loading" /></section>;
  }
  if (error && !isNotFoundError(error)) return <PortalErrorState onRetry={onRetry} />;

  return (
    <section className="portal-glass portal-limits" aria-label="Your limits">
      <div className="portal-limits-head">
        <div className="portal-product-tabs" aria-label="Insurance products">
          {groups.map((group) => (
            <button
              key={group.code}
              type="button"
              className="portal-product-tab"
              aria-pressed={group.code === selected?.code}
              onClick={() => { setProductCode(group.code); setBenefitIndex(0); }}
            >
              {group.name}
            </button>
          ))}
        </div>
        <button type="button" className="portal-text-link portal-all-limits" onClick={() => setAllOpen((open) => !open)} aria-expanded={allOpen}>
          All limits <ArrowUpRight size={17} aria-hidden />
        </button>
      </div>

      {bucket && figure ? (
        <div className="portal-limit-body" aria-live="polite">
          <p className="portal-kicker">{figure.label}</p>
          <p className="portal-limit-figure">{figure.value}{figure.suffix && <span> {figure.suffix}</span>}</p>
          {selected.buckets.length > 1 ? (
            <label className="portal-benefit-picker">
              <select aria-label="Benefit" value={Math.min(benefitIndex, selected.buckets.length - 1)} onChange={(event) => setBenefitIndex(Number(event.target.value))}>
                {selected.buckets.map((item, index) => (
                  <option key={`${item.benefit_key ?? "overall"}-${index}`} value={index}>
                    {item.benefit_key ?? `${selected.name} overall`}
                  </option>
                ))}
              </select>
              <ChevronDown size={17} aria-hidden />
            </label>
          ) : (
            <p className="portal-limit-name">{bucket.benefit_key ?? selected.name}</p>
          )}
          <div className="portal-limit-progress" role="progressbar" aria-label={`${selected.name} limit used`} aria-valuenow={Math.round(Math.min(1, Math.max(0, figure.ratio)) * 100)} aria-valuemin={0} aria-valuemax={100}>
            <span style={{ width: `${Math.min(100, Math.max(0, figure.ratio * 100))}%` }} />
          </div>
          <p className="portal-limit-detail">{figure.detail}</p>
          {bucket.pending > 0 && bucket.limit_basis === "policy_year" && bucket.pending_unconverted === 0 && (
            <p className="portal-limit-pending">Includes {currency}{moneyText(bucket.pending)} in pending claims</p>
          )}
        </div>
      ) : (
        <div className="portal-limit-empty">
          <p className="portal-kicker">Your cover</p>
          <p>No tracked annual limits are available for this year.</p>
          <Link className="portal-text-link" to="/portal/$company/coverage" params={{ company }} search={{ tab: "benefits" }}>Explore your cover <ArrowUpRight size={17} aria-hidden /></Link>
        </div>
      )}

      {allOpen && groups.length > 0 && (
        <div className="portal-all-list">
          {groups.map((group) => (
            <div key={group.code} className="portal-all-group">
              <h3>{group.name}</h3>
              {group.buckets.map((item, index) => {
                const summary = figureFor(item, currency);
                return (
                  <div className="portal-all-row" key={`${item.benefit_key ?? "overall"}-${index}`}>
                    <span>{item.benefit_key ?? "Overall"}</span>
                    <strong>{summary.value}{summary.suffix && ` ${summary.suffix}`}</strong>
                  </div>
                );
              })}
            </div>
          ))}
          <Link className="portal-text-link" to="/portal/$company/coverage" params={{ company }} search={{ tab: "usage" }}>Full usage details <ArrowUpRight size={17} aria-hidden /></Link>
        </div>
      )}
    </section>
  );
}
