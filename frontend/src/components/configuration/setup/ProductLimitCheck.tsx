import { useMemo } from "react";
import { CircleCheck, Loader2, TriangleAlert } from "lucide-react";
import { useCoverageLimits, type LimitPreview } from "@/api/coverageLimits";
import { LimitAlertList } from "@/components/limits/LimitAlertList";
import { useDebouncedValue } from "@/lib/use-debounced-value";

/**
 * Who on the current employee listing crosses this product's limits, checked
 * against the values in the form — so typing "70" into Employee Age Limit
 * shows at once who that would affect. Nothing is saved or changed by it.
 */
export function ProductLimitCheck({
  policyYearId,
  productCode,
  eligibility,
  maxSumInsured,
}: {
  policyYearId: string;
  productCode: string;
  eligibility: Record<string, string>;
  maxSumInsured: string;
}) {
  const preview = useMemo<LimitPreview>(
    () => ({
      productCode,
      employee_age_limit: eligibility.employee_age_limit ?? "",
      last_entry_age: eligibility.last_entry_age ?? "",
      spouse_age_limit: eligibility.spouse_age_limit ?? "",
      child_age_limit: eligibility.child_age_limit ?? "",
      employees_above_last_entry_age: eligibility.employees_above_last_entry_age ?? "",
      max_sum_insured: maxSumInsured,
    }),
    [productCode, eligibility, maxSumInsured],
  );
  const debounced = useDebouncedValue(preview, 500);
  const { data, isLoading, isError, isFetching } = useCoverageLimits(policyYearId, debounced);
  const alerts = data?.alerts ?? [];
  const people = new Set(alerts.map((a) => a.dependant_id ?? a.employee_id)).size;
  const actionable = alerts.some((a) => a.severity === "action");

  return (
    <div
      role="region"
      aria-label={`${productCode} limit check`}
      className="rounded-lg border border-border bg-card p-3"
    >
      <div className="flex flex-wrap items-center gap-x-2 gap-y-1">
        {isLoading ? (
          <Loader2 aria-hidden className="size-4 animate-spin text-muted-foreground" />
        ) : alerts.length ? (
          <TriangleAlert
            aria-hidden
            className={actionable ? "size-4 text-warn" : "size-4 text-muted-foreground"}
          />
        ) : (
          <CircleCheck aria-hidden className="size-4 text-good" />
        )}
        <h3 className="text-sm font-semibold text-foreground">
          {isLoading
            ? "Checking the employee listing…"
            : isError
              ? "The limit check is unavailable"
              : alerts.length
                ? `${people} ${people === 1 ? "person crosses" : "people cross"} these limits`
                : "No one on the employee listing crosses these limits"}
        </h3>
        {isFetching && !isLoading && (
          <span className="text-2xs text-muted-foreground">Updating…</span>
        )}
      </div>
      <p className="mt-0.5 text-xs text-muted-foreground">
        Ages are taken at the benefit year start. Updates as you edit the limits
        above; nothing changes until a broker acts on it.
      </p>
      {alerts.length > 0 && (
        <div className="mt-3">
          <LimitAlertList alerts={alerts} />
        </div>
      )}
    </div>
  );
}
