/** Create the link + PIN HR uses to download a prepared batch.
 *
 * The PIN is shown once, here. The broker sends the QR or link by email and
 * gives the PIN separately (a call or a text), so a forwarded email alone
 * cannot open the forms. Creating a new link retires the previous one. */
import { useState } from "react";
import { Copy, Download, KeyRound, ShieldAlert } from "lucide-react";
import { toast } from "sonner";
import { useBatchAction, type Batch, type IssuedGrant } from "@/api/qrEnrolment";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Sheet, SheetBody, SheetContent, SheetDescription, SheetFooter, SheetHeader, SheetTitle,
} from "@/components/ui/sheet";
import { formatError } from "@/lib/errors";
import { triggerDownload } from "@/lib/download";
import { fmtDateTime } from "@/lib/format";
import { composeQrPng, QrPreview, useQrCanvas } from "./qrImage";

async function copyText(text: string, done: string) {
  try {
    await navigator.clipboard.writeText(text);
    toast.success(done);
  } catch {
    toast.error("Copy failed. Select the text and copy it instead.");
  }
}

function IssuedView({
  issued, canvasRef, onDownload,
}: {
  issued: IssuedGrant;
  canvasRef: React.RefObject<HTMLCanvasElement>;
  onDownload: () => void;
}) {
  return (
    <>
      <div className="grid gap-5 sm:grid-cols-[auto_minmax(0,1fr)]">
        <QrPreview value={issued.url} canvasRef={canvasRef} label="HR download QR code" />
        <div className="min-w-0 space-y-3">
          <div>
            <p className="text-xs text-muted-foreground">PIN (shown only now)</p>
            <p className="flex items-center gap-3">
              <span className="font-mono text-3xl font-semibold tracking-[0.25em] text-foreground tabular-nums">{issued.pin}</span>
              <Button variant="outline" size="sm" onClick={() => void copyText(issued.pin, "PIN copied")}>
                <KeyRound className="size-4" /> Copy PIN
              </Button>
            </p>
          </div>
          <p className="truncate rounded-md bg-muted px-3 py-2 font-mono text-xs text-muted-foreground" title={issued.url}>{issued.url}</p>
          <div className="flex flex-wrap gap-2">
            <Button onClick={onDownload}><Download className="size-4" /> Download QR (PNG)</Button>
            <Button variant="outline" onClick={() => void copyText(issued.url, "Link copied")}><Copy className="size-4" /> Copy link</Button>
          </div>
        </div>
      </div>
      <ol className="list-decimal space-y-1.5 pl-5 text-sm text-foreground">
        <li>Email the QR image or the link to HR from your own mailbox.</li>
        <li>Give HR the PIN another way: a call or a text message. Never put it in the same email.</li>
        <li>HR opens the link on the computer that will keep the files, enters the PIN and taps Start download.</li>
      </ol>
      <p className="text-sm text-muted-foreground">
        Unused, the link expires {fmtDateTime(issued.grant.unused_expires_at)}. If it expires or the PIN is lost,
        create a new link and PIN for this batch.
      </p>
    </>
  );
}

export function HrLinkSheet({
  batch,
  companyName,
  onClose,
}: {
  batch: Batch | null;
  companyName: string;
  onClose: () => void;
}) {
  const act = useBatchAction();
  const [note, setNote] = useState("");
  const [issued, setIssued] = useState<IssuedGrant | null>(null);
  const canvasRef = useQrCanvas();
  const reissue = !!batch?.grant && !issued;

  const create = async () => {
    if (!batch) return;
    try {
      setIssued(await act.mutateAsync({ kind: "issue", batchId: batch.id, recipientNote: note }) as IssuedGrant);
    } catch (e) {
      toast.error(formatError(e));
    }
  };
  const download = async () => {
    if (!canvasRef.current || !issued || !batch) return;
    try {
      const blob = await composeQrPng(canvasRef.current, {
        title: `${companyName} enrolment forms`,
        lines: [`${batch.file_count} forms ready to download`, `Link expires ${fmtDateTime(issued.grant.unused_expires_at)} if not used`],
        footer: "Enter the PIN your broker gives you separately. You then have 10 minutes to download.",
      });
      triggerDownload(blob, `${companyName} HR download QR.png`);
    } catch (e) {
      toast.error(formatError(e));
    }
  };
  const close = () => { setIssued(null); setNote(""); onClose(); };

  return (
    <Sheet open={!!batch} onOpenChange={open => { if (!open) close(); }}>
      <SheetContent>
        <SheetHeader>
          <SheetTitle>{issued ? "Send to HR" : reissue ? "New link and PIN" : "Create link and PIN"}</SheetTitle>
          <SheetDescription>
            {batch ? `${batch.label} · ${batch.file_count} forms.` : ""} HR opens the link, enters the PIN, then has
            10 minutes to download the ZIP. No HR portal sign-in needed.
          </SheetDescription>
        </SheetHeader>
        <SheetBody className="space-y-5">
          {!issued ? (
            <>
              {reissue && (
                <p className="flex gap-2 rounded-lg bg-warn-soft px-3 py-2 text-sm text-warn">
                  <ShieldAlert className="mt-0.5 size-4 shrink-0" aria-hidden />
                  The current link and PIN for this batch stop working when you create a new one.
                </p>
              )}
              <div className="space-y-1.5">
                <Label htmlFor="hr-recipient">Sending to (for your records)</Label>
                <Input id="hr-recipient" value={note} maxLength={255} onChange={e => setNote(e.target.value)}
                  placeholder="e.g. Mary Tan, HR manager" />
                <p className="text-xs text-muted-foreground">
                  Kept in the audit trail. The link is not tied to an email address.
                </p>
              </div>
            </>
          ) : (
            <IssuedView issued={issued} canvasRef={canvasRef} onDownload={() => void download()} />
          )}
        </SheetBody>
        <SheetFooter>
          {issued ? (
            <Button onClick={close}>Done</Button>
          ) : (
            <>
              <Button variant="ghost" onClick={close}>Cancel</Button>
              <Button onClick={() => void create()} loading={act.isPending}>
                {reissue ? "Create new link and PIN" : "Create link and PIN"}
              </Button>
            </>
          )}
        </SheetFooter>
      </SheetContent>
    </Sheet>
  );
}
