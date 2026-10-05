import { useWindowReadiness } from "@/api/enrollment";
import { Button } from "@/components/ui/button";
import { formatError } from "@/lib/errors";
import { ReadinessChecklist } from "./ReadinessChecklist";

export function ReadinessReview({ windowId }: { windowId: string }) {
  const readiness = useWindowReadiness(windowId);
  return <div className="space-y-4">
    <h3 className="text-sm font-semibold text-foreground">Validation checks</h3>
    {readiness.isError ? <div role="alert" className="space-y-2 text-sm text-error">
      <p>Could not load validation checks. You can still open this period. {formatError(readiness.error)}</p>
      <Button variant="outline" size="sm" onClick={() => void readiness.refetch()}>Retry validation checks</Button>
    </div> : <ReadinessChecklist windowId={windowId} readiness={readiness.data} isLoading={readiness.isLoading} />}
  </div>;
}
