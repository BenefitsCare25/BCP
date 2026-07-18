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
}

/** Outline button that downloads an .xlsx export, mirroring the coverage
 * "Export Excel" pattern (spinner while preparing, error toast). */
export function ReportDownloadButton({
  path,
  filename,
  label,
  disabled,
  size = "default",
}: Props) {
  const [busy, setBusy] = useState(false);

  async function onClick() {
    setBusy(true);
    try {
      const blob = await api.download(path);
      triggerDownload(blob, filename);
      toast.success("Report downloaded");
    } catch (e) {
      toast.error(formatError(e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <Button
      type="button"
      variant="outline"
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
