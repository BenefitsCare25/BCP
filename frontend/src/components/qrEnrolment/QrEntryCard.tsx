/** The shared employee QR for one enrolment period.
 *
 * One QR for the whole company: the broker downloads it (or copies the link),
 * sends it to HR, and HR forwards it to staff. Downloading again gives the same
 * QR, so copies already forwarded keep working; "Replace QR" retires them all. */
import { useState } from "react";
import { Copy, Download, Pause, Play, QrCode, RefreshCw } from "lucide-react";
import { toast } from "sonner";
import type { EnrollmentWindow } from "@/api/enrollment";
import { useQrEntry, useQrEntryAction, useQrEntryLink, type QrEntry } from "@/api/qrEnrolment";
import { AlertDialog } from "@/components/ui/alert-dialog";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { formatError } from "@/lib/errors";
import { triggerDownload } from "@/lib/download";
import { fmtDateTime } from "@/lib/format";
import { composeQrPng, QrPreview, useQrCanvas } from "./qrImage";

type EntryAction = "enable" | "pause" | "resume" | "rotate";

function Counts({ data }: { data: QrEntry | undefined }) {
  const rows = [["Waiting to match", data?.pending], ["Matched", data?.linked], ["Rejected", data?.rejected]] as const;
  return (
    <dl className="grid grid-cols-3 gap-3 text-sm">
      {rows.map(([term, n]) => (
        <div key={term} className="rounded-lg bg-muted px-3 py-2">
          <dt className="text-xs text-muted-foreground">{term}</dt>
          <dd className="text-lg font-semibold tabular-nums text-foreground">{n ?? 0}</dd>
        </div>
      ))}
    </dl>
  );
}

function EntryActions({
  url, enabled, closed, busy, onDownload, onRun, onRotate,
}: {
  url: string | undefined;
  enabled: boolean;
  closed: boolean;
  busy: boolean;
  onDownload: () => void;
  onRun: (kind: EntryAction, done: string) => void;
  onRotate: () => void;
}) {
  const copy = async () => {
    if (!url) return;
    try {
      await navigator.clipboard.writeText(url);
      toast.success("Link copied");
    } catch {
      toast.error("Copy failed. Select the link and copy it instead.");
    }
  };
  return (
    <div className="flex flex-wrap gap-2">
      <Button onClick={onDownload} disabled={!url}><Download className="size-4" /> Download QR (PNG)</Button>
      <Button variant="outline" onClick={() => void copy()} disabled={!url}><Copy className="size-4" /> Copy link</Button>
      {enabled ? (
        <Button variant="outline" onClick={() => onRun("pause", "QR form paused")} loading={busy}>
          <Pause className="size-4" /> Pause
        </Button>
      ) : (
        <Button variant="outline" onClick={() => onRun("resume", "QR form resumed")} loading={busy} disabled={closed}>
          <Play className="size-4" /> Resume
        </Button>
      )}
      <Button variant="ghost" onClick={onRotate}><RefreshCw className="size-4" /> Replace QR</Button>
    </div>
  );
}

