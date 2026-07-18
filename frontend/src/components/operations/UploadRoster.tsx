import { useRef, useState } from "react";
import { Upload, FileSpreadsheet, AlertTriangle } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { ReportDownloadButton } from "@/components/operations/ReportDownloadButton";
import { toast } from "sonner";
import type { UseMutationResult } from "@tanstack/react-query";
import type { UploadResult } from "@/types";

interface Props {
  title: string;
  description: string;
  policyYearId: string;
  upload: UseMutationResult<
    UploadResult,
    Error,
    { file: File; policyYearId: string }
  >;
  /** Optional API path for a downloadable blank/pre-filled upload template. */
  templatePath?: string;
  templateFilename?: string;
}

const REASON_LABEL: Record<string, string> = {
  existing: "Already on file",
  in_file: "Repeated in this file",
};

export function UploadRoster({
  title,
  description,
  policyYearId,
  upload,
  templatePath,
  templateFilename,
}: Props) {
  const fileInput = useRef<HTMLInputElement>(null);
  const [result, setResult] = useState<UploadResult | null>(null);

  const onPick = (file: File | null) => {
    if (!file) return;
    upload.mutate(
      { file, policyYearId },
      {
        onSuccess: (r) => {
          setResult(r);
          if (r.skipped > 0) {
            toast.warning(
              `${r.inserted} added · ${r.skipped} duplicate${r.skipped === 1 ? "" : "s"} skipped`,
            );
          } else {
            toast.success(`${r.inserted} rows added`);
          }
        },
        // Errors surface through the global MutationCache.onError toast (which
        // uses formatError) — no local handler, so it isn't toasted twice.
      },
    );
    // Allow re-picking the same file after a fix.
    if (fileInput.current) fileInput.current.value = "";
  };

  const duplicates = result?.duplicates ?? [];
  const errors = result?.errors ?? [];

  return (
    <Card>
      <CardContent className="p-5">
        <div className="flex items-center justify-between gap-3 flex-wrap">
          <div className="flex items-center gap-4">
            <div className="size-11 rounded-lg bg-accent text-accent-foreground grid place-items-center">
              <FileSpreadsheet className="size-5" />
            </div>
            <div>
              <div className="font-medium text-foreground">{title}</div>
              <div className="text-sm text-muted-foreground">{description}</div>
            </div>
          </div>
          <div className="flex items-center gap-3">
            {templatePath && (
              <ReportDownloadButton
                path={templatePath}
                filename={templateFilename ?? "member-listing-template.xlsx"}
                label="Download template"
              />
            )}
            <input
              ref={fileInput}
              type="file"
              accept=".xls,.xlsx,.xlsm"
              aria-label={title}
              className="hidden"
              onChange={(e) => onPick(e.target.files?.[0] ?? null)}
            />
            <Button
              onClick={() => fileInput.current?.click()}
              disabled={upload.isPending}
            >
              <Upload className="size-4" />
              {upload.isPending ? "Uploading…" : "Upload"}
            </Button>
          </div>
        </div>

        {duplicates.length > 0 && (
          <div className="mt-4 rounded-lg border border-warn/40 bg-warn-soft/40 p-3">
            <div className="mb-2 flex items-center gap-2 text-sm font-medium text-warn-foreground">
              <AlertTriangle className="size-4" />
              {duplicates.length} duplicate
              {duplicates.length === 1 ? "" : "s"} skipped — review below
            </div>
            <div className="max-h-56 overflow-y-auto rounded-md border border-border">
              <table className="w-full text-sm">
                <thead className="sticky top-0 bg-muted text-muted-foreground">
                  <tr>
                    <th className="px-3 py-1.5 text-left font-medium">Row</th>
                    <th className="px-3 py-1.5 text-left font-medium">Name</th>
                    <th className="px-3 py-1.5 text-left font-medium">Staff ID</th>
                    <th className="px-3 py-1.5 text-left font-medium">NRIC/FIN</th>
                    <th className="px-3 py-1.5 text-left font-medium">Reason</th>
                  </tr>
                </thead>
                <tbody>
                  {duplicates.map((d, i) => (
                    <tr key={i} className="border-t border-border">
                      <td className="px-3 py-1.5 text-muted-foreground">{d.row}</td>
                      <td className="px-3 py-1.5 text-foreground">{d.name || "—"}</td>
                      <td className="px-3 py-1.5 text-foreground">
                        {d.staff_id || "—"}
                      </td>
                      <td className="px-3 py-1.5 font-mono text-muted-foreground">
                        {d.nric_masked || "—"}
                      </td>
                      <td className="px-3 py-1.5 text-muted-foreground">
                        {REASON_LABEL[d.reason] ?? d.reason}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            <div className="mt-2 text-xs text-muted-foreground">
              To change an existing person, use “Bulk update roster (ADC)”.
            </div>
          </div>
        )}

        {errors.length > 0 && (
          <div className="mt-4 rounded-lg border border-warn/40 bg-warn-soft/40 p-3">
            <div className="mb-1.5 flex items-center gap-2 text-sm font-medium text-warn-foreground">
              <AlertTriangle className="size-4" />
              {errors.length} warning{errors.length === 1 ? "" : "s"}
            </div>
            <ul className="list-disc pl-5 text-xs text-muted-foreground space-y-0.5">
              {errors.map((msg, i) => (
                <li key={i}>{msg}</li>
              ))}
            </ul>
          </div>
        )}
      </CardContent>
    </Card>
  );
}
