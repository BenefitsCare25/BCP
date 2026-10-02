import { SystemAdminOnly } from "@/components/auth/SystemAdminOnly";
/** One primary action for where the member stands, with the reason when an
 * action is unavailable — instead of four equal buttons (Save · Submit ·
 * Confirm · Discard) that each worked in some states only. */
import { useState } from "react";
import { CheckCircle2, Loader2, LockOpen, RotateCcw, Send } from "lucide-react";
import type { EnrollmentStatus, WindowPhase } from "@/api/enrollment";
import { AlertDialog } from "@/components/ui/alert-dialog";
import { Button } from "@/components/ui/button";
import { fmtWhen } from "@/components/enrollment/period/periodMeta";
import type { ElectionActions } from "./useElectionActions";

export function ElectionActionBar({
  status,
  phase,
  opensAt,
  editable,
  overdrawn,
  actions: a,
}: {
  status: EnrollmentStatus;
  phase: WindowPhase;
  opensAt: string;
  editable: boolean;
  overdrawn: boolean;
  actions: ElectionActions;
}) {
  const [confirmReset, setConfirmReset] = useState(false);
  const submitted = status === "submitted";
  return (
    <div className="flex flex-wrap items-center gap-2 border-t border-border pt-4">
      {submitted && (
        <Button onClick={a.doConfirm} disabled={a.busy}>
          {a.pending.confirm ? (
            <Loader2 className="size-4 animate-spin" aria-hidden />
          ) : (
            <CheckCircle2 className="size-4" aria-hidden />
          )}
          Confirm
        </Button>
      )}
      {editable && !submitted && (
        <Button
          onClick={() => a.submitAndConfirm(false)}
          disabled={a.busy || overdrawn}
          title={overdrawn ? "These choices cost more than the wallet holds" : undefined}
        >
          {a.pending.submit ? (
            <Loader2 className="size-4 animate-spin" aria-hidden />
          ) : (
            <Send className="size-4" aria-hidden />
          )}
          Submit &amp; confirm
        </Button>
      )}
      {editable && (
        <Button variant="outline" onClick={() => void a.saveDraft()} disabled={a.busy}>
          {submitted ? "Save changes" : "Save as draft"}
        </Button>
      )}
      {editable && status !== "not_started" && (
        <SystemAdminOnly><Button variant="ghost" onClick={() => setConfirmReset(true)} disabled={a.pending.reset}>
          <RotateCcw className="size-4" aria-hidden /> Discard changes
        </Button></SystemAdminOnly>
      )}
      {status === "confirmed" && phase === "open" && (
        <Button variant="outline" disabled={a.pending.reopen} onClick={a.reopen}>
          {a.pending.reopen ? (
            <Loader2 className="size-4 animate-spin" aria-hidden />
          ) : (
            <LockOpen className="size-4" aria-hidden />
          )}
          Reopen for changes
        </Button>
      )}
      <StateNote status={status} phase={phase} opensAt={opensAt} editable={editable} />
      <SystemAdminOnly><AlertDialog
        open={confirmReset}
        onOpenChange={setConfirmReset}
        title="Discard this member's changes?"
        description="Their saved plan and leave choices for this period are cleared, and they go back to the plans they had when it opened. Confirmed coverage is not affected."
        confirmLabel="Discard"
        confirmVariant="default"
        loading={a.pending.reset}
        onConfirm={() => a.discard(() => setConfirmReset(false))}
      /></SystemAdminOnly>
    </div>
  );
}

/** Why an action is missing — phrased for the phase actually in force, so a
 * period that hasn't started never claims its deadline has passed. */
function StateNote({
  status,
  phase,
  opensAt,
  editable,
}: {
  status: EnrollmentStatus;
  phase: WindowPhase;
  opensAt: string;
  editable: boolean;
}) {
  let text: string | null = null;
  if (status === "deemed") {
    text = "Finalised by the period's default when it closed.";
  } else if (status === "confirmed" && phase !== "open") {
    text = "Confirmed. The period no longer takes changes — correct it with Revert below.";
  } else if (phase === "scheduled" && status !== "submitted") {
    text = `This period starts ${fmtWhen(opensAt)} — changes open then.`;
  } else if (phase === "overdue" && status !== "submitted" && status !== "confirmed") {
    text = "Locked: the deadline has passed. Extend it on the Overview tab to change this.";
  } else if (status === "submitted" && editable) {
    text = "Submitted by the member. Changing it moves it back to a draft.";
  }
  if (!text) return null;
  return <p className="basis-full text-xs text-muted-foreground">{text}</p>;
}
