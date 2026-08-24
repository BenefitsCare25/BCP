import * as React from "react";
import * as DialogPrimitive from "@radix-ui/react-dialog";
import { AlertTriangle, Info } from "lucide-react";
import { Button, type ButtonProps } from "@/components/ui/button";
import { cn } from "@/lib/cn";

interface AlertDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  title: string;
  description: React.ReactNode;
  confirmLabel?: string;
  cancelLabel?: string | null;
  confirmVariant?: ButtonProps["variant"];
  secondaryLabel?: string | null;
  secondaryVariant?: ButtonProps["variant"];
  onSecondary?: () => void | Promise<void>;
  /** Visual register of the prompt. Defaults to `danger` so existing
   * destructive call sites are unchanged; use `info` for a confirmation that
   * isn't destructive — a red warning triangle over "Approve this claim?"
   * contradicts the action it is confirming. */
  tone?: "danger" | "info";
  onConfirm: () => void | Promise<void>;
  loading?: boolean;
  secondaryLoading?: boolean;
  confirmDisabled?: boolean;
  secondaryDisabled?: boolean;
}

const TONES = {
  danger: { icon: AlertTriangle, className: "bg-error-soft text-error" },
  info: { icon: Info, className: "bg-info-soft text-info" },
} as const;

/**
 * Confirmation dialog. Renders an icon + title + body + cancel/confirm buttons.
 * The confirm button defaults to the `destructive` variant; pass
 * `confirmVariant` and `tone` to override for non-destructive prompts.
 */
export function AlertDialog({
  open,
  onOpenChange,
  title,
  description,
  confirmLabel = "Delete",
  cancelLabel = "Cancel",
  confirmVariant = "destructive",
  secondaryLabel = null,
  secondaryVariant = "outline",
  onSecondary,
  tone = "danger",
  onConfirm,
  loading = false,
  secondaryLoading = false,
  confirmDisabled = false,
  secondaryDisabled = false,
}: AlertDialogProps) {
  const { icon: ToneIcon, className: toneClass } = TONES[tone];
  const busy = loading || secondaryLoading;
  const hasSecondaryAction = Boolean(secondaryLabel && onSecondary);
  const actionClassName = hasSecondaryAction
    ? "w-full min-w-0"
    : "w-full sm:w-auto";
  return (
    <DialogPrimitive.Root open={open} onOpenChange={onOpenChange}>
      <DialogPrimitive.Portal>
        <DialogPrimitive.Overlay
          className={cn(
            "fixed inset-0 z-50 bg-foreground/30 backdrop-blur-[2px]",
            "data-[state=open]:animate-in data-[state=closed]:animate-out",
            "data-[state=closed]:fade-out-0 data-[state=open]:fade-in-0",
          )}
        />
        <DialogPrimitive.Content
          className={cn(
            "fixed left-1/2 top-1/2 z-50 -translate-x-1/2 -translate-y-1/2",
            "w-[calc(100vw-2rem)] rounded-xl border border-border bg-card shadow-xl",
            hasSecondaryAction ? "max-w-2xl" : "max-w-md",
            "data-[state=open]:animate-in data-[state=closed]:animate-out",
            "data-[state=closed]:fade-out-0 data-[state=open]:fade-in-0",
            "data-[state=open]:zoom-in-95 data-[state=closed]:zoom-out-95",
          )}
        >
          <div className="p-6 flex gap-4">
            <div
              className={cn(
                "grid size-10 shrink-0 place-items-center rounded-full",
                toneClass,
              )}
            >
              <ToneIcon className="size-5" />
            </div>
            <div className="flex-1 min-w-0">
              <DialogPrimitive.Title className="text-base font-semibold text-foreground">
                {title}
              </DialogPrimitive.Title>
              <DialogPrimitive.Description asChild>
                <div className="text-sm text-muted-foreground mt-1.5">
                  {description}
                </div>
              </DialogPrimitive.Description>
            </div>
          </div>
          <div
            className={cn(
              "gap-2 rounded-b-xl border-t border-border bg-muted/40 px-6 py-4",
              hasSecondaryAction
                ? "grid grid-cols-1 md:grid-cols-3"
                : "flex flex-col sm:flex-row sm:justify-end",
            )}
          >
            {cancelLabel && (
              <Button
                variant="outline"
                onClick={() => onOpenChange(false)}
                disabled={busy}
                className={actionClassName}
              >
                {cancelLabel}
              </Button>
            )}
            {secondaryLabel && onSecondary && (
              <Button
                variant={secondaryVariant}
                onClick={onSecondary}
                loading={secondaryLoading}
                disabled={busy || secondaryDisabled}
                className={actionClassName}
              >
                {secondaryLabel}
              </Button>
            )}
            <Button
              variant={confirmVariant}
              onClick={onConfirm}
              loading={loading}
              disabled={busy || confirmDisabled}
              className={actionClassName}
            >
              {confirmLabel}
            </Button>
          </div>
        </DialogPrimitive.Content>
      </DialogPrimitive.Portal>
    </DialogPrimitive.Root>
  );
}
