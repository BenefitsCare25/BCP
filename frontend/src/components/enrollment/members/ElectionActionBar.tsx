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
  const [reviewAction, setReviewAction] = useState<"discard" | "reopen" | "returnForCorrection" | null>(null);
  const [reason, setReason] = useState("");
  const openReview = (action: typeof reviewAction) => { setReason(""); setReviewAction(action); };
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
      {editable && !submitted && status !== "returned" && (
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
      {editable && !submitted && (
        <Button variant="outline" onClick={() => void a.saveDraft()} disabled={a.busy}>
          {submitted ? "Save changes" : "Save as draft"}
        </Button>
      )}
      {submitted && phase === "open" && (
        <Button variant="outline" disabled={a.busy} onClick={() => openReview("returnForCorrection")}>Return for correction</Button>
      )}
      {editable && status !== "not_started" && (
        <SystemAdminOnly><Button variant="ghost" onClick={() => openReview("discard")} disabled={a.busy}>
          <RotateCcw className="size-4" aria-hidden /> Cancel and clear choices
        </Button></SystemAdminOnly>
      )}
      {status === "confirmed" && phase === "open" && (
        <Button variant="outline" disabled={a.busy} onClick={() => openReview("reopen")}>
          {a.pending.reopen ? (
            <Loader2 className="size-4 animate-spin" aria-hidden />
          ) : (
            <LockOpen className="size-4" aria-hidden />
          )}
          Reopen for changes
        </Button>
      )}
      <StateNote status={status} phase={phase} opensAt={opensAt} />
      <AlertDialog
        open={reviewAction !== null}
        onOpenChange={(open) => { if (!open && !a.busy) setReviewAction(null); }}
        title={reviewAction === "discard" ? "Cancel and clear this enrolment?" : reviewAction === "reopen" ? "Reopen this enrolment?" : "Return for correction?"}
        description={<div className="space-y-3">
          <p>{reviewAction === "discard"
            ? "Saved plan and leave choices will be cleared. The signed form remains on record as cancelled. Existing confirmed coverage stays in place."
            : reviewAction === "reopen"
              ? "Saved choices are retained. Existing confirmed coverage stays in place until replacement choices are confirmed."
              : "Choices are retained. The employee must review your reason, correct their choices and sign again before the deadline."}</p>
          <label className="block space-y-1.5">
            <span className="font-medium text-foreground">Reason for the employee</span>
            <textarea value={reason} onChange={(e) => setReason(e.target.value)} maxLength={2000} required rows={4} disabled={a.busy}
              className="w-full rounded-md border border-input bg-background p-2 text-sm text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring" />
          </label>
          <p>A portal notification is recorded immediately. Email delivery is tracked in enrolment activity.</p>
        </div>}
        confirmLabel={reviewAction === "discard" ? "Cancel and notify" : reviewAction === "reopen" ? "Reopen and notify" : "Return and notify"}
        confirmVariant={reviewAction === "discard" ? "destructive" : "default"}
        tone={reviewAction === "discard" ? "danger" : "info"}
        loading={a.busy}
        confirmDisabled={!reason.trim()}
        onConfirm={() => { if (reviewAction && reason.trim()) a[reviewAction](reason.trim(), () => setReviewAction(null)); }}
      />
    </div>
  );
}

/** Why an action is missing — phrased for the phase actually in force, so a
 * period that hasn't started never claims its deadline has passed. */
function StateNote({
  status,
  phase,
  opensAt,
}: {
  status: EnrollmentStatus;
  phase: WindowPhase;
  opensAt: string;
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
  } else if (status === "submitted") {
    text = phase === "open" ? "Review the submitted choices, then confirm or return them for correction." : "Review is still available. Extend the deadline before returning this submission for correction.";
  } else if (status === "returned") {
    text = "Awaiting correction and a new signed submission. Resolve this before closing the period.";
  }
  if (!text) return null;
  return <p className="basis-full text-xs text-muted-foreground">{text}</p>;
}
