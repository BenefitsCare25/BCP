/** "What's covered" — the member's own leaf. */
import { usePortalStatement, usePortalUtilization } from "@/api/portal";
import { CoverageLeaf } from "@/components/portal/leaf/CoverageLeaf";
import { Mount } from "@/components/portal/leaf/Mount";
import { PortalErrorState } from "@/components/portal/PortalErrorState";
import { LeafSkeleton } from "@/components/portal/leaf/LeafSkeleton";
import { isNotFoundError } from "@/lib/errors";

export function PortalBenefitsPage() {
  const statement = usePortalStatement();
  // Fetched alongside the statement so each benefit line can show what's left.
  // Never gates rendering: the schedule is still useful if usage fails to load.
  const utilization = usePortalUtilization();

  if (statement.isLoading) return <LeafSkeleton label="Loading your benefits" />;

  // Only a 404 means "no active coverage" — other failures get a retryable
  // error state instead of the confident no-coverage copy.
  if (statement.isError && !isNotFoundError(statement.error)) {
    return <PortalErrorState onRetry={() => void statement.refetch()} />;
  }

  if (statement.isError || !statement.data) {
    return (
      <Mount label="No benefits on record">
        <p className="text-row text-label">
          We don't have any benefits recorded against your name for this
          period. This usually means your company's cover for the year hasn't
          been finalised yet. Your HR team can tell you where things stand.
        </p>
      </Mount>
    );
  }

  return (
    <CoverageLeaf data={statement.data} utilization={utilization.data} />
  );
}
