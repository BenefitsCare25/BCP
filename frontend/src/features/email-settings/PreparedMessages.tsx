import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { ArrowLeft } from "lucide-react";
import { api } from "@/api/client";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { formatError } from "@/lib/errors";
import { useSession } from "@/stores/session";
import { clientOptions, type Content } from "./types";

interface Preparation { id: string; title: string; recipient_count: number; status: string; created_at: string }
interface Detail { title: string; content: Content; recipients: { id: string; email: string }[]; preview: { subject: string; html: string; text: string } }

export function PreparedMessages() {
  const clientId = useSession(s => s.activeClientId);
  const [selected, setSelected] = useState<string | null>(null);
  const query = useQuery({ queryKey: ["email-preparations", clientId],
    queryFn: () => api.get<Preparation[]>("/email-templates/preparations", clientOptions(clientId)) });
  const detail = useQuery({ queryKey: ["email-preparation-detail", clientId, selected],
    queryFn: () => api.get<Detail>(`/email-templates/preparations/${selected}`, clientOptions(clientId)), enabled: !!selected, gcTime: 0, retry: false });
  if (selected) return <section className="space-y-4">
    <Button size="sm" variant="ghost" onClick={() => setSelected(null)}><ArrowLeft className="size-4" />All preparations</Button>
    {detail.isError ? <p role="alert" className="text-sm text-error">{formatError(detail.error)}</p> : !detail.data ? <p role="status">Loading preparation…</p> : <>
      <h3 className="text-lg font-semibold">{detail.data.title}</h3>
      <p className="text-sm text-muted-foreground">Saved content and recipient addresses. Nothing has been sent. Recipient eligibility must be checked again before a future send.</p>
      <div className="grid min-w-0 gap-6 xl:grid-cols-2">
        <div className="space-y-3"><h4 className="font-medium">{detail.data.recipients.length} saved recipients</h4>
          <ul className="max-h-[560px] space-y-2 overflow-auto rounded-md border border-border p-4 text-sm">{detail.data.recipients.map(row => <li className="break-all" key={row.id}>{row.email}</li>)}</ul>
        </div>
        <div className="min-w-0 space-y-3"><h4 className="font-medium">Saved message · sample recipient</h4><p className="break-words text-sm">Subject: {detail.data.preview.subject}</p>
          <iframe title="Saved email preview" sandbox="" referrerPolicy="no-referrer" srcDoc={detail.data.preview.html} className="h-[560px] w-full rounded-md border border-border bg-background" />
          <details className="text-sm"><summary className="cursor-pointer">Plain text</summary><pre className="mt-3 whitespace-pre-wrap break-words font-sans">{detail.data.preview.text}</pre></details>
        </div>
      </div>
    </>}
  </section>;
  return <section className="space-y-4"><h3 className="text-lg font-semibold">Prepared messages</h3>
    <p className="text-sm text-muted-foreground">Saved for review only. These messages are not queued and will not send automatically when delivery is configured.</p>
    {query.isError ? <p role="alert" className="text-sm text-error">{formatError(query.error)} <Button variant="link" onClick={() => void query.refetch()}>Retry</Button></p> : !query.data ?
      <p role="status" className="text-sm text-muted-foreground">Loading preparations…</p> : query.data.length === 0 ? <p className="py-8 text-sm text-muted-foreground">No prepared messages. Choose Send email on a published template to select recipients.</p> : <>
        <div className="overflow-x-auto rounded-lg border border-border"><table className="w-full text-left text-sm"><thead className="bg-muted/50 text-muted-foreground"><tr><th className="p-3">Template</th><th className="p-3">Recipients</th><th className="p-3">Saved</th><th className="p-3">Status</th></tr></thead>
          <tbody>{query.data.map(row => <tr key={row.id} className="border-t border-border"><td className="p-3"><button className="text-left font-medium text-primary hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring" onClick={() => setSelected(row.id)}>{row.title}</button></td><td className="p-3 tabular-nums">{row.recipient_count}</td><td className="p-3">{new Date(row.created_at).toLocaleString()}</td><td className="p-3"><Badge variant="warn">Prepared · unsent</Badge></td></tr>)}</tbody></table></div>
        <p className="text-xs text-muted-foreground">Showing the latest 100 preparations for this company.</p>
      </>}
  </section>;
}
