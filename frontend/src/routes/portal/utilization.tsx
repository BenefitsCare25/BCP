/** "What's left" — how much of each benefit the member has used. */
import { usePortalUtilization } from "@/api/portal";
import { UsageLeaf } from "@/components/portal/leaf/UsageLeaf";
import { Mount } from "@/components/portal/leaf/Mount";
import { LeafSkeleton } from "@/components/portal/leaf/LeafSkeleton";
import { PortalErrorState } from "@/components/portal/PortalErrorState";
import { isNotFoundError } from "@/lib/errors";

export function PortalUtilizationPage() {
  const { data, isLoading, isError, error, refetch } = usePortalUtilization();

  if (isLoading) return <LeafSkeleton label="Loading your balances" mounts={2} />;

  if (isError && !isNotFoundError(error)) {
    return <PortalErrorState onRetry={() => void refetch()} />;
  }

  if (isError || !data) {
    return (
      <Mount label="Nothing to show yet">
        <p className="text-row text-label">
          We don't have any benefits recorded against your name for this
          period, so there's nothing to track yet. Your HR team can check your
          record.
        </p>
      </Mount>
    );
  }

  return <UsageLeaf data={data} />;
}
