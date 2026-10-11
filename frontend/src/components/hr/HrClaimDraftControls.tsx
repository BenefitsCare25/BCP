/** Corrections HR may still make to a delegated claim: discard the draft,
 *  take a wrongly attached file back off it (once sent, only a file HR itself
 *  uploaded, before a decision — the server decides via `can_remove`), and
 *  the converted figure submit asks HR to confirm. */
import { useNavigate } from "@tanstack/react-router";
import { Trash2, X } from "lucide-react";
import { useState } from "react";
import type { FxQuote } from "@/api/portal";
import {
  type HrClaimDocument,
  useDiscardHrClaim,
  useRemoveHrClaimDocument,
} from "@/api/hrClaims";
import { AlertDialog } from "@/components/ui/alert-dialog";
import { Button } from "@/components/ui/button";
import { usePortalTranslation } from "@/i18n/portal";
import { ConflictDetailError, formatError } from "@/lib/errors";

/** The figure a 409 `fx_confirmation_required` asks HR to accept, shaped as a
 *  quote so `ConversionNotice` can show it; null for any other failure. */
export function fxConfirmationQuote(error: unknown): FxQuote | null {
  if (
    !(error instanceof ConflictDetailError) ||
    error.detail.code !== "fx_confirmation_required"
  ) {
    return null;
  }
  const { currency, policy_currency, amount_claimed, amount_converted, rate, rate_date } =
    error.detail;
  if (
    typeof currency !== "string" ||
    typeof policy_currency !== "string" ||
    typeof amount_claimed !== "number" ||
    typeof amount_converted !== "number"
  ) {
    return null;
  }
  return {
    currency,
    policy_currency,
    amount: amount_claimed,
    converted: amount_converted,
    rate: typeof rate === "number" ? rate : null,
    as_of_date: null,
    rate_date: typeof rate_date === "string" ? rate_date : null,
    stale: false,
    source: null,
    available: true,
    note: null,
  };
}

export function HrClaimDocumentLine({
  claimId,
  document,
  removable,
  onError,
}: {
  claimId: string;
  document: HrClaimDocument;
  removable: boolean;
  onError: (message: string | null) => void;
}) {
  const pt = usePortalTranslation();
  const remove = useRemoveHrClaimDocument();
  const takeBack = async () => {
    onError(null);
    try {
      await remove.mutateAsync({ claimId, documentId: document.id });
    } catch (caught) {
      onError(formatError(caught));
    }
  };
  return (
    <div className="mt-2 flex items-start justify-between gap-3">
      <p className="min-w-0 break-all text-sm text-muted-foreground">{document.file_name}</p>
      {removable && (
        <Button
          type="button"
          variant="ghost"
          size="sm"
          className="h-11 shrink-0 sm:h-8"
          aria-label={pt("Remove {0}", [document.file_name])}
          loading={remove.isPending}
          onClick={() => void takeBack()}
        >
          <X className="size-4" aria-hidden />
          {pt("Remove")}
        </Button>
      )}
    </div>
  );
}

export function DiscardHrDraftButton({ claimId }: { claimId: string }) {
  const pt = usePortalTranslation();
  const navigate = useNavigate();
  const discard = useDiscardHrClaim();
  const [open, setOpen] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const confirm = async () => {
    setError(null);
    try {
      await discard.mutateAsync(claimId);
      setOpen(false);
      await navigate({ to: "/hr/claims" });
    } catch (caught) {
      setError(formatError(caught));
    }
  };
  return (
    <>
      <Button
        type="button"
        variant="ghost"
        className="h-11 shrink-0 sm:h-9"
        onClick={() => {
          setError(null);
          setOpen(true);
        }}
      >
        <Trash2 className="size-4" aria-hidden />
        {pt("Discard draft")}
      </Button>
      <AlertDialog
        open={open}
        onOpenChange={setOpen}
        title={pt("Discard this draft?")}
        description={
          <>
            <p>{pt("The draft and the evidence attached to it are deleted. This can't be undone.")}</p>
            {error && (
              <p className="mt-2 text-error" role="alert">
                {pt(error)}
              </p>
            )}
          </>
        }
        confirmLabel={pt("Discard")}
        cancelLabel={pt("Keep draft")}
        loading={discard.isPending}
        onConfirm={() => void confirm()}
      />
    </>
  );
}
