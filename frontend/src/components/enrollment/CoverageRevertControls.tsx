import { useState, type ReactNode } from "react";
import { RotateCcw, Undo2 } from "lucide-react";
import { Button } from "@/components/ui/button";
import { AlertDialog } from "@/components/ui/alert-dialog";
import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from "@/components/ui/tooltip";
import {
  useRevertCoverage,
  useUndoBulk,
  type CoverageChange,
  type CoverageRevertRequest,
} from "@/api/enrollment";
import { toast } from "sonner";
import { Loader2 } from "lucide-react";

/** Tooltip wrapper. The trigger is a `span`, not the button itself: a disabled
 *  button fires no pointer events, and the disabled state is exactly when the
 *  broker most needs to be told why. */
function Explain({ text, children }: { text: ReactNode; children: ReactNode }) {
  return (
    <Tooltip>
      <TooltipTrigger asChild>
        <span className="inline-flex">{children}</span>
      </TooltipTrigger>
      <TooltipContent side="bottom">{text}</TooltipContent>
    </Tooltip>
  );
}

/**
 * Revert a member's coverage to the window baseline or the cohort default.
 * Both actions confirm first (they overwrite effective coverage) and are fully
 * audited server-side. Shown on the employee detail sheet and the elections panel.
 */
export function CoverageRevertControls({
  employeeId,
  hasBaseline,
  baselineDiffers,
  windowId,
}: {
  employeeId: string;
  /** Enable the baseline action only when a window baseline exists for the member. */
  hasBaseline: boolean;
  /**
   * Whether reverting to the baseline lands anywhere other than the cohort
   * default (server-computed: `CoverageHistory.baseline_differs_from_default`).
   * `false` hides the baseline button entirely — the two actions would be the
   * same operation described two ways, which is what made the pair unreadable.
   *
   * `undefined` means "not evaluated here, show it". The elections panel passes
   * nothing: its flag is scoped to the newest baseline while that panel reverts
   * to a NAMED window's, so a false there could hide a button that would really
   * change something. Hiding a correct action is worse than showing a redundant
   * one, and in that panel "revert to baseline" reads unambiguously anyway —
   * the period it means is the one on screen.
   */
  baselineDiffers?: boolean;
  windowId?: string | null;
}) {
  const revert = useRevertCoverage(employeeId);
  const undo = useUndoBulk();
  const [target, setTarget] = useState<"baseline" | "default" | null>(null);
  const [result, setResult] = useState<CoverageChange[] | null>(null);
  // The batch this revert wrote. A revert DELETES overrides, so without a way
  // back a mis-aimed one had to be re-entered by hand — while the bulk
  // equivalent has had undo all along.
  const [batchId, setBatchId] = useState<string | null>(null);

  const run = async () => {
    if (!target) return;
    // Clear the PREVIOUS outcome first. Without this, a revert that fails (a
    // baseline revert 409ing "no baseline available") left the earlier revert's
    // summary and its Undo button on screen, reading as the result of the call
    // that just failed.
    setResult(null);
    setBatchId(null);
    const body: CoverageRevertRequest = { target };
    if (target === "baseline" && windowId) body.window_id = windowId;
    try {
      const res = await revert.mutateAsync(body);
      setResult(res.changes);
      setBatchId(res.batch_id);
    } catch {
      // The global MutationCache onError surfaces the message; just close.
    } finally {
      setTarget(null);
    }
  };

  // An unscoped revert also CLEARS the member's buy/sell-leave trade, and that
  // is a `LeaveElection`, not an override — `undo_batch` restores overrides
  // only, so it cannot put the trade back. Undo must say so, or a broker who
  // reverted someone who had traded 3 days gets the coverage back and the trade
  // (and its wallet impact) stays gone with nothing on screen admitting it.
  const clearedLeave = (result ?? []).some((c) => c.product_code === "(leave)");

  const runUndo = async () => {
    if (!batchId) return;
    try {
      const res = await undo.mutateAsync(batchId);
      // `undo_batch` SKIPS a pair whose coverage moved after the revert rather
      // than clobbering someone else's later work, so "undone" is NOT a given —
      // report what actually happened, the way BatchHistory does.
      const applied = res.counts?.applied ?? 0;
      const skipped = res.counts?.skipped ?? 0;
      const leaveNote = clearedLeave
        ? " The buy/sell-leave trade stays cleared — an undo cannot restore it."
        : "";
      if (applied === 0) {
        toast.warning(
          `Nothing was put back — coverage has changed since this revert${
            skipped > 0 ? ` (${skipped} left as ${skipped === 1 ? "it is" : "they are"})` : ""
          }.${leaveNote}`,
        );
      } else {
        toast.success(
          `Put back ${applied} change${applied === 1 ? "" : "s"}${
            skipped > 0 ? `, ${skipped} left as ${skipped === 1 ? "it is" : "they are"} (changed since)` : ""
          }.${leaveNote}`,
        );
      }
      setResult(null);
      setBatchId(null);
    } catch {
      // Global onError surfaces it (e.g. already_undone).
    }
  };

  const applied = (result ?? []).filter(
    (c) => c.outcome === "reverted" || c.outcome === "reset_to_default",
  );
  // `skipped` is NOT "already correct" — it means the revert deliberately left a
  // live override in place (the product left the member's cohort, or the
  // override predates the baseline) and each row carries the reason. Folded into
  // the applied count it read as "nothing to do", which is the opposite: on a
  // member with no matched categories EVERY product is skipped, and the broker
  // was told their coverage was already at the target state.
  const skipped = (result ?? []).filter((c) => c.outcome === "skipped");

  // Only offer the baseline action when it would land somewhere else.
  const showBaseline = hasBaseline && baselineDiffers !== false;

  return (
    <div className="space-y-2">
      <TooltipProvider delayDuration={150}>
        <div className="flex flex-wrap gap-2">
          {/* No baseline, or a baseline that lands on the cohort default →
              the button is absent, not disabled. A disabled control has to
              justify itself, and both explanations reduce to "the other button
              is the one you want" — which is the only button left. */}
          {showBaseline && (
          <Explain text="Back to the coverage they held when the enrolment period opened — undoing their elections, but keeping any manual change made before the period.">
            <Button
              variant="outline"
              size="sm"
              disabled={revert.isPending}
              onClick={() => setTarget("baseline")}
            >
              <Undo2 className="size-3.5" />
              Revert to baseline
            </Button>
          </Explain>
          )}
          <Explain text="Back to their matched category's plan — wiping every manual change, including ones made before the enrolment period. Goes back further than the baseline.">
            <Button
              variant="outline"
              size="sm"
              disabled={revert.isPending}
              onClick={() => setTarget("default")}
            >
              <RotateCcw className="size-3.5" />
              Reset to default
            </Button>
          </Explain>
        </div>
      </TooltipProvider>

      {result != null && (
        <div className="space-y-1 text-xs">
          <p className="text-muted-foreground">
            {applied.length === 0
              ? skipped.length === 0
                ? "No coverage changed — already at the target state."
                : "No coverage changed."
              : `Reverted ${applied.length} product${applied.length === 1 ? "" : "s"}: ${applied
                  .map((c) => `${c.product_code} → ${c.to_plan ?? "declined"}`)
                  .join(", ")}.`}
          </p>
          {/* Keyed by index: `product_code` is NOT unique here. A baseline
              revert emits one `skipped` from the snapshot walk (the product left
              the cohort) and another from the leftover-override walk for that
              same code — the documented orphan-override case. */}
          {skipped.length > 0 && (
            <ul className="space-y-0.5 text-amber-700">
              {skipped.map((c, i) => (
                <li key={`${c.product_code}-${i}`}>
                  <span className="font-medium">{c.product_code}</span> left
                  unchanged — {c.detail ?? "not covered by this revert."}
                </li>
              ))}
            </ul>
          )}
          {batchId && clearedLeave && (
            <p className="text-amber-700">
              The buy/sell-leave trade was cleared too. Undo restores coverage
              only — it cannot put the trade back.
            </p>
          )}
          {batchId && (
            <Button
              variant="outline"
              size="sm"
              disabled={undo.isPending}
              onClick={() => void runUndo()}
            >
              {undo.isPending ? (
                <Loader2 className="size-3.5 animate-spin" />
              ) : (
                <Undo2 className="size-3.5" />
              )}
              Undo this revert
            </Button>
          )}
        </div>
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
