/** "My benefits" — the member's own statement, rendered with the same
 * components as the broker view (financials are stripped server-side). */
import { FileWarning } from "lucide-react";
import { usePortalStatement, usePortalUtilization } from "@/api/portal";
import { BenefitStatement } from "@/components/benefits/BenefitStatement";
import { PortalErrorState } from "@/components/portal/PortalErrorState";
import { Skeleton } from "@/components/ui/skeleton";
import { isNotFoundError } from "@/lib/errors";

export function PortalBenefitsPage() {
  const statement = usePortalStatement();
  // Fetched alongside the statement so each benefit line can show what's left.
  // Never gates rendering: the schedule is still useful if usage fails to load.
  const utilization = usePortalUtilization();

  if (statement.isLoading) {
    return (
      <div className="space-y-4">
        <Skeleton className="h-24 w-full" />
        <Skeleton className="h-48 w-full" />
        <Skeleton className="h-48 w-full" />
      </div>
    );
  }

  // Only a 404 means "no active coverage" — other failures get a retryable
  // error state instead of the confident no-coverage copy.
  if (statement.isError && !isNotFoundError(statement.error)) {
    return <PortalErrorState onRetry={() => void statement.refetch()} />;
  }

  if (statement.isError || !statement.data) {
    return (
      <div className="rounded-lg border border-border bg-card p-8 text-center">
        <FileWarning className="mx-auto size-6 text-muted-foreground" />
        <p className="mt-2 text-sm font-medium text-foreground">
          No active coverage found
        </p>
        <p className="mt-1 text-xs text-muted-foreground">
          Your company doesn't have an active policy year yet, or your record
          isn't on the current roster. Contact your HR or broker.
        </p>
      </div>
    );
  }

  return <BenefitStatement data={statement.data} utilization={utilization.data} />;
}
