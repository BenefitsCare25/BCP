import { useState } from "react";
import { RotateCcw, Undo2 } from "lucide-react";
import { Button } from "@/components/ui/button";
import { AlertDialog } from "@/components/ui/alert-dialog";
import {
  useRevertCoverage,
  type CoverageChange,
  type CoverageRevertRequest,
} from "@/api/enrollment";

/**
 * Revert a member's coverage to the window baseline or the cohort default.
 * Both actions confirm first (they overwrite effective coverage) and are fully
 * audited server-side. Shown on the employee detail sheet and the elections panel.
 */
export function CoverageRevertControls({
  employeeId,
  hasBaseline,
  windowId,
}: {
  employeeId: string;
  /** Enable the baseline action only when a window baseline exists for the member. */
  hasBaseline: boolean;
  windowId?: string | null;
}) {
  const revert = useRevertCoverage(employeeId);
  const [target, setTarget] = useState<"baseline" | "default" | null>(null);
  const [result, setResult] = useState<CoverageChange[] | null>(null);

  const run = async () => {
    if (!target) return;
    const body: CoverageRevertRequest = { target };
    if (target === "baseline" && windowId) body.window_id = windowId;
    try {
      const res = await revert.mutateAsync(body);
      setResult(res.changes);
    } catch {
      // The global MutationCache onError surfaces the message; just close.
    } finally {
      setTarget(null);
    }
  };

  const applied = (result ?? []).filter(
    (c) => c.outcome === "reverted" || c.outcome === "reset_to_default",
  );

  return (
    <div className="space-y-2">
      <div className="flex flex-wrap gap-2">
        <Button
          variant="outline"
          size="sm"
          disabled={!hasBaseline || revert.isPending}
          onClick={() => setTarget("baseline")}
          title={
            hasBaseline
              ? "Restore the coverage this member had when the enrolment period opened"
              : "No enrollment baseline is available for this member"
          }
        >
          <Undo2 className="size-3.5" />
          Revert to baseline
        </Button>
        <Button
          variant="outline"
          size="sm"
          disabled={revert.isPending}
          onClick={() => setTarget("default")}
          title="Drop overrides so the member returns to their cohort default plan"
        >
          <RotateCcw className="size-3.5" />
          Reset to default
        </Button>
      </div>

      {result != null && (
        <p className="text-xs text-muted-foreground">
          {applied.length === 0
            ? "No coverage changed — already at the target state."
            : `Reverted ${applied.length} product${applied.length === 1 ? "" : "s"}: ${applied
                .map((c) => `${c.product_code} → ${c.to_plan ?? "declined"}`)
                .join(", ")}.`}
        </p>
      )}

      <AlertDialog
        open={target != null}
        onOpenChange={(o) => !o && setTarget(null)}
        title={
          target === "baseline"
            ? "Revert to the enrolment-period baseline?"
            : "Reset to cohort default?"
        }
        description={
          target === "baseline"
            ? "This restores the coverage this member had when the enrolment period opened, replacing any later plan changes. The change is audited."
            : "This removes the member's plan overrides so they return to their matched-category default plan and dependant coverage. The change is audited."
        }
        confirmLabel="Revert"
        confirmVariant="default"
        loading={revert.isPending}
        onConfirm={run}
      />
    </div>
  );
}
