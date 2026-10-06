/** The broker's actions on one member's election. Errors from the plain
 * mutations are toasted by the global mutation handler; only submit carries
 * `localErrorHandling`, because its 409s (unpriced, overdrawn) need their own
 * dialog and wording. */
import { useState } from "react";
import { toast } from "sonner";
import {
  useConfirmEnrollment,
  useReopenEnrollment,
  useResetEnrollment,
  useReturnEnrollment,
  useSaveEnrollmentDraft,
  useSetLeave,
  useSubmitEnrollment,
} from "@/api/enrollment";
import { ConflictDetailError, formatError } from "@/lib/errors";
import { fmtCurrency } from "@/lib/format";
import type { MemberElection } from "./useMemberElection";

export function useElectionActions(enrollmentId: string, m: MemberElection) {
  const setElections = useSaveEnrollmentDraft();
  const setLeave = useSetLeave();
  const submit = useSubmitEnrollment();
  const confirm = useConfirmEnrollment();
  const reset = useResetEnrollment();
  const reopen = useReopenEnrollment();
  const returnForCorrection = useReturnEnrollment();
  // Products the server flagged as changed-but-unpriced at submit.
  const [unpriced, setUnpriced] = useState<string[] | null>(null);

  async function saveDraft() {
    const body = m.payload();
    try {
      await setElections.mutateAsync({ id: enrollmentId, ...body, expected_event_id: m.enr?.latest_event_id ?? null });
      toast.success("Saved as draft.");
    } catch {
      // Already toasted by the global mutation handler.
    }
  }

  function doConfirm() {
    confirm.mutate(enrollmentId, {
      onSuccess: () => toast.success("Confirmed — this is now their coverage."),
    });
  }

  function onSubmitError(e: unknown) {
    if (e instanceof ConflictDetailError && e.detail.code === "unpriced_elections") {
      setUnpriced(Array.isArray(e.detail.products) ? (e.detail.products as string[]) : []);
      return;
    }
    if (e instanceof ConflictDetailError && e.detail.code === "flex_overdrawn") {
      const bal = e.detail.balance;
      toast.error(
        `These choices cost more than the flex wallet${
          typeof bal === "number" ? ` by ${fmtCurrency(Math.abs(bal))}` : ""
        }. Reduce them, or allow overdraft on the period.`,
      );
      return;
    }
    toast.error(formatError(e));
  }

  // Submit carries the on-screen choices (nothing unsaved is lost), then
  // confirms: the broker acting for a member needs no hand-off to themselves.
  function submitAndConfirm(acknowledgeUnpriced: boolean) {
    const days = Number(m.leaveDays);
    if (!Number.isFinite(days) || days < 0) {
      toast.error("Enter a valid, non-negative number of leave days.");
      return;
    }
    submit.mutate(
      { id: enrollmentId, acknowledgeUnpriced, ...m.payload() },
      {
        onSuccess: () => {
          setUnpriced(null);
          doConfirm();
        },
        onError: onSubmitError,
      },
    );
  }

  return {
    saveDraft,
    doConfirm,
    submitAndConfirm,
    saveLeave: () =>
      setLeave.mutate(
        { id: enrollmentId, action: m.leaveAction, days: Number(m.leaveDays) },
        { onSuccess: () => toast.success("Leave saved.") },
      ),
    discard: (reason: string, done: () => void) =>
      reset.mutate({ id: enrollmentId, reason }, {
        onSuccess: () => {
          toast.success("Choices cancelled. An employee portal notice has been recorded.");
          done();
        },
      }),
    reopen: (reason: string, done: () => void) =>
      reopen.mutate({ id: enrollmentId, reason }, {
        onSuccess: () => { toast.success("Reopened. An employee portal notice has been recorded."); done(); },
      }),
    returnForCorrection: (reason: string, done: () => void) =>
      returnForCorrection.mutate({ id: enrollmentId, reason }, {
        onSuccess: () => { toast.success("Returned for correction. Choices retained and employee notified in the portal."); done(); },
      }),
    unpriced,
    clearUnpriced: () => setUnpriced(null),
    busy: setElections.isPending || setLeave.isPending || submit.isPending || confirm.isPending || reset.isPending || reopen.isPending || returnForCorrection.isPending,
    pending: {
      confirm: confirm.isPending,
      submit: submit.isPending || confirm.isPending,
      leave: setLeave.isPending,
      reset: reset.isPending,
      reopen: reopen.isPending,
      returnForCorrection: returnForCorrection.isPending,
    },
  };
}

export type ElectionActions = ReturnType<typeof useElectionActions>;
