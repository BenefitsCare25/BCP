/** Send forms to HR without a portal sign-in: prepared batches, each with one
 *  live link + PIN at a time. */
import { useState } from "react";
import { Archive, FileArchive, KeyRound, Link2Off, Send } from "lucide-react";
import { toast } from "sonner";
import type { EnrollmentWindow } from "@/api/enrollment";
import { useBatchAction, useHrBatches, type Batch, type Grant } from "@/api/qrEnrolment";
import { AlertDialog } from "@/components/ui/alert-dialog";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { formatError } from "@/lib/errors";
import { fmtDateTime } from "@/lib/format";
import { HrLinkSheet } from "./HrLinkSheet";
import { PrepareBatchSheet } from "./PrepareBatchSheet";

function size(bytes: number): string {
  return bytes >= 1024 * 1024 ? `${(bytes / (1024 * 1024)).toFixed(1)} MB` : `${Math.max(1, Math.round(bytes / 1024))} KB`;
}

function grantBadge(batch: Batch): { label: string; variant: "good" | "warn" | "error" | "outline" | "info" } {
  if (batch.status !== "ready") return { label: batch.status === "closed" ? "Closed" : "Expired", variant: "outline" };
  const g: Grant | null = batch.grant;
  if (!g) return { label: "No link yet", variant: "outline" };
  switch (g.state) {
    case "ready": return { label: "Link sent, not used", variant: "info" };
    case "active": return { label: `Downloading until ${fmtDateTime(g.expires_at)}`, variant: "good" };
    case "expired": return { label: g.activated_at ? "Downloaded, window ended" : "Link expired", variant: "warn" };
    case "locked": return { label: "Locked: wrong PINs", variant: "error" };
    default: return { label: g.revoke_reason === "reissued" ? "Link replaced" : "Link revoked", variant: "outline" };
  }
}

function BatchRow({
  batch, canIssue, onLink, onRevoke, onClose,
}: {
  batch: Batch;
  canIssue: boolean;
  onLink: () => void;
  onRevoke: (grant: Grant) => void;
  onClose: () => void;
}) {
  const badge = grantBadge(batch);
  const live = batch.status === "ready";
  const grant = batch.grant;
  const revocable = grant && (grant.state === "ready" || grant.state === "active");
  return (
    <div className="flex flex-col gap-3 px-4 py-3 sm:flex-row sm:items-center sm:px-5">
      <div className="min-w-0 flex-1">
        <p className="flex flex-wrap items-center gap-2 font-medium text-foreground">
          <span className="truncate">{batch.label}</span>
          <Badge variant={badge.variant}>{badge.label}</Badge>
        </p>
        <p className="text-sm text-muted-foreground">
          {batch.file_count} forms · {size(batch.size_bytes)} · prepared {fmtDateTime(batch.created_at)}
          {live ? ` · kept until ${fmtDateTime(batch.expires_at)}` : ""}
          {grant?.recipient_note ? ` · to ${grant.recipient_note}` : ""}
          {grant?.downloads ? ` · downloaded ${grant.downloads}×` : ""}
        </p>
      </div>
      {canIssue && live && (
        <div className="flex shrink-0 flex-wrap gap-2">
          <Button variant={grant ? "outline" : "default"} onClick={onLink}>
            <KeyRound className="size-4" /> {grant ? "New link and PIN" : "Create link and PIN"}
          </Button>
          {revocable && (
            <Button variant="ghost" onClick={() => onRevoke(grant)}>
              <Link2Off className="size-4" /> Revoke link
            </Button>
          )}
          <Button variant="ghost" onClick={onClose}>
            <Archive className="size-4" /> Close
          </Button>
        </div>
      )}
    </div>
  );
}

export function HrDownloadPanel({
  policyYearId,
  windows,
  defaultWindowId,
  companyName,
  canIssue,
}: {
  policyYearId: string;
  windows: EnrollmentWindow[];
  defaultWindowId?: string;
  companyName: string;
  canIssue: boolean;
}) {
  const batches = useHrBatches(policyYearId);
  const act = useBatchAction();
  const [preparing, setPreparing] = useState(false);
  const [linkFor, setLinkFor] = useState<Batch | null>(null);
  const [closing, setClosing] = useState<Batch | null>(null);

  const revoke = async (grant: Grant) => {
    try {
      await act.mutateAsync({ kind: "revoke", grantId: grant.id });
      toast.success("Link revoked");
    } catch (e) {
      toast.error(formatError(e));
    }
  };

  return (
    <section aria-labelledby="hr-batches-title" className="space-y-3">
      <div className="flex flex-col gap-3 sm:flex-row sm:items-end sm:justify-between">
        <div>
          <h3 id="hr-batches-title" className="font-semibold text-foreground">Send forms to HR</h3>
          <p className="max-w-2xl text-sm text-muted-foreground">
            Pack form PDFs into a ZIP and give HR a QR link with a PIN. HR downloads without signing in,
            within 10 minutes of starting. Their HR portal keeps working as usual.
          </p>
        </div>
        {canIssue && (
          <Button onClick={() => setPreparing(true)}><Send className="size-4" /> Prepare forms for HR</Button>
        )}
      </div>
      {batches.isLoading ? (
        <Skeleton className="h-20 rounded-lg" />
      ) : batches.error ? (
        <Card className="p-5 text-sm text-muted-foreground" role="alert">{formatError(batches.error)}</Card>
      ) : !batches.data?.items.length ? (
        <Card className="flex items-center gap-3 p-5 text-sm text-muted-foreground">
          <FileArchive className="size-5 shrink-0" aria-hidden />
          No downloads prepared yet.
        </Card>
      ) : (
        <Card className="divide-y divide-border overflow-hidden">
          {batches.data.items.map(batch => (
            <BatchRow key={batch.id} batch={batch} canIssue={canIssue} onLink={() => setLinkFor(batch)}
              onRevoke={grant => void revoke(grant)} onClose={() => setClosing(batch)} />
          ))}
        </Card>
      )}
      <PrepareBatchSheet
        open={preparing}
        policyYearId={policyYearId}
        windows={windows}
        defaultWindowId={defaultWindowId}
        onClose={() => setPreparing(false)}
        onPrepared={batch => { setPreparing(false); setLinkFor(batch); }}
      />
      <HrLinkSheet batch={linkFor} companyName={companyName} onClose={() => setLinkFor(null)} />
      <AlertDialog
        open={!!closing}
        onOpenChange={o => { if (!o) setClosing(null); }}
        title="Close this download?"
        description="Its link stops working and the prepared ZIP is deleted. The forms themselves stay in the register."
        confirmLabel="Close download"
        loading={act.isPending}
        onConfirm={async () => {
          if (!closing) return;
          try {
            await act.mutateAsync({ kind: "close", batchId: closing.id });
            toast.success("Download closed");
          } catch (e) {
            toast.error(formatError(e));
          }
          setClosing(null);
        }}
      />
    </section>
  );
}
