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
import { formatError } from "@/lib/errors";
import { useDocumentTitle } from "@/lib/useDocumentTitle";

const MAX_UPLOAD_BYTES = 10 * 1024 * 1024;

function Fact({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div>
      <dt className="text-xs font-medium text-muted-foreground">{label}</dt>
      <dd className="mt-1 text-sm text-foreground">{children}</dd>
    </div>
  );
}

export function HrClaimDetailPage() {
  const { claimId } = useParams({ strict: false }) as { claimId: string };
  const claim = useHrClaim(claimId);
  const upload = useUploadHrClaimDocument();
  const submit = useSubmitHrClaim();
  const [uploadingSlot, setUploadingSlot] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  useDocumentTitle(claim.data?.claim_ref ?? "Employee claim");

  if (claim.isLoading) {
    return (
      <div className="mx-auto max-w-3xl space-y-4" aria-label="Loading claim">
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
          <Link to="/hr/claims"><ArrowLeft className="size-4" aria-hidden />All claims</Link>
        </Button>
        <Card className="p-5" role="alert">
          <p className="font-medium">This claim could not be loaded</p>
          <p className="mt-1 text-sm text-muted-foreground">{formatError(claim.error)}</p>
        </Card>
      </div>
    );
  }

  const data = claim.data;
  const missing = data.doc_slots.filter(
    (slot) => !data.documents.some((document) => document.doc_type === slot.key),
  );
  const submitReady = data.can_submit && missing.length === 0;

  const uploadFile = async (slot: string, file: File | null) => {
    if (!file) return;
    if (file.size > MAX_UPLOAD_BYTES) {
      setError("Choose a file no larger than 10 MB.");
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
    <div className="mx-auto max-w-3xl space-y-5">
      <div>
        <Button asChild variant="ghost" className="-ml-3 h-11 sm:h-9">
          <Link to="/hr/claims">
            <ArrowLeft className="size-4" aria-hidden />
            All claims
          </Link>
        </Button>
        <div className="mt-2 flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
          <div>
            <h1 className="text-2xl font-semibold tracking-tight">
              {data.claim_ref ?? "Draft claim"}
            </h1>
            <p className="mt-1 text-sm text-muted-foreground">
              {data.employee_name ?? "Employee"} · {data.claim_type}
            </p>
          </div>
          <div className="self-start">
            <ClaimStatus status={data.status} />
          </div>
        </div>
      </div>

      <Card className="p-5 sm:p-6">
        <dl className="grid gap-x-6 gap-y-5 sm:grid-cols-2">
          <Fact label="Employee">{data.employee_name ?? "Employee"}</Fact>
          <Fact label="Claim amount">
            <span className="tabular-nums">{formatClaimMoney(data.amount_claimed, data.currency)}</span>
          </Fact>
          <Fact label="Date incurred">{formatClaimDate(data.incurred_date)}</Fact>
          <Fact label="Provider">{data.provider_name ?? "—"}</Fact>
          <Fact label="Invoice or receipt">{data.invoice_number ?? "—"}</Fact>
          <Fact label="Submitted">
            {data.submitted_at ? formatClaimDate(data.submitted_at) : "Not submitted"}
          </Fact>
        </dl>
        <div className="mt-5 flex items-start gap-3 border-t border-border pt-5">
          <UserRound className="mt-0.5 size-4 shrink-0 text-muted-foreground" aria-hidden />
          <div className="text-sm">
            <p className="font-medium">Filed for {data.employee_name ?? "the employee"}</p>
            <p className="text-muted-foreground">
              By {data.submitted_by_name || data.submitted_by_email || "company HR"}
              {data.submitted_by_name && data.submitted_by_email
                ? ` · ${data.submitted_by_email}`
                : ""}
            </p>
          </div>
        </div>
      </Card>

      <Card className="p-5 sm:p-6">
        <div className="flex items-start justify-between gap-4">
          <div>
            <h2 className="text-base font-semibold">Evidence</h2>
            <p className="mt-1 text-sm text-muted-foreground">
              {data.can_add_evidence
                ? data.can_submit
                  ? "Attach each required document before submitting."
                  : "Add supporting documents while this claim is under review."
                : "Evidence attached to this claim is retained with the record."}
            </p>
          </div>
          <span className="shrink-0 text-xs tabular-nums text-muted-foreground">
            {data.documents.length} attached
          </span>
        </div>

        {data.doc_slots.length === 0 ? (
          <p className="mt-5 rounded-lg bg-muted p-4 text-sm text-muted-foreground">
            No evidence is required for this claim type.
          </p>
        ) : (
          <div className="mt-5 divide-y divide-border rounded-lg border border-border">
            {data.doc_slots.map((slot) => {
              const documents = data.documents.filter((item) => item.doc_type === slot.key);
              const satisfied = documents.length > 0;
              const uploading = uploadingSlot === slot.key;
              return (
                <div key={slot.key} className="p-4">
                  <div className="flex items-start gap-3">
                    {satisfied ? (
                      <CheckCircle2 className="mt-0.5 size-5 shrink-0 text-good" aria-hidden />
                    ) : (
                      <FileText className="mt-0.5 size-5 shrink-0 text-muted-foreground" aria-hidden />
                    )}
                    <div className="min-w-0 flex-1">
                      <p className="text-sm font-medium">{slot.label}</p>
                      {slot.instructions && (
                        <p className="mt-0.5 text-xs text-muted-foreground">{slot.instructions}</p>
                      )}
                      {documents.map((document) => (
                        <p key={document.id} className="mt-2 truncate text-sm text-muted-foreground">
                          {document.file_name}
                        </p>
                      ))}
                    </div>
                    {data.can_add_evidence && (
                      <label className="relative inline-flex min-h-11 shrink-0 cursor-pointer items-center gap-2 rounded-md border border-input bg-card px-3 text-sm font-medium hover:bg-muted focus-within:ring-2 focus-within:ring-ring/40 sm:min-h-9">
                        {uploading ? (
                          <Loader2 className="size-4 animate-spin" aria-hidden />
                        ) : (
                          <Upload className="size-4" aria-hidden />
                        )}
                        {satisfied ? "Add another" : "Upload"}
                        <input
                          type="file"
                          className="sr-only"
                          aria-label={`Upload ${slot.label}`}
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
                </div>
              );
            })}
          </div>
        )}

        {error && (
          <p className="mt-4 rounded-lg bg-error-soft p-3 text-sm text-error" role="alert">
            {error}
          </p>
        )}

        {data.can_submit && (
          <div className="mt-5 flex flex-col gap-3 border-t border-border pt-5 sm:flex-row sm:items-center sm:justify-between">
            <p className="text-sm text-muted-foreground">
              {missing.length > 0
                ? `${missing.length} required ${missing.length === 1 ? "document is" : "documents are"} still missing.`
                : "All required evidence is attached."}
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
              Submit claim
            </Button>
          </div>
        )}
      </Card>
    </div>
  );
}
