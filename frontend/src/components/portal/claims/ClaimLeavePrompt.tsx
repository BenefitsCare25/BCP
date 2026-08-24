import { useEffect, useState } from "react";
import { useBlocker } from "@tanstack/react-router";
import { AlertDialog } from "@/components/ui/alert-dialog";
import { formatError } from "@/lib/errors";
import type { NewClaimForm } from "./useNewClaimForm";

type LeaveAction = "save" | "discard" | null;

export function ClaimLeavePrompt({ form }: { form: NewClaimForm }) {
  const [open, setOpen] = useState(false);
  const [action, setAction] = useState<LeaveAction>(null);
  const [error, setError] = useState<string | null>(null);
  const blocker = useBlocker({
    shouldBlockFn: ({ current, next }) =>
      form.hasUnsubmittedWork &&
      !form.busy &&
      current.pathname !== next.pathname,
    enableBeforeUnload: () => form.hasUnsubmittedWork && !form.busy,
    disabled: !form.hasUnsubmittedWork || form.busy,
    withResolver: true,
  });

  useEffect(() => {
    if (blocker.status === "blocked") {
      setError(null);
      setOpen(true);
    }
  }, [blocker.status]);

  const finishLeave = async (choice: Exclude<LeaveAction, null>) => {
    setAction(choice);
    setError(null);
    try {
      if (choice === "save") await form.saveDraft();
      else await form.discardDraft();
      setAction(null);
      setOpen(false);
      if (blocker.status === "blocked") blocker.proceed();
    } catch (caught) {
      setError(formatError(caught));
      setAction(null);
    }
  };

  return (
    <AlertDialog
      open={open}
      onOpenChange={(nextOpen) => {
        if (action) return;
        setOpen(nextOpen);
        if (!nextOpen && blocker.status === "blocked") blocker.reset();
      }}
      title="Save this claim as a draft?"
      description={
        <div className="space-y-2">
          <p>Save your answers now and continue this claim later.</p>
          {form.hasLocalAttachments && (
            <p>Selected files cannot be stored in a draft and must be reattached.</p>
          )}
          {error && (
            <p className="font-medium text-error" role="alert">
              {error}
            </p>
          )}
        </div>
      }
      tone="info"
      confirmLabel="Save draft and leave"
      confirmVariant="default"
      loading={action === "save"}
      onConfirm={() => finishLeave("save")}
      secondaryLabel="Leave without saving"
      secondaryVariant="destructiveOutline"
      secondaryLoading={action === "discard"}
      onSecondary={() => finishLeave("discard")}
      cancelLabel="Continue editing"
    />
  );
}
