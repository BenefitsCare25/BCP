/**
 * Past coverage changes — what ran, and the two things you want afterwards.
 *
 * These records have always been written and were read by nothing, which meant a
 * batch applied to 400 people left no trace anyone could open. Two actions hang
 * off them:
 *
 * - **Re-run this selection** loads the stored RULE back into the builder. It
 *   does not re-apply anything: the roster has moved, so the population is
 *   re-resolved and re-previewed like any other run.
 * - **Undo** puts each member back to what this batch replaced, as a NEW batch.
 *   A pair somebody has moved since is skipped and reported, never overwritten.
 */
import { Loader2, RotateCcw, Undo2 } from "lucide-react";
import { toast } from "sonner";
import {
  type BulkBatchDetail,
  type BulkBatchSummary,
  useFetchBulkBatch,
  useUndoBulk,
} from "@/api/enrollment";
import { formatError } from "@/lib/errors";
import { fmtDateTime } from "@/lib/format";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { AlertDialog } from "@/components/ui/alert-dialog";
import { useState } from "react";

export function BatchHistory({
  batches,
  loading,
  onReRun,
}: {
  batches: BulkBatchSummary[] | undefined;
  loading: boolean;
  onReRun: (detail: BulkBatchDetail) => void;
}) {
  const fetchBatch = useFetchBulkBatch();
  const undo = useUndoBulk();
  const [confirming, setConfirming] = useState<BulkBatchSummary | null>(null);

  if (loading) {
    return (
      <p className="flex items-center gap-2 text-xs text-muted-foreground">
        <Loader2 className="size-3.5 animate-spin" /> Loading past changes…
      </p>
    );
  }
  if (!batches?.length) {
    return (
      <p className="text-sm text-muted-foreground">
        No coverage changes have been applied to this benefit year yet.
      </p>
    );
  }

  function reRun(batch: BulkBatchSummary) {
    fetchBatch.mutate(batch.id, {
      onSuccess: (detail) => {
        onReRun(detail);
        toast.success("Selection loaded — preview it against today's roster.");
      },
      onError: (e) => toast.error(formatError(e)),
    });
  }

  function runUndo(batch: BulkBatchSummary) {
    undo.mutate(batch.id, {
      onSuccess: (res) => {
        setConfirming(null);
        const skipped = res.superseded.length;
        toast.success(
          `Put back ${res.counts.applied ?? 0} change(s)` +
            (skipped ? ` · ${skipped} left alone (changed since)` : ""),
        );
      },
      onError: (e) => {
        setConfirming(null);
        toast.error(formatError(e));
      },
    });
  }

  return (
    <>
      <ul className="divide-y divide-border">
        {batches.map((b) => {
          const isUndo = !!b.undo_of;
          return (
            <li
              key={b.id}
              className="flex flex-wrap items-center gap-x-3 gap-y-2 py-3 first:pt-0 last:pb-0"
            >
              <span className="text-sm text-foreground">
                {b.product_codes.join(", ") || "—"}
              </span>
              <span className="text-xs text-muted-foreground">
                {b.created_at ? fmtDateTime(b.created_at) : "—"}
              </span>
              <span className="text-xs text-muted-foreground">
                {b.counts.applied ?? 0} changed
                {b.counts.no_change ? ` · ${b.counts.no_change} already set` : ""}
                {b.counts.error ? ` · ${b.counts.error} error` : ""}
              </span>
              {isUndo && <Badge variant="outline">Undo</Badge>}
              {b.undone_by && <Badge variant="outline">Undone</Badge>}
              {b.acknowledged.length > 0 && (
                <Badge variant="outline">
                  {b.acknowledged.length} warning
                  {b.acknowledged.length === 1 ? "" : "s"} accepted
                </Badge>
              )}

              {b.not_restorable > 0 && (
                <Badge variant="outline" className="text-warn">
                  {b.not_restorable} not undoable
                </Badge>
              )}

              <span className="ml-auto flex items-center gap-1">
                {/* An undo record carries the SOURCE batch's rule and change set
                    (that is what makes its detail readable), so re-running it
                    would load the very change that was just reversed — under a
                    row badged "Undo". Re-run the original instead. */}
                {!isUndo && (
                  <Button
                    variant="ghost"
                    size="sm"
                    disabled={fetchBatch.isPending}
                    onClick={() => reRun(b)}
                  >
                    <RotateCcw className="size-4" /> Re-run selection
                  </Button>
                )}
                {/* An undo is not itself undoable, and a batch that recorded no
                    previous state has nothing to put back — offering the button
                    there would promise something that cannot happen. */}
                {!isUndo && !b.undone_by && b.restorable > 0 && (
                  <Button
                    variant="ghost"
                    size="sm"
                    disabled={undo.isPending}
                    onClick={() => setConfirming(b)}
                  >
                    <Undo2 className="size-4" /> Undo
                  </Button>
                )}
              </span>
            </li>
          );
        })}
      </ul>

      <AlertDialog
        open={!!confirming}
        onOpenChange={(open) => !open && setConfirming(null)}
        title="Put this coverage change back?"
        description={
          `${confirming?.restorable ?? 0} coverage change(s) go back to what this ` +
          "batch replaced. Anyone whose coverage has moved since is left alone and " +
          "reported. This is recorded as a new change, not a deletion." +
          (confirming?.not_restorable
            ? ` ${confirming.not_restorable} more were applied but not recorded ` +
              "in detail (the batch was too large), and will stay on their new " +
              "coverage."
            : "")
        }
        confirmLabel="Undo"
        confirmVariant="default"
        loading={undo.isPending}
        onConfirm={() => {
          if (confirming) runUndo(confirming);
        }}
      />
    </>
  );
}
