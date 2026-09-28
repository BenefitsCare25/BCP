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
  useSetElections,
  useSetLeave,
  useSubmitEnrollment,
} from "@/api/enrollment";
import { ConflictDetailError, formatError } from "@/lib/errors";
import { fmtCurrency } from "@/lib/format";
import type { MemberElection } from "./useMemberElection";

export function useElectionActions(enrollmentId: string, m: MemberElection) {
  const setElections = useSetElections();
  const setLeave = useSetLeave();
  const submit = useSubmitEnrollment();
  const confirm = useConfirmEnrollment();
  const reset = useResetEnrollment();
  const reopen = useReopenEnrollment();
  // Products the server flagged as changed-but-unpriced at submit.
  const [unpriced, setUnpriced] = useState<string[] | null>(null);

  async function saveDraft() {
    const body = m.payload();
    try {
      if (body.elections) {
        await setElections.mutateAsync({ id: enrollmentId, elections: body.elections });
      }
      if (body.leave) await setLeave.mutateAsync({ id: enrollmentId, ...body.leave });
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
    discard: (done: () => void) =>
      reset.mutate(enrollmentId, {
        onSuccess: () => {
          toast.success("Changes discarded.");
          done();
        },
      }),
    reopen: () =>
      reopen.mutate(enrollmentId, {
        onSuccess: () => toast.success("Reopened — you can change this selection again."),
      }),
    unpriced,
    clearUnpriced: () => setUnpriced(null),
    busy: setElections.isPending || setLeave.isPending || submit.isPending || confirm.isPending,
    pending: {
      confirm: confirm.isPending,
      submit: submit.isPending || confirm.isPending,
      leave: setLeave.isPending,
      reset: reset.isPending,
      reopen: reopen.isPending,
    },
  };
}

export type ElectionActions = ReturnType<typeof useElectionActions>;
