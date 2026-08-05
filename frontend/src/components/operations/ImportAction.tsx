import { useRef, type ReactNode } from "react";
import { Loader2, Upload } from "lucide-react";
import { Button } from "@/components/ui/button";
import { ReportDownloadButton } from "./ReportDownloadButton";

/** Spreadsheet formats `excel_reader.open_workbook` accepts. */
const SPREADSHEET_ACCEPT = ".xls,.xlsx,.xlsm";

interface Props {
  templatePath: string;
  templateFilename: string;
  templateLabel: string;
  uploadLabel: string;
  onPick: (file: File) => void;
  pending?: boolean;
  /** Filled treatment. At most ONE action in a bar may be primary. */
  primary?: boolean;
  /** Overrides the upload glyph (ADC uses a diff icon, not a plain upload). */
  icon?: ReactNode;
  disabled?: boolean;
}

/**
 * One import job as a pair: the template that starts it, then the upload that
 * finishes it. The template is the quieter half (ghost) because it is the step
 * a broker skips once they have the file — the pairing is carried by proximity,
 * so several of these can sit in one toolbar separated by a rule, and each
 * button still names its own job.
 */
export function ImportAction({
  templatePath,
  templateFilename,
  templateLabel,
  uploadLabel,
  onPick,
  pending,
  primary,
  icon,
  disabled,
}: Props) {
  const fileInput = useRef<HTMLInputElement>(null);

  return (
    <div className="flex items-center gap-1">
      <ReportDownloadButton
        path={templatePath}
        filename={templateFilename}
        label={templateLabel}
        variant="ghost"
        disabled={disabled}
      />
      <input
        ref={fileInput}
        type="file"
        accept={SPREADSHEET_ACCEPT}
        aria-label={uploadLabel}
        className="hidden"
        onChange={(e) => {
          const file = e.target.files?.[0];
          // Clear before dispatching so re-picking the same file after a fix
          // still fires a change event.
          e.target.value = "";
          if (file) onPick(file);
        }}
      />
      <Button
        variant={primary ? "default" : "outline"}
        onClick={() => fileInput.current?.click()}
        disabled={pending || disabled}
      >
        {pending ? (
          <Loader2 className="size-4 animate-spin" />
        ) : (
          (icon ?? <Upload className="size-4" />)
        )}
        {uploadLabel}
      </Button>
    </div>
  );
}
