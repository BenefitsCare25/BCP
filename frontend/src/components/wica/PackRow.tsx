import { useState } from "react";
import { Download } from "lucide-react";
import type { WicaPack } from "@/api/wica";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Field, dateLabel, today } from "./shared";

export function PackRow({ pack, editable, busy, downloadBusy, download, sent }: { pack: WicaPack; editable: boolean; busy: boolean; downloadBusy: boolean; download: () => void; sent: (body: Record<string, unknown>) => void }) {
  const [recording, setRecording] = useState(false);
  return <div className="space-y-3 p-4"><div className="flex flex-wrap items-center gap-3 text-sm"><span className="font-medium">{pack.document_count} files</span><span className="text-muted-foreground">{dateLabel(pack.created_at)}</span><span className="break-all text-xs text-muted-foreground">{pack.id.slice(0, 8).toUpperCase()}</span><span className={pack.sent_on ? "text-success" : "text-muted-foreground"}>{pack.sent_on ? `Sent ${dateLabel(pack.sent_on)} · ${pack.sent_reference}` : "Not recorded as sent"}</span><div className="flex gap-2 sm:ml-auto"><Button variant="outline" disabled={downloadBusy} onClick={download}><Download className="size-4" />Download pack</Button>{editable && !pack.sent_on && <Button variant="ghost" disabled={busy} onClick={() => setRecording(v => !v)}>Record sent</Button>}</div></div>
    {recording && !pack.sent_on && <form aria-label="Record external submission" className="grid items-end gap-3 sm:grid-cols-2 lg:grid-cols-[1fr_2fr_auto]" onSubmit={e => { e.preventDefault(); const data = new FormData(e.currentTarget); sent({ sent_on: data.get("sent_on"), sent_reference: data.get("sent_reference") }); }}>
      <Field label="Sent date"><Input name="sent_on" type="date" max={today()} required disabled={busy} /></Field><Field label="Recipient / submission reference"><Input name="sent_reference" maxLength={255} required disabled={busy} /></Field><div className="flex gap-2"><Button loading={busy}>Save sent record</Button><Button type="button" variant="ghost" disabled={busy} onClick={() => setRecording(false)}>Cancel</Button></div>
    </form>}
  </div>;
}
