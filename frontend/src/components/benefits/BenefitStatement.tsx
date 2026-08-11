import type { ReactNode } from "react";
import { FileWarning } from "lucide-react";
import type {
  BenefitStatement as BenefitStatementData,
  Utilization,
} from "@/types";
import { SectionLabel } from "@/components/ui/section-label";
import { fmtMoney } from "@/lib/format";
import { StatementHeader } from "./StatementHeader";
import { CoverageTable } from "./CoverageTable";
import { FlexPanel } from "./FlexPanel";
import { orphanBuckets, PendingSwatch } from "./usage";

/** Claims filed against cover that has since left the statement (a re-parse or
 * a re-match dropped the category). They have no coverage row to sit on, which
 * is the only reason this block exists — everything else the old "Claims
 * utilization" section showed is now on the row it describes. */
function OrphanedClaims({
  buckets,
}: {
  buckets: ReturnType<typeof orphanBuckets>;
}) {
  return (
    <section className="rounded-lg border border-warn/40 bg-warn-soft/25 p-3">
      <SectionLabel as="h3">Claims against cover no longer on this statement</SectionLabel>
      <ul className="mt-1.5 flex flex-col gap-1">
        {buckets.map((b, i) => (
          <li
            key={`${b.product_code}/${b.benefit_key}/${i}`}
            className="flex items-baseline justify-between gap-3 text-xs"
          >
            <span className="min-w-0 break-words text-foreground">
              {b.product_code ?? "—"}
              {b.benefit_key ? ` · ${b.benefit_key}` : ""}
            </span>
            <span className="shrink-0 tabular-nums text-muted-foreground">
              {fmtMoney(b.approved)} approved
              {b.pending > 0 ? ` · ${fmtMoney(b.pending)} pending` : ""}
              {/* Foreign claims with no resolved SGD value are ABSENT from
                  `pending` — they cannot be summed into a policy-currency
                  figure. Naming them is what keeps the total honest; a
                  silently short number is the same class of wrongness the
                  conversion work removed. */}
              {b.pending_unconverted > 0 ? (
                <span className="text-warn">
                  {" · "}
                  {b.pending_unconverted} awaiting conversion
                </span>
              ) : null}
            </span>
          </li>
        ))}
      </ul>
    </section>
  );
}

export function BenefitStatement({
  data,
  utilization,
  actions,
}: {
  data: BenefitStatementData;
  /**
   * Claim usage for the same employee. Optional so a surface can render the
   * statement before (or without) it; when present each coverage row shows what
   * is left, which is the question a broker opens this page to answer.
   */
  utilization?: Utilization | null;
  /** Per-person admin controls, rendered in the identity strip. */
  actions?: ReactNode;
}) {
  const hasFlex = Boolean(data.flex);
  // Gate on what there is to RENDER, not on `is_matched`: `hydrate_plans` skips
  // matched_categories entries whose category was deleted or re-parsed, so a
  // matched employee can still have no coverage lines.
  const hasAnyCoverage = data.coverage.length > 0 || hasFlex;
  const orphans = orphanBuckets(utilization);
  const anyPending =
    (utilization?.insured ?? []).some((b) => b.pending > 0) ||
    (utilization?.flex?.pending ?? 0) > 0;

  return (
    <div className="space-y-4">
      <StatementHeader
        employee={data.employee}
        attributes={data.attributes}
        dependants={data.dependants}
        coverage={data.coverage}
        isMatched={data.is_matched}
        productCount={data.coverage.length}
        hasFlex={hasFlex}
        actions={actions}
      />

      {hasAnyCoverage ? (
        <>
          {data.coverage.length > 0 && (
            <CoverageTable lines={data.coverage} utilization={utilization} />
          )}
          {data.flex && (
            <FlexPanel
              flex={data.flex}
              usage={utilization?.flex}
              employeeId={data.employee.id}
            />
          )}
        </>
      ) : (
        <div className="rounded-lg border border-border bg-card p-8 text-center">
          <FileWarning className="mx-auto size-6 text-muted-foreground" aria-hidden />
          <p className="mt-2 text-sm font-medium text-foreground">
            No coverage assigned
          </p>
          <p className="mt-1 text-xs text-muted-foreground">
            This employee did not match any product category. Review their
            attributes, or re-run matching from Member Listing.
          </p>
        </div>
      )}

      {/* OUTSIDE the coverage branch, deliberately. An employee whose cover was
       * dropped by a re-parse or a re-match has no coverage lines at all — which
       * is precisely when every bucket they own is orphaned. Nested in the
       * branch above, the page said "No coverage assigned" and the approved and
       * pending money simply vanished from the UI. */}
      {orphans.length > 0 && <OrphanedClaims buckets={orphans} />}
      {anyPending && (
        <p className="text-2xs text-muted-foreground">
          <PendingSwatch />
          Pending claims are shown for awareness and aren&rsquo;t subtracted from
          the remaining balance.
        </p>
      )}
    </div>
  );
}
