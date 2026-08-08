import { useState } from "react";
import { Download, Loader2 } from "lucide-react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { api } from "@/api/client";
import { triggerDownload } from "@/lib/download";
import { formatError } from "@/lib/errors";

interface Props {
  /** API path (relative to /api/v1) returning an .xlsx blob. */
  path: string;
  /** Fallback download filename. */
  filename: string;
  label: string;
  disabled?: boolean;
  size?: "default" | "sm";
  /** `ghost` for a download that is the quiet half of a paired action. */
  variant?: "outline" | "ghost";
  /** Fired after the file has been handed to the browser, with what the
   *  server's retention did (`X-Inspro-Report-Filed`). Downloading a
   *  submission-grade report FILES a retained copy, so the record line above it
   *  is stale the moment the click succeeds — and when nothing needed filing,
   *  that line looks identical either way, which is the one case a broker
   *  cannot tell apart on their own. */
  onDownloaded?: (filed: string | null) => void;
}

/** What the server did with the copy, in the broker's words.
 *
 *  "unchanged" is the message that matters: the changed-since badge fires on ANY
 *  roster or config edit while filing compares the report's BYTES, so an edit
 *  that does not reach this insurer leaves the badge showing after a download —
 *  and without this the broker has no way to learn why, and downloads again. */
function describeFiled(filed: string | null): string {
  if (!filed) return "Report downloaded";
  if (filed === "error") {
    return "Report downloaded — but it could not be filed. Try again later.";
  }
  const unchanged = filed.startsWith("unchanged:");
  const version = filed.replace("unchanged:", "");
  return unchanged
    ? `Report downloaded — identical to ${version}, nothing new filed`
    : `Report downloaded and filed as ${filed}`;
}

/** Outline button that downloads an .xlsx export, mirroring the coverage
 * "Export Excel" pattern (spinner while preparing, error toast). */
export function ReportDownloadButton({
  path,
  filename,
  label,
  disabled,
  size = "default",
  variant = "outline",
  onDownloaded,
}: Props) {
  const [busy, setBusy] = useState(false);

  async function onClick() {
    setBusy(true);
    try {
      const res = await api.downloadResponse(path);
      const filed = res.headers.get("X-Inspro-Report-Filed");
      triggerDownload(await res.blob(), filename);
      toast.success(describeFiled(filed));
      onDownloaded?.(filed);
    } catch (e) {
      toast.error(formatError(e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <Button
      type="button"
      variant={variant}
      size={size}
      onClick={onClick}
      disabled={busy || disabled}
    >
      {busy ? (
        <Loader2 className="size-4 animate-spin" />
      ) : (
        <Download className="size-4" />
      )}
      {label}
    </Button>
  );
}
