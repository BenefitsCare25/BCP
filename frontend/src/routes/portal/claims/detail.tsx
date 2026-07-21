/** Claim detail — status, receipts, broker notes; resubmission when more
 * info was requested. */
import { useRef } from "react";
import { useNavigate, useParams } from "@tanstack/react-router";
import { ArrowLeft, Check, Loader2, Paperclip, Send } from "lucide-react";
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
const MAX_BYTES = 15 * 1024 * 1024;

// The generic invoice/receipt slot is satisfied by ANY attached document
// (mirrors the backend `assert_documents_satisfy_slots`); specific slots need
// a document tagged with the matching key.
const GENERIC_SLOT = "invoice_receipt";

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
  const slots = data.required_doc_slots ?? [];

  // A slot is satisfied when a matching tagged document exists; the generic
  // invoice/receipt slot accepts any attached document.
  const slotSatisfied = (key: string) =>
    key === GENERIC_SLOT
      ? data.documents.length > 0
      : data.documents.some((d) => d.doc_type === key);
  const allSlotsSatisfied = slots.every((s) => slotSatisfied(s.key));

  const addDocument = async (file: File | undefined, docType?: string) => {
    if (!file) return;
    if (file.size > MAX_BYTES) {
      toast.error(`${file.name} exceeds 15 MB`);
      return;
    }
    try {
      await uploadDoc.mutateAsync({ claimId: data.id, file, docType });
      await claim.refetch();
      toast.success("Document added");
    } catch (err) {
      toast.error(formatError(err));
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
                : data.claim_type || data.product_code}
            </h2>
            {data.sub_type && (
              <p className="mt-0.5 text-xs text-muted-foreground">
                {data.sub_type}
              </p>
            )}
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
          {data.dependant_name && (
            <div>
              <div className="text-xs text-muted-foreground">Claimant</div>
              <div className="font-medium text-foreground">
                {data.dependant_name}
              </div>
            </div>
          )}
          {data.diagnosis && (
            <div className="col-span-2">
              <div className="text-xs text-muted-foreground">Diagnosis</div>
              <div className="font-medium text-foreground">{data.diagnosis}</div>
            </div>
          )}
          {(data.referral_document || data.referral_not_applicable) && (
            <div className="col-span-2">
              <div className="text-xs text-muted-foreground">Referral letter</div>
              <div className="font-medium text-foreground">
                {data.referral_document
                  ? data.referral_document.file_name
                  : "Not applicable"}
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
            Documents ({data.documents.length})
          </div>
          {data.documents.length > 0 && (
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
          )}
        </div>

        {/* Required documents — one labeled, tagged upload per slot the claim
            must fill (so a needs_info resubmission can satisfy every slot the
            backend enforces). The generic invoice/receipt slot is met by any
            attached document. */}
        {editable && slots.length > 0 && (
          <div className="space-y-2">
            <div className="text-xs font-medium text-foreground">
              Required documents
            </div>
            {slots.map((slot) => {
              const done = slotSatisfied(slot.key);
              return (
                <div
                  key={slot.key}
                  className="flex items-center justify-between gap-2"
                >
                  <span className="flex items-center gap-1.5 text-xs text-foreground">
                    {done && <Check className="size-3.5 text-good shrink-0" />}
                    {slot.label}
                  </span>
                  <label className="inline-flex cursor-pointer items-center gap-1.5 rounded-md border border-border bg-background px-2.5 py-1.5 text-xs font-medium text-foreground hover:bg-muted">
                    <Paperclip className="size-3.5 shrink-0" />
                    {done ? "Replace" : "Attach"}
                    <input
                      type="file"
                      accept={ACCEPT}
                      className="hidden"
                      disabled={uploadDoc.isPending}
                      onChange={(e) => {
                        const f = e.target.files?.[0];
                        e.target.value = "";
                        void addDocument(
                          f,
                          slot.key === GENERIC_SLOT ? undefined : slot.key,
                        );
                      }}
                    />
                  </label>
                </div>
              );
            })}
          </div>
        )}

        {editable && (
          <div className="space-y-2">
            <input
              ref={fileInput}
              type="file"
              accept={ACCEPT}
              className="hidden"
              onChange={(e) => {
                const f = e.target.files?.[0];
                if (fileInput.current) fileInput.current.value = "";
                void addDocument(f);
              }}
            />
            <Button
              type="button"
              variant="outline"
              size="sm"
              disabled={uploadDoc.isPending}
              onClick={() => fileInput.current?.click()}
            >
              {uploadDoc.isPending ? (
                <Loader2 className="size-4 animate-spin" />
              ) : (
                <Paperclip className="size-4" />
              )}
              Add another document
            </Button>
            <Button
              className="w-full"
              disabled={submitClaim.isPending || !allSlotsSatisfied}
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
          </div>
        )}
      </div>
    </div>
  );
}
