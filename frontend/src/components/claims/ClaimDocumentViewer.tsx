import { useEffect, useMemo, useRef, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { api } from "@/api/client";
import { AlertDialog } from "@/components/ui/alert-dialog";
import { Download, FileText, Loader2, Maximize2, RefreshCw } from "lucide-react";
import {
  downloadClaimDocument,
  getClaimDocumentBlob,
  type StoredDocumentMeta,
} from "@/api/claims";
import { Button } from "@/components/ui/button";
import { SectionLabel } from "@/components/ui/section-label";
import { ClaimDocumentLightbox } from "@/components/claims/ClaimDocumentLightbox";
import { cn } from "@/lib/cn";
import { formatError } from "@/lib/errors";
import { toast } from "sonner";

function fileSize(bytes: number): string {
  if (bytes < 1024 * 1024) return `${Math.max(1, Math.round(bytes / 1024))} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

function previewType(doc: StoredDocumentMeta, blob: Blob): string {
  return blob.type || doc.mime_type || "application/octet-stream";
}

export function ClaimDocumentViewer({
  claimId,
  documents,
  revision,
  canManage = false,
}: {
  claimId: string;
  documents: StoredDocumentMeta[];
  revision?: number;
  canManage?: boolean;
}) {
  const queryClient = useQueryClient();
  const replacementInput = useRef<HTMLInputElement>(null);
  const [confirmRemoval, setConfirmRemoval] = useState(false);
  const [documentBusy, setDocumentBusy] = useState(false);
  const refresh = async () => {
    await Promise.all([
      queryClient.invalidateQueries({ queryKey: ["claims"] }),
      queryClient.invalidateQueries({ queryKey: ["claim-detail"] }),
    ]);
  };
  const [selectedId, setSelectedId] = useState(documents[0]?.id ?? null);
  const [preview, setPreview] = useState<{ url: string; mime: string } | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [retry, setRetry] = useState(0);
  const [fullScreenOpen, setFullScreenOpen] = useState(false);
  const fullScreenTriggerRef = useRef<HTMLButtonElement>(null);

  const selected = useMemo(
    () => documents.find((document) => document.id === selectedId) ?? documents[0],
    [documents, selectedId],
  );

  const addReplacement = async (file: File | undefined) => {
    if (!file) return;
    if (file.size > 15 * 1024 * 1024) {
      toast.error("Choose a document smaller than 15 MB.");
      return;
    }
    setDocumentBusy(true);
    try {
      const form = new FormData();
      form.append("file", file);
      if (selected?.doc_type) form.append("doc_type", selected.doc_type);
      await api.upload(`/claims/${claimId}/documents`, form);
      await refresh();
      toast.success(
        selected
          ? "Document added. You can now remove the earlier attachment if permitted."
          : "Document added.",
      );
    } catch (caught) {
      toast.error(formatError(caught));
    } finally {
      setDocumentBusy(false);
    }
  };

  useEffect(() => {
    if (!selectedId || documents.some((document) => document.id === selectedId)) {
      return;
    }
    setSelectedId(documents[0]?.id ?? null);
  }, [documents, selectedId]);

  useEffect(() => {
    let active = true;
    let objectUrl: string | null = null;
    setPreview(null);
    setError(null);
    setFullScreenOpen(false);
    if (!selected) {
      setLoading(false);
      return () => {
        active = false;
      };
    }

    setLoading(true);
    void getClaimDocumentBlob(claimId, selected)
      .then((blob) => {
        if (!active) return;
        objectUrl = URL.createObjectURL(blob);
        setPreview({ url: objectUrl, mime: previewType(selected, blob) });
      })
      .catch((caught) => {
        if (active) setError(formatError(caught));
      })
      .finally(() => {
        if (active) setLoading(false);
      });

    return () => {
      active = false;
      if (objectUrl) URL.revokeObjectURL(objectUrl);
    };
  }, [claimId, retry, selected]);

  if (documents.length === 0) {
    return (
      <section id="claim-documents" className="flex min-h-64 flex-col items-center justify-center gap-2 p-6 text-center">
        <FileText className="size-8 text-muted-foreground" aria-hidden />
        <SectionLabel as="h3">Documents</SectionLabel>
        <p className="max-w-md text-sm text-muted-foreground">
          No documents were submitted with this claim.
        </p>
        {canManage && (
          <>
            <input
              ref={replacementInput}
              type="file"
              accept=".pdf,.jpg,.jpeg,.png,.webp"
              className="hidden"
              aria-label="Add claim document"
              onChange={(event) => {
                const file = event.target.files?.[0];
                event.target.value = "";
                void addReplacement(file);
              }}
            />
            <Button
              type="button"
              size="sm"
              variant="outline"
              disabled={documentBusy}
              loading={documentBusy}
              onClick={() => replacementInput.current?.click()}
            >
              Add document
            </Button>
          </>
        )}
      </section>
    );
  }

  return (
    <section id="claim-documents" className="flex min-h-0 min-w-0 flex-1 flex-col" aria-label="Claim documents">
      <div className="shrink-0 border-b border-border px-4 py-3">
        <div className="flex min-h-10 items-center gap-3">
          <SectionLabel as="h3" className="shrink-0">
            Documents ({documents.length})
          </SectionLabel>
        <div
          className="flex min-w-0 flex-1 gap-2 overflow-x-auto"
          role="tablist"
          aria-label="Submitted documents"
        >
          {documents.map((document) => {
            const active = document.id === selected?.id;
            return (
              <button
                key={document.id}
                type="button"
                role="tab"
                aria-selected={active}
                aria-controls="claim-document-preview"
                title={document.file_name}
                className={cn(
                  "inline-flex h-11 max-w-56 shrink-0 items-center gap-2 rounded-md border px-3 text-xs font-medium transition-colors sm:h-9 lg:max-w-72",
                  "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring/40",
                  active
                    ? "border-input bg-foreground text-card"
                    : "border-border bg-card text-muted-foreground hover:bg-muted hover:text-foreground",
                )}
                onClick={() => setSelectedId(document.id)}
              >
                <FileText className="size-3.5 shrink-0" aria-hidden />
                <span className="truncate">{document.file_name}</span>
              </button>
            );
          })}
        </div>
          {selected && (
            <Button
              type="button"
              size="sm"
              variant="outline"
              className="h-11 shrink-0 sm:h-9"
              aria-label={`Download ${selected.file_name}`}
              onClick={async () => {
                try {
                  await downloadClaimDocument(claimId, selected);
                } catch (caught) {
                  toast.error(formatError(caught));
                }
              }}
            >
              <Download className="size-3.5" aria-hidden />
              <span className="hidden lg:inline">Download</span>
            </Button>
          )}
        </div>
      </div>

      {selected && <div className="space-y-2 border-b border-border px-4 py-3 text-xs text-muted-foreground">
        <p>{selected.removal_reason ?? "This attachment may be removed while required evidence remains. To replace it, upload the corrected file first."}</p>
        {canManage && <div className="flex flex-wrap gap-2">
          <input
            ref={replacementInput}
            type="file"
            accept=".pdf,.jpg,.jpeg,.png,.webp"
            className="hidden"
            aria-label="Add correction or replacement"
            onChange={(event) => {
              const file = event.target.files?.[0];
              event.target.value = "";
              void addReplacement(file);
            }}
          />
          <Button type="button" size="sm" variant="outline" disabled={documentBusy} onClick={() => replacementInput.current?.click()}>Add correction / replacement</Button>
          <Button type="button" size="sm" variant="outline" disabled={documentBusy || !selected.removal_allowed || revision == null} onClick={() => setConfirmRemoval(true)}>Remove attachment</Button>
        </div>}
      </div>}
      <AlertDialog open={confirmRemoval} onOpenChange={setConfirmRemoval} title="Remove attachment?" description={`Remove ${selected?.file_name ?? "this document"}? The file will be deleted; its removal remains in the audit history.`} confirmLabel="Remove attachment" loading={documentBusy} onConfirm={async () => {
        if (!selected || revision == null) return;
        setDocumentBusy(true);
        try {
          await api.delete(`/claims/${claimId}/documents/${selected.id}?expected_revision=${revision}`);
          setConfirmRemoval(false);
          await refresh();
          toast.success("Attachment removed. Audit history retained.");
        } catch (error) { toast.error(formatError(error)); await refresh(); }
        finally { setDocumentBusy(false); }
      }} />
      <div
        id="claim-document-preview"
        role="tabpanel"
        aria-label={selected ? `Preview of ${selected.file_name}` : "Document preview"}
        className="relative flex min-h-96 min-w-0 flex-1 overflow-hidden bg-muted/30"
      >
        {loading && (
          <div className="flex flex-1 items-center justify-center gap-2 text-sm text-muted-foreground">
            <Loader2 className="size-4 animate-spin" aria-hidden />
            Loading {selected?.file_name}…
          </div>
        )}

        {!loading && error && (
          <div className="m-auto flex max-w-md flex-col items-center gap-3 px-6 text-center">
            <p className="text-sm text-error">Couldn&apos;t preview this document. {error}</p>
            <Button type="button" size="sm" variant="outline" onClick={() => setRetry((value) => value + 1)}>
              <RefreshCw className="size-3.5" aria-hidden />
              Try again
            </Button>
          </div>
        )}

        {!loading && preview && selected && preview.mime.startsWith("image/") && (
          <button
            ref={fullScreenTriggerRef}
            type="button"
            className="absolute inset-0 flex cursor-zoom-in items-center justify-center overflow-hidden p-3 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-ring/50 sm:p-4"
            aria-label={`Open ${selected.file_name} full screen`}
            onClick={() => setFullScreenOpen(true)}
          >
            <img
              src={preview.url}
              alt={`Preview of ${selected.file_name}`}
              className="h-full w-full object-contain"
            />
            <span className="absolute bottom-3 right-3 inline-flex size-10 items-center justify-center rounded-md bg-foreground/85 text-card shadow-sm backdrop-blur-sm" aria-hidden>
              <Maximize2 className="size-4" />
            </span>
          </button>
        )}

        {!loading &&
          preview &&
          selected &&
          preview.mime.toLowerCase().startsWith("application/pdf") && (
          <>
            <iframe
              src={`${preview.url}#view=Fit&toolbar=1&navpanes=0`}
              title={`Preview of ${selected.file_name}`}
              className="min-h-96 min-w-0 w-full border-0 bg-card"
            />
            <Button
              ref={fullScreenTriggerRef}
              type="button"
              size="icon"
              variant="secondary"
              className="absolute bottom-3 right-3 z-10 size-10 bg-card/90 shadow-md backdrop-blur-sm"
              aria-label={`Open ${selected.file_name} full screen`}
              onClick={() => setFullScreenOpen(true)}
            >
              <Maximize2 className="size-4" aria-hidden />
            </Button>
          </>
        )}

        {!loading &&
          preview &&
          selected &&
          !preview.mime.startsWith("image/") &&
          !preview.mime.toLowerCase().startsWith("application/pdf") && (
            <div className="m-auto flex max-w-md flex-col items-center gap-3 px-6 text-center">
              <FileText className="size-8 text-muted-foreground" aria-hidden />
              <p className="text-sm text-muted-foreground">
                This file type cannot be previewed by the browser. Download the
                {` ${fileSize(selected.size_bytes)} `}file to open it.
              </p>
            </div>
          )}
      </div>

      {preview && selected &&
        (preview.mime.startsWith("image/") ||
          preview.mime.toLowerCase().startsWith("application/pdf")) && (
          <ClaimDocumentLightbox
            fileName={selected.file_name}
            mime={preview.mime}
            onCloseAutoFocus={() => fullScreenTriggerRef.current?.focus()}
            onOpenChange={setFullScreenOpen}
            open={fullScreenOpen}
            url={preview.url}
          />
        )}
    </section>
  );
}
