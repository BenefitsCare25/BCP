/** Claim detail — status, receipts, broker notes; resubmission when more
 * info was requested. */
import { useRef } from "react";
import { useNavigate, useParams } from "@tanstack/react-router";
import { ArrowLeft, Loader2, Paperclip, Send } from "lucide-react";
import { toast } from "sonner";
import {
  usePortalClaim,
  useSubmitClaim,
  useUploadClaimDocument,
} from "@/api/portal";
import { ClaimStatusBadge } from "@/components/portal/ClaimStatusBadge";
import { PortalErrorState } from "@/components/portal/PortalErrorState";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { formatError, isNotFoundError } from "@/lib/errors";
import { fmtAmount } from "@/lib/format";

const ACCEPT = ".pdf,.png,.jpg,.jpeg";

export function PortalClaimDetailPage() {
  const { claimId } = useParams({ strict: false }) as { claimId: string };
  const navigate = useNavigate();
  const claim = usePortalClaim(claimId);
  const uploadDoc = useUploadClaimDocument();
  const submitClaim = useSubmitClaim();
  const fileInput = useRef<HTMLInputElement>(null);

  if (claim.isLoading) return <Skeleton className="h-64 w-full" />;
  // Only a real 404 means the claim doesn't exist — any other failure gets a
  // retryable error state instead of a misleading "not found".
  if (claim.isError && !isNotFoundError(claim.error)) {
    return <PortalErrorState onRetry={() => void claim.refetch()} />;
  }
  if (claim.isError || !claim.data) {
    return <p className="text-sm text-muted-foreground">Claim not found.</p>;
  }

  const data = claim.data;
  const editable = data.status === "draft" || data.status === "needs_info";

  const addReceipt = async (files: FileList | null) => {
    if (!files?.[0]) return;
    try {
      await uploadDoc.mutateAsync({ claimId: data.id, file: files[0] });
      await claim.refetch();
      toast.success("Receipt added");
    } catch (err) {
      toast.error(formatError(err));
    } finally {
      if (fileInput.current) fileInput.current.value = "";
    }
  };

  const resubmit = async () => {
    try {
      await submitClaim.mutateAsync(data.id);
      toast.success("Claim resubmitted");
      void navigate({ to: "/portal/claims" });
    } catch (err) {
      toast.error(formatError(err));
    }
  };

  return (
    <div className="mx-auto max-w-lg space-y-4">
      <button
        type="button"
        onClick={() => void navigate({ to: "/portal/claims" })}
        className="flex items-center gap-1 text-xs text-muted-foreground hover:text-foreground"
      >
        <ArrowLeft className="size-3.5" /> Back to claims
      </button>

      <div className="rounded-lg border border-border bg-card p-5 space-y-4">
        <div className="flex items-start justify-between gap-3">
          <div>
            <h2 className="text-sm font-semibold text-foreground">
              {data.claim_kind === "flex"
                ? data.flex_category_name
                : data.benefit_key || data.product_code}
            </h2>
            <p className="mt-0.5 text-xs text-muted-foreground">
              {data.provider_name ? `${data.provider_name} · ` : ""}
              incurred {data.incurred_date}
            </p>
          </div>
          <ClaimStatusBadge status={data.status} />
        </div>

        <div className="grid grid-cols-2 gap-3 text-sm">
          <div>
            <div className="text-xs text-muted-foreground">Amount claimed</div>
            <div className="font-semibold text-foreground">
              {data.currency} {fmtAmount(data.amount_claimed)}
            </div>
          </div>
          {data.amount_approved != null && (
            <div>
              <div className="text-xs text-muted-foreground">Amount approved</div>
              <div className="font-semibold text-good">
                {data.currency} {fmtAmount(data.amount_approved)}
              </div>
            </div>
          )}
          {data.invoice_number && (
            <div>
              <div className="text-xs text-muted-foreground">Invoice number</div>
              <div className="font-medium text-foreground">
                {data.invoice_number}
              </div>
            </div>
          )}
        </div>

        {data.remarks && (
          <div>
            <div className="text-xs text-muted-foreground">Remarks</div>
            <p className="mt-0.5 text-sm text-foreground">{data.remarks}</p>
          </div>
        )}

        {data.decision_notes && (
          <div className="rounded-md bg-muted p-3">
            <div className="text-xs font-medium text-foreground">
              Note from your broker
            </div>
            <p className="mt-1 text-xs text-muted-foreground">
              {data.decision_notes}
            </p>
          </div>
        )}

        <div>
          <div className="text-xs font-medium text-foreground mb-1.5">
            Receipts ({data.documents.length})
          </div>
          <ul className="space-y-1">
            {data.documents.map((doc) => (
              <li
                key={doc.id}
                className="rounded-md bg-muted px-2.5 py-1.5 text-xs text-foreground"
              >
                {doc.file_name}
                <span className="ml-2 text-muted-foreground">
                  {(doc.size_bytes / 1024).toFixed(0)} KB
                </span>
              </li>
            ))}
          </ul>
          {editable && (
            <>
              <input
                ref={fileInput}
                type="file"
                accept={ACCEPT}
                className="hidden"
                onChange={(e) => void addReceipt(e.target.files)}
              />
              <Button
                type="button"
                variant="outline"
                size="sm"
                className="mt-2"
                disabled={uploadDoc.isPending}
                onClick={() => fileInput.current?.click()}
              >
                {uploadDoc.isPending ? (
                  <Loader2 className="size-4 animate-spin" />
                ) : (
                  <Paperclip className="size-4" />
                )}
                Add receipt
              </Button>
            </>
          )}
        </div>

        {editable && (
          <Button
            className="w-full"
            disabled={submitClaim.isPending || data.documents.length === 0}
            onClick={() => void resubmit()}
          >
            {submitClaim.isPending ? (
              <Loader2 className="size-4 animate-spin" />
            ) : (
              <Send className="size-4" />
            )}
            <span className="ml-1.5">
              {data.status === "needs_info" ? "Resubmit claim" : "Submit claim"}
            </span>
          </Button>
        )}
      </div>
    </div>
  );
}