function LiveEntry({
  period, companyName, readOnly, data, busy, onRun, onRotate,
}: {
  period: EnrollmentWindow;
  companyName: string;
  readOnly: boolean;
  data: QrEntry | undefined;
  busy: boolean;
  onRun: (kind: EntryAction, done: string) => void;
  onRotate: () => void;
}) {
  const link = useQrEntryLink(period.id, !readOnly);
  const canvasRef = useQrCanvas();
  const download = async () => {
    if (!canvasRef.current) return;
    try {
      const blob = await composeQrPng(canvasRef.current, {
        title: `${companyName} enrolment form`,
        lines: [period.name, `Closes ${fmtDateTime(period.closes_at)}`],
        footer: "Scan with your phone camera. No login needed.",
      });
      triggerDownload(blob, `${companyName} enrolment QR.png`);
    } catch (e) {
      toast.error(formatError(e));
    }
  };
  return (
    <div className="mt-4 grid gap-5 md:grid-cols-[auto_minmax(0,1fr)]">
      {link.data ? (
        <QrPreview value={link.data.url} canvasRef={canvasRef} label={`${companyName} enrolment form QR code`} />
      ) : readOnly ? (
        <div className="grid size-48 place-items-center rounded-lg border border-border text-center text-xs text-muted-foreground">
          The QR is available to broker administrators.
        </div>
      ) : (
        <Skeleton className="size-48 rounded-lg" />
      )}
      <div className="min-w-0 space-y-4">
        <Counts data={data} />
        {link.data && (
          <p className="truncate rounded-md bg-muted px-3 py-2 font-mono text-xs text-muted-foreground" title={link.data.url}>
            {link.data.url}
          </p>
        )}
        {!readOnly && (
          <EntryActions url={link.data?.url} enabled={!!data?.enabled} closed={period.status === "closed"} busy={busy}
            onDownload={() => void download()} onRun={onRun} onRotate={onRotate} />
        )}
        <p className="text-xs text-muted-foreground">
          Open while the period is open (closes {fmtDateTime(period.closes_at)}).
          {data?.rotated_at ? ` QR last replaced ${fmtDateTime(data.rotated_at)}.` : ""}
        </p>
      </div>
    </div>
  );
}

export function QrEntryCard({
  window: period,
  companyName,
  readOnly,
}: {
  window: EnrollmentWindow;
  companyName: string;
  readOnly: boolean;
}) {
  const entry = useQrEntry(period.id);
  const live = !!entry.data?.exists;
  const action = useQrEntryAction(period.id);
  const [confirmRotate, setConfirmRotate] = useState(false);
  const closed = period.status === "closed";

  const run = async (kind: EntryAction, done: string) => {
    try {
      await action.mutateAsync(kind);
      toast.success(done);
    } catch (e) {
      toast.error(formatError(e));
    }
  };
  if (entry.isLoading) return <Skeleton className="h-56 rounded-lg" />;
  if (entry.error) {
    return <Card className="p-5 text-sm text-muted-foreground" role="alert">{formatError(entry.error)}</Card>;
  }
  const data = entry.data;
  const status = !live ? null : closed ? "Period closed" : data?.enabled ? "Live" : "Paused";

  return (
    <Card className="p-5">
      <h3 className="flex items-center gap-2 font-semibold text-foreground">
        <QrCode className="size-4 text-muted-foreground" aria-hidden /> Shared QR form
        {status && <Badge variant={status === "Live" ? "good" : "warn"}>{status}</Badge>}
      </h3>
      <p className="mt-1 max-w-2xl text-sm text-muted-foreground">
        One QR for everyone in this period. Download it and send it to HR to share with employees.
        Employees fill in the form without signing in; each submission waits in QR submissions below
        until you match it to an employee.
      </p>
      {!live ? (
        <div className="mt-4 flex flex-col items-start gap-3 rounded-lg border border-dashed border-border p-4">
          <p className="text-sm text-foreground">
            {closed ? "This period has closed." : "The QR form is off for this period."}
          </p>
          {!readOnly && !closed && (
            <Button onClick={() => void run("enable", "QR form turned on")} loading={action.isPending}>
              <QrCode className="size-4" /> Turn on QR form
            </Button>
          )}
        </div>
      ) : (
        <LiveEntry period={period} companyName={companyName} readOnly={readOnly} data={data}
          busy={action.isPending} onRun={(k, d) => void run(k, d)} onRotate={() => setConfirmRotate(true)} />
      )}
      <AlertDialog
        open={confirmRotate}
        onOpenChange={setConfirmRotate}
        title="Replace the QR?"
        description="Every copy of the current QR and link stops working at once, including ones HR has already shared. Send the new QR to HR afterwards."
        confirmLabel="Replace QR"
        loading={action.isPending}
        onConfirm={async () => {
          await run("rotate", "QR replaced. Send the new one to HR.");
          setConfirmRotate(false);
        }}
      />
    </Card>
  );
}
