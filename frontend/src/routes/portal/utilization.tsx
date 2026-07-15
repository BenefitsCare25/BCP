import { Loader2 } from "lucide-react";
import { usePortalUtilization } from "@/api/portal";
import { UtilizationView } from "@/components/benefits/UtilizationView";
import { PortalErrorState } from "@/components/portal/PortalErrorState";
import { isNotFoundError } from "@/lib/errors";

export function PortalUtilizationPage() {
  const { data, isLoading, isError, error, refetch } = usePortalUtilization();

  return (
    <div className="space-y-4">
      <div>
        <h1 className="text-lg font-semibold text-foreground">My usage</h1>
        <p className="text-sm text-muted-foreground">
          How much of each benefit you've used this policy year.
        </p>
      </div>

      {isLoading ? (
        <div className="flex items-center gap-2 text-sm text-muted-foreground py-8">
          <Loader2 className="size-4 animate-spin" /> Loading usage…
        </div>
      ) : isError && !isNotFoundError(error) ? (
        <PortalErrorState onRetry={() => void refetch()} />
      ) : isError || !data ? (
        <div className="rounded-lg border border-dashed border-border p-8 text-center text-sm text-muted-foreground">
          No active coverage found for your account. If you believe this is a
          mistake, contact your benefits administrator.
        </div>
      ) : (
        <UtilizationView data={data} />
      )}
    </div>
  );
}
