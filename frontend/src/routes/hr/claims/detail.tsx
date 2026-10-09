import { usePortalTranslation } from "@/i18n/portal";
import { Link, useParams } from "@tanstack/react-router";
import {
  ArrowLeft,
  CheckCircle2,
  FileText,
  Loader2,
  Send,
  Upload,
  UserRound,
} from "lucide-react";
import { type ReactNode, useState } from "react";
import {
  useHrClaim,
  useSubmitHrClaim,
  useUploadHrClaimDocument,
} from "@/api/hrClaims";
import {
  ClaimStatus,
  formatClaimDate,
  formatClaimMoney,
} from "@/components/hr/claimPresentation";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { CLAIM_DOCUMENT_MAX_BYTES } from "@/lib/claim-files";
import { formatError } from "@/lib/errors";
import { useDocumentTitle } from "@/lib/useDocumentTitle";
import { documentsForSlot } from "@/components/hr/claimEvidence";

function Fact({ label, children }: { label: string; children: ReactNode }) {
  const pt = usePortalTranslation();
  return (
    <div className="min-w-0">
      <dt className="text-xs font-medium text-muted-foreground">{pt(label)}</dt>
      <dd className="mt-1 break-words text-sm text-foreground">{children}</dd>
    </div>
  );
}

export function HrClaimDetailPage() {
  const pt = usePortalTranslation();
  const { claimId } = useParams({ strict: false }) as { claimId: string };
  const claim = useHrClaim(claimId);
  const upload = useUploadHrClaimDocument();
  const submit = useSubmitHrClaim();
  const [uploadingSlot, setUploadingSlot] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  useDocumentTitle(claim.data?.claim_ref ?? "Employee claim");

  if (claim.isLoading) {
    return (
      <div className="mx-auto max-w-3xl space-y-4" aria-label={pt("Loading claim")}>
        <Skeleton className="h-10 w-48" />
        <Skeleton className="h-40 rounded-xl" />
        <Skeleton className="h-56 rounded-xl" />
      </div>
    );
  }

  if (claim.isError || !claim.data) {
    return (
      <div className="mx-auto max-w-3xl space-y-4">
        <Button asChild variant="ghost" className="-ml-3 h-11 sm:h-9">
          <Link to="/hr/claims"><ArrowLeft className="size-4" aria-hidden />{pt("All claims")}</Link>
        </Button>
        <Card className="p-5" role="alert">
          <p className="font-medium">{pt("This claim could not be loaded")}</p>
          <p className="mt-1 text-sm text-muted-foreground">{pt(formatError(claim.error))}</p>
        </Card>
      </div>
    );
  }

  const data = claim.data;
  const missing = data.doc_slots.filter(
    (slot) => documentsForSlot(data.documents, slot.key).length === 0,
  );
  const matchedIds = new Set(data.doc_slots.flatMap((slot) => documentsForSlot(data.documents, slot.key).map((document) => document.id)));
  const additionalDocuments = data.documents.filter((document) => !matchedIds.has(document.id));
  const submitReady = data.can_submit && missing.length === 0;

  const uploadFile = async (slot: string, file: File | null) => {
    if (!file) return;
    if (file.size > CLAIM_DOCUMENT_MAX_BYTES) {
      setError("Choose a file no larger than 15 MB.");
      return;
    }
    setError(null);
    setUploadingSlot(slot);
    try {
      await upload.mutateAsync({ claimId: data.id, file, docType: slot });
    } catch (caught) {
      setError(formatError(caught));
    } finally {
      setUploadingSlot(null);
    }
  };

  const sendClaim = async () => {
    setError(null);
    try {
      await submit.mutateAsync(data.id);
    } catch (caught) {
      setError(formatError(caught));
    }
  };

  return (
    <div className="hr-claim-detail grid items-start gap-3 md:grid-cols-2">
      <div className="md:col-span-2">
        <Button asChild variant="ghost" className="-ml-3 h-11 sm:h-9">
          <Link to="/hr/claims">
            <ArrowLeft className="size-4" aria-hidden />
            {pt("All claims")} </Link>
        </Button>
        <div className="mt-2 flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
          <div>
            <h1 className="text-2xl font-semibold tracking-tight">
              {data.claim_ref ?? pt("Draft claim")}
            </h1>
            <p className="mt-1 text-sm text-muted-foreground">
              {data.employee_name ?? pt("Employee")} · {pt(data.claim_type)}
            </p>
          </div>
          <div className="self-start">
            <ClaimStatus status={data.status} />
          </div>
        </div>
      </div>

      <Card className="p-5">
        <dl className="grid grid-cols-2 gap-x-4 gap-y-3">
          <Fact label={pt("Claim amount")}>
            <span className="tabular-nums">{formatClaimMoney(data.amount_claimed, data.currency)}</span>
          </Fact>
          <Fact label={pt("Date incurred")}>{formatClaimDate(data.incurred_date)}</Fact>
          <Fact label={pt("Provider")}>{data.provider_name ?? "—"}</Fact>
          <Fact label={pt("Invoice or receipt")}>{data.invoice_number ?? "—"}</Fact>
          <Fact label={pt("Submitted")}>
            {data.submitted_at ? formatClaimDate(data.submitted_at) : pt("Not submitted")}
          </Fact>
        </dl>
        <div className="mt-3 flex items-start gap-3 border-t border-border pt-3">
          <UserRound className="mt-0.5 size-4 shrink-0 text-muted-foreground" aria-hidden />
          <div className="text-sm">
            <p className="font-medium">{pt("Filed by")} {data.submitted_by_name || data.submitted_by_email || pt("company HR")}</p>
            {data.submitted_by_name && data.submitted_by_email && (
              <p className="break-all text-muted-foreground">{data.submitted_by_email}</p>
            )}
          </div>
        </div>
      </Card>

      <Card className="p-5">
        <div className="flex items-start justify-between gap-4">
          <div>
            <h2 className="text-base font-semibold">{pt("Evidence")}</h2>
            <p className="mt-1 text-sm text-muted-foreground">
              {data.can_add_evidence
                ? data.can_submit
                  ? pt("Attach each required document before submitting.")
                  : pt("Add supporting documents while this claim is under review.")
                : pt("Evidence attached to this claim is retained with the record.")}
            </p>
          </div>
          <span className="shrink-0 text-xs tabular-nums text-muted-foreground">
            {data.documents.length}  {pt("attached")} </span>
        </div>

        {data.doc_slots.length === 0 ? (
          <p className="mt-5 rounded-lg bg-muted p-4 text-sm text-muted-foreground">
            {pt("No evidence is required for this claim type.")} </p>
        ) : (
          <div className="mt-3 divide-y divide-border rounded-xl border border-border">
            {data.doc_slots.map((slot) => {
              const documents = documentsForSlot(data.documents, slot.key);
              const satisfied = documents.length > 0;
              const uploading = uploadingSlot === slot.key;
              return (
                <div key={slot.key} className="p-4">
                  <div className="flex flex-wrap items-start gap-3">
                    {satisfied ? (
                      <CheckCircle2 className="mt-0.5 size-5 shrink-0 text-good" aria-hidden />
                    ) : (
                      <FileText className="mt-0.5 size-5 shrink-0 text-muted-foreground" aria-hidden />
                    )}
                    <div className="min-w-0 flex-1">
                      <p className="text-sm font-medium">{pt(slot.label)}</p>
                      {slot.instructions && slot.instructions.trim().toLowerCase() !== `attach the ${slot.label.toLowerCase()}.` && (
                        <p className="mt-0.5 text-xs text-muted-foreground">{pt(slot.instructions)}</p>
                      )}
                    </div>
                    {data.can_add_evidence && (
                      <label className="relative inline-flex min-h-11 shrink-0 cursor-pointer items-center gap-2 rounded-md border border-input bg-card px-3 text-sm font-medium hover:bg-muted focus-within:ring-2 focus-within:ring-ring/40 sm:min-h-9">
                        {uploading ? (
                          <Loader2 className="size-4 animate-spin" aria-hidden />
                        ) : (
                          <Upload className="size-4" aria-hidden />
                        )}
                        {satisfied ? pt("Add another") : pt("Upload")}
                        <input
                          type="file"
                          className="sr-only"
                          aria-label={pt("Upload {0}", [slot.label])}
                          accept=".pdf,.png,.jpg,.jpeg,application/pdf,image/png,image/jpeg"
                          disabled={uploadingSlot !== null}
                          onChange={(event) => {
                            const file = event.target.files?.[0] ?? null;
                            event.target.value = "";
                            void uploadFile(slot.key, file);
                          }}
                        />
                      </label>
                    )}
                  </div>
                  {documents.map((document) => (
                    <p key={document.id} className="mt-2 break-all text-sm text-muted-foreground">
                      {document.file_name}
                    </p>
                  ))}
                </div>
              );
            })}
          </div>
        )}
        {additionalDocuments.length > 0 && (
          <div className="mt-3 space-y-2 border-t border-border pt-3">
            <h3 className="text-sm font-semibold">{pt("Additional evidence")}</h3>
            {additionalDocuments.map((document) => (
              <p key={document.id} className="break-all text-sm text-label">{document.file_name}</p>
            ))}
          </div>
        )}

        {error && (
          <p className="mt-4 rounded-lg bg-error-soft p-3 text-sm text-error" role="alert">
            {pt(error)}
          </p>
        )}

        {data.can_submit && (
          <div className="mt-5 flex flex-col gap-3 border-t border-border pt-5 sm:flex-row sm:items-center sm:justify-between">
            <p className="text-sm text-muted-foreground">
              {missing.length > 0
                ? pt("{0} required {1} still missing.", [missing.length, missing.length === 1 ? "document is" : "documents are"])
                : pt("All required evidence is attached.")}
            </p>
            <Button
              className="h-11 shrink-0 sm:h-9"
              disabled={!submitReady || submit.isPending || uploadingSlot !== null}
              onClick={() => void sendClaim()}
            >
              {submit.isPending ? (
                <Loader2 className="size-4 animate-spin" aria-hidden />
              ) : (
                <Send className="size-4" aria-hidden />
              )}
              {pt("Submit claim")} </Button>
          </div>
        )}
      </Card>
    </div>
  );
}
