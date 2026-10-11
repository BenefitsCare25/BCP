import { FirmOwnerOnly } from "@/components/auth/FirmOwnerOnly";
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

type ReviewAction = "discard" | "reopen" | "returnForCorrection" | "returnToDraft";

const NOTIFIED = "A portal notification is recorded immediately. Email delivery is tracked in enrolment activity.";

/** Dialog wording per review action. "Return to draft" (a broker-managed
 * period) asks nothing of the employee: only the broker corrects it. */
const REVIEW_COPY: Record<ReviewAction, {
  title: string;
  body: string;
  reasonLabel: string;
  notice: string;
  confirm: string;
}> = {
  discard: {
    title: "Cancel and clear this enrolment?",
    body: "Saved plan and leave choices will be cleared. The signed form remains on record as cancelled. Existing confirmed coverage stays in place.",
    reasonLabel: "Reason for the employee",
    notice: NOTIFIED,
    confirm: "Cancel and notify",
  },
  reopen: {
    title: "Reopen this enrolment?",
    body: "Saved choices are retained. Existing confirmed coverage stays in place until replacement choices are confirmed.",
    reasonLabel: "Reason for the employee",
    notice: NOTIFIED,
    confirm: "Reopen and notify",
  },
  returnForCorrection: {
    title: "Return for correction?",
    body: "Choices are retained. The employee must review your reason, correct their choices and sign again before the deadline.",
    reasonLabel: "Reason for the employee",
    notice: NOTIFIED,
    confirm: "Return and notify",
  },
  returnToDraft: {
    title: "Return to your draft?",
    body: "The submitted choices come back here as your draft, so you can correct them, then submit and confirm them again. Nothing is asked of the employee, and existing confirmed coverage stays in place until you confirm.",
    reasonLabel: "Reason",
    notice: "The employee's portal records that your team is correcting the submission, with this reason. Email delivery is tracked in enrolment activity.",
    confirm: "Return to draft",
  },
};

export function ElectionActionBar({
  status,
  phase,
  opensAt,
  editable,
  overdrawn,
  selfService,
  portalSigned,
  actions: a,
}: {
  status: EnrollmentStatus;
  phase: WindowPhase;
  opensAt: string;
  editable: boolean;
  overdrawn: boolean;
  /** The period's member self-service flag: with it a submission goes back to
   *  the employee to correct; without it, back to the broker's draft. */
  selfService: boolean;
  /** The employee signed the submission in the portal. Only they can sign
   *  again, so without self-service it can't be returned at all. */
  portalSigned: boolean;
  actions: ElectionActions;
}) {
  const [reviewAction, setReviewAction] = useState<ReviewAction | null>(null);
  // The dialog keeps its wording while it animates closed.
  const [shownAction, setShownAction] = useState<ReviewAction>("discard");
  const [reason, setReason] = useState("");
  const openReview = (action: ReviewAction) => {
    setReason("");
    setShownAction(action);
    setReviewAction(action);
  };
  const submitted = status === "submitted";
  const copy = REVIEW_COPY[reviewAction ?? shownAction];
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
      {submitted && phase === "open" && selfService && (
        <Button variant="outline" disabled={a.busy} onClick={() => openReview("returnForCorrection")}>Return for correction</Button>
      )}
      {submitted && phase === "open" && !selfService && !portalSigned && (
        <Button variant="outline" disabled={a.busy} onClick={() => openReview("returnToDraft")}>
          <RotateCcw className="size-4" aria-hidden /> Return to draft
        </Button>
      )}
      {editable && status !== "not_started" && (
        <FirmOwnerOnly><Button variant="ghost" onClick={() => openReview("discard")} disabled={a.busy}>
          <RotateCcw className="size-4" aria-hidden /> Cancel and clear choices
        </Button></FirmOwnerOnly>
      )}
      {status === "confirmed" && phase === "open" && (selfService || !portalSigned) && (
        <Button variant="outline" disabled={a.busy} onClick={() => openReview("reopen")}>
          {a.pending.reopen ? (
            <Loader2 className="size-4 animate-spin" aria-hidden />
          ) : (
            <LockOpen className="size-4" aria-hidden />
          )}
          Reopen for changes
        </Button>
      )}
      <StateNote
        status={status}
        phase={phase}
        opensAt={opensAt}
        selfService={selfService}
        portalSigned={portalSigned}
      />
      <AlertDialog
        open={reviewAction !== null}
        onOpenChange={(open) => { if (!open && !a.busy) setReviewAction(null); }}
        title={copy.title}
        description={<div className="space-y-3">
          <p>{copy.body}</p>
          <label className="block space-y-1.5">
            <span className="font-medium text-foreground">{copy.reasonLabel}</span>
            <textarea value={reason} onChange={(e) => setReason(e.target.value)} maxLength={2000} required rows={4} disabled={a.busy}
              className="w-full rounded-md border border-input bg-background p-2 text-sm text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring" />
          </label>
          <p>{copy.notice}</p>
        </div>}
        confirmLabel={copy.confirm}
        confirmVariant={shownAction === "discard" ? "destructive" : "default"}
        tone={shownAction === "discard" ? "danger" : "info"}
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
  selfService,
  portalSigned,
}: {
  status: EnrollmentStatus;
  phase: WindowPhase;
  opensAt: string;
  selfService: boolean;
  portalSigned: boolean;
}) {
  let text: string | null = null;
  if (status === "deemed") {
    text = "Finalised by the period's default when it closed.";
  } else if (status === "confirmed" && phase === "open" && !selfService && portalSigned) {
    text = "Confirmed. The employee signed this one, so reopening it needs member self-service on this period.";
  } else if (status === "confirmed" && phase !== "open") {
    text = "Confirmed. The period no longer takes changes — correct it with Revert below.";
  } else if (phase === "scheduled" && status !== "submitted") {
    text = `This period starts ${fmtWhen(opensAt)} — changes open then.`;
  } else if (phase === "overdue" && status !== "submitted" && status !== "confirmed") {
    text = "Locked: the deadline has passed. Extend it on the Overview tab to change this.";
  } else if (status === "submitted") {
    text = phase !== "open"
      ? selfService || portalSigned
        ? "Review is still available. Extend the deadline before returning this submission for correction."
        : "Review is still available. Extend the deadline before returning this submission to your draft."
      : selfService
        ? "Review the submitted choices, then confirm or return them for correction."
        : portalSigned
          ? "Review the submitted choices, then confirm them. The employee signed this one, so returning it for correction needs member self-service on this period."
          : "Review the submitted choices, then confirm them, or return them to your draft to correct them.";
  } else if (status === "returned") {
    text = "Awaiting correction and a new signed submission. Resolve this before closing the period.";
  }
  if (!text) return null;
  return <p className="basis-full text-xs text-muted-foreground">{text}</p>;
}
