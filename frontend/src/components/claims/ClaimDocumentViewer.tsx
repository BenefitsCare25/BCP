import { useEffect, useMemo, useState } from "react";
import { Download, FileText, Loader2, RefreshCw } from "lucide-react";
import {
  downloadClaimDocument,
  getClaimDocumentBlob,
  type StoredDocumentMeta,
} from "@/api/claims";
import { Button } from "@/components/ui/button";
import { SectionLabel } from "@/components/ui/section-label";
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
}: {
  claimId: string;
  documents: StoredDocumentMeta[];
}) {
  const [selectedId, setSelectedId] = useState(documents[0]?.id ?? null);
  const [preview, setPreview] = useState<{ url: string; mime: string } | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [retry, setRetry] = useState(0);

  const selected = useMemo(
    () => documents.find((document) => document.id === selectedId) ?? documents[0],
    [documents, selectedId],
  );

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
      </section>
    );
  }

  return (
    <section id="claim-documents" className="flex min-h-0 flex-1 flex-col" aria-label="Claim documents">
      <div className="space-y-3 border-b border-border px-5 py-4">
        <div className="flex items-center justify-between gap-3">
          <div className="space-y-1">
            <SectionLabel as="h3">Documents ({documents.length})</SectionLabel>
            <p className="text-xs text-muted-foreground">
              Select a file to review it without leaving the claim.
            </p>
          </div>
          {selected && (
            <Button
              type="button"
              size="sm"
              variant="outline"
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
              Download
            </Button>
          )}
        </div>
        <div
          className="flex max-w-full gap-2 overflow-x-auto pb-1"
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
                className={cn(
                  "inline-flex h-9 max-w-64 shrink-0 items-center gap-2 rounded-md border px-3 text-xs font-medium transition-colors",
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
      </div>

      <div
        id="claim-document-preview"
        role="tabpanel"
        aria-label={selected ? `Preview of ${selected.file_name}` : "Document preview"}
        className="relative flex min-h-96 flex-1 overflow-hidden bg-muted"
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
          <div className="flex min-h-96 w-full items-start justify-center overflow-auto p-4 sm:p-6">
            <img
              src={preview.url}
              alt={`Preview of ${selected.file_name}`}
              className="h-auto max-w-full bg-card object-contain shadow-lg"
            />
          </div>
        )}

        {!loading && preview && selected && preview.mime === "application/pdf" && (
          <iframe
            src={preview.url}
            title={`Preview of ${selected.file_name}`}
            className="min-h-96 w-full border-0 bg-card"
          />
        )}

        {!loading &&
          preview &&
          selected &&
          !preview.mime.startsWith("image/") &&
          preview.mime !== "application/pdf" && (
            <div className="m-auto flex max-w-md flex-col items-center gap-3 px-6 text-center">
              <FileText className="size-8 text-muted-foreground" aria-hidden />
              <p className="text-sm text-muted-foreground">
                This file type cannot be previewed by the browser. Download the
                {` ${fileSize(selected.size_bytes)} `}file to open it.
              </p>
            </div>
          )}
      </div>
    </section>
  );
}
