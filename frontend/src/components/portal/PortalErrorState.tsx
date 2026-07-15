/** Shared inline error state for portal pages. Portal queries opt out of the
 * global error toast (`meta.localErrorHandling` + `retry: false`), so a failed
 * fetch must render here — distinct from the confident "no data" empty states,
 * which are reserved for real 404s. */
import { AlertTriangle, RefreshCw } from "lucide-react";
import { Button } from "@/components/ui/button";

export function PortalErrorState({ onRetry }: { onRetry: () => void }) {
  return (
    <div className="rounded-lg border border-border bg-card p-8 text-center">
      <AlertTriangle className="mx-auto size-6 text-muted-foreground" />
      <p className="mt-2 text-sm font-medium text-foreground">
        Something went wrong loading this page
      </p>
      <p className="mt-1 text-xs text-muted-foreground">
        Please try again. If the problem persists, contact your benefits
        administrator.
      </p>
      <Button
        type="button"
        variant="outline"
        size="sm"
        className="mt-3"
        onClick={onRetry}
      >
        <RefreshCw className="size-4" />
        <span className="ml-1">Retry</span>
      </Button>
    </div>
  );
}
