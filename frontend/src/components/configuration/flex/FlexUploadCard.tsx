import { useRef, useState } from "react";
import { Upload, FileText, CheckCircle2, AlertTriangle, Sparkles } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { InfoHint } from "@/components/ui/tooltip";
import { useUploadFlex } from "@/api/hooks";
import { formatError } from "@/lib/errors";
import { toast } from "sonner";

interface Props {
  policyYearId: string;
  /** Re-upload variant renders a compact inline button instead of the hero card. */
  compact?: boolean;
}

const ALLOWED_EXT = [".pdf", ".png", ".jpg", ".jpeg", ".msg"];
const MAX_BYTES = 50 * 1024 * 1024; // mirrors backend DEFAULT_MAX_BYTES

function validateFile(file: File): string | null {
  const lower = file.name.toLowerCase();
  if (!ALLOWED_EXT.some((ext) => lower.endsWith(ext))) {
    return `Unsupported file type — use ${ALLOWED_EXT.join(", ")}.`;
  }
  if (file.size === 0) return "File is empty.";
  if (file.size > MAX_BYTES) {
    return `File is too large (${(file.size / 1024 / 1024).toFixed(1)} MB) — max 50 MB.`;
  }
  return null;
}

export function FlexUploadCard({ policyYearId, compact = false }: Props) {
  const fileInput = useRef<HTMLInputElement>(null);
  const [filename, setFilename] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const upload = useUploadFlex(policyYearId);

  const onPick = (picked: FileList | null) => {
    const files = picked ? Array.from(picked) : [];
    if (files.length === 0) return;
    const err = files.map(validateFile).find(Boolean) ?? null;
    setFilename(
      files.length === 1 ? files[0].name : `${files.length} files`,
    );
    if (err) {
      setError(err);
      toast.error(err);
      return;
    }
    setError(null);
    upload.mutate(files, {
      onSuccess: (scheme) => {
        const tiers = scheme.scheme.tiers?.length ?? 0;
        toast.success(
          `${tiers} eligibility tier${tiers === 1 ? "" : "s"} in the scheme`,
          {
            description:
              files.length > 1
                ? `Merged ${files.length} documents. Review before confirming.`
                : "Review the extracted scheme below before confirming.",
          },
        );
      },
      onError: (e) => toast.error(formatError(e)),
    });
  };

  const hiddenInput = (
    <input
      ref={fileInput}
      type="file"
      accept={ALLOWED_EXT.join(",")}
      multiple
      className="hidden"
      onChange={(e) => {
        onPick(e.target.files);
        e.target.value = "";
      }}
    />
  );

  if (compact) {
    return (
      <>
        {hiddenInput}
        <Button
          variant="outline"
          size="sm"
          onClick={() => fileInput.current?.click()}
          disabled={upload.isPending}
          title="Upload Flexible Benefits documents (PDF, image or .msg — max 50 MB each). AI extracts the scheme; select multiple files to merge."
        >
          <Upload className="size-3.5" />
          {upload.isPending ? "Extracting…" : "Upload documents"}
        </Button>
      </>
    );
  }

  return (
    <Card>
      <CardContent className="p-5 space-y-4">
        <div className="flex items-center justify-between gap-4 flex-wrap">
          <div className="flex items-center gap-4">
            <div className="size-11 rounded-lg bg-accent text-accent-foreground grid place-items-center">
              <FileText className="size-5" />
            </div>
            <div>
              <div className="font-medium text-foreground flex items-center gap-2">
                Upload Flexible Benefits documents
                <Badge variant="primary" className="gap-1">
                  <Sparkles className="size-3" /> AI extraction
                </Badge>
                <InfoHint>
                  PDF, image (PNG/JPG) or Outlook .msg — max 50 MB each. Select
                  multiple files at once (e.g. each grade band + the country
                  tables); AI reads the four parameters from each and merges them
                  into one scheme, then you review before confirming.
                </InfoHint>
              </div>
            </div>
          </div>

          <div className="flex items-center gap-3">
            {filename && (
              <div className="flex items-center gap-2 text-sm">
                {upload.isPending ? (
                  <Badge variant="warn">Extracting…</Badge>
                ) : error ? (
                  <Badge variant="error" className="gap-1">
                    <AlertTriangle className="size-3" /> Invalid
                  </Badge>
                ) : upload.isSuccess ? (
                  <Badge variant="good" className="gap-1">
                    <CheckCircle2 className="size-3" /> Done
                  </Badge>
                ) : null}
                <span className="text-muted-foreground">{filename}</span>
              </div>
            )}
            {hiddenInput}
            <Button
              onClick={() => fileInput.current?.click()}
              disabled={upload.isPending}
            >
              <Upload className="size-4" /> Choose files
            </Button>
          </div>
        </div>

        {error && (
          <div className="flex items-start gap-2 rounded-lg border border-border bg-error-soft/40 p-3 text-sm text-foreground">
            <AlertTriangle className="size-4 text-error mt-0.5 shrink-0" />
            <span>{error}</span>
          </div>
        )}
      </CardContent>
    </Card>
  );
}
