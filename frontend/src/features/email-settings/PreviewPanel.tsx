import { useEffect, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { api } from "@/api/client";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { formatError } from "@/lib/errors";
import { useSession } from "@/stores/session";
import { apiPath, clientOptions, fieldClass, type Content, type Preview, type RecipientPage, type Scope } from "./types";

export function PreviewPanel({ content, scope, canReadHr }: { content: Content; scope: Scope; canReadHr: boolean }) {
  const clientId = useSession(s => s.activeClientId);
  const yearId = useSession(s => s.currentPolicyYearId);
  const [recipient, setRecipient] = useState("");
  const [search, setSearch] = useState("");
  const [mode, setMode] = useState<"desktop" | "mobile" | "text">("desktop");
  const [debounced, setDebounced] = useState(content);
  const [debouncedSearch, setDebouncedSearch] = useState("");
  useEffect(() => { const timer = setTimeout(() => setDebounced(content), 350); return () => clearTimeout(timer); }, [content]);
  useEffect(() => { const timer = setTimeout(() => setDebouncedSearch(search), 250); return () => clearTimeout(timer); }, [search]);
  useEffect(() => { setRecipient(""); setSearch(""); }, [content.audience, clientId, yearId, scope]);
  const canReal = scope === "company" && !!clientId && (content.audience === "hr" ? canReadHr : !!yearId);
  const recipients = useQuery({
    queryKey: ["email-preview-recipients", clientId, yearId, content.audience, content.purpose, debouncedSearch],
    queryFn: () => api.get<RecipientPage>(`/email-templates/recipients?${new URLSearchParams({
      audience: content.audience, purpose: content.purpose, policy_year_id: yearId ?? "", search: debouncedSearch, limit: "100",
    })}`, clientOptions(clientId)), enabled: canReal, gcTime: 0,
  });
  const preview = useQuery({
    queryKey: ["email-preview", clientId, yearId, scope, debounced, recipient],
    queryFn: () => api.post<Preview>(apiPath(scope, "/preview"), {
      content: debounced, recipient_id: recipient || null, policy_year_id: yearId,
    }, clientOptions(clientId)), gcTime: 0, retry: false,
  });
  const stale = debounced !== content || preview.isFetching;
  return <section className="min-w-0 space-y-4" aria-label="Email preview">
    <div className="flex flex-wrap items-center justify-between gap-2">
      <h3 className="font-semibold">Email preview</h3>
      <div className="flex gap-1" aria-label="Preview format">
        {(["desktop", "mobile", "text"] as const).map(value => <Button key={value} size="sm"
          variant={mode === value ? "secondary" : "ghost"} aria-pressed={mode === value} onClick={() => setMode(value)}>
          {value === "text" ? "Plain text" : value[0].toUpperCase() + value.slice(1)}
        </Button>)}
      </div>
    </div>
    {canReal && <div className="space-y-2">
      <Label htmlFor="preview-search">Find a recipient for real-data preview</Label>
      <Input id="preview-search" value={search} onChange={e => setSearch(e.target.value)} placeholder="Search name, staff ID or email" />
      <select className={fieldClass} aria-label="Preview recipient" value={recipient} onChange={e => setRecipient(e.target.value)}>
        <option value="">Sample recipient</option>
        {recipient && !recipients.data?.items.some(row => row.id === recipient) && <option value={recipient}>Selected recipient</option>}
        {recipients.data?.items.map(row => <option value={row.id} key={row.id}>{row.name} — {row.email || "No email"}</option>)}
      </select>
      {recipients.isError && <p role="alert" className="text-sm text-error">{formatError(recipients.error)} <Button size="sm" variant="link" onClick={() => void recipients.refetch()}>Retry recipients</Button></p>}
      {recipients.data?.total === 0 && <p className="text-sm text-muted-foreground">No matching recipients. Sample preview is available.</p>}
      {(recipients.data?.total ?? 0) > 100 && <p className="text-xs text-muted-foreground">Showing the first 100 matches. Search to find a specific person.</p>}
    </div>}
    {!canReal && <p className="text-sm text-muted-foreground">Sample preview. {scope === "firm" ? "Select Company overrides to preview a real recipient." : "Select a benefit year or use an authorized HR account to preview real data."}</p>}
    {preview.isError ? <div role="alert" className="space-y-2 text-sm text-error">{formatError(preview.error)}<Button variant="outline" size="sm" onClick={() => void preview.refetch()}>Retry preview</Button></div> : preview.data ? <>
      <div className="space-y-1 text-sm" aria-live="polite">
        <p className="font-medium">{stale ? "Updating preview…" : preview.data.data_source === "real" ? "Real recipient data · preview only" : "Sample recipient data"}</p>
        {preview.data.recipient_email && <p className="break-all text-muted-foreground">To: {preview.data.recipient_email}</p>}
        <p className="break-words"><span className="text-muted-foreground">Subject: </span>{preview.data.subject || "No subject"}</p>
        {preview.data.preheader && <p className="break-words text-muted-foreground">{preview.data.preheader}</p>}
      </div>
      {Object.keys(preview.data.errors).length > 0 && <div role="alert" className="space-y-1 text-sm text-error"><p>This preview needs attention:</p>
        <ul className="list-disc pl-5">{Object.entries(preview.data.errors).map(([field, error]) => <li key={field}>{field.replaceAll("_", " ")}: {error}</li>)}</ul></div>}
      <div className="rounded-lg border border-border bg-muted/30 p-2" aria-busy={stale}>
        {mode === "text" ? <pre className="min-h-80 whitespace-pre-wrap break-words p-3 font-sans text-sm">{preview.data.text}</pre> :
          <iframe title="Rendered email preview" sandbox="" referrerPolicy="no-referrer" srcDoc={preview.data.html}
            className={`mx-auto block h-[560px] w-full rounded-md bg-background ${mode === "mobile" ? "max-w-[360px]" : ""}`} />}
      </div>
      {preview.data.warnings.length > 0 && <ul className="list-disc space-y-1 pl-5 text-xs text-muted-foreground">
        {preview.data.warnings.map(message => <li key={message}>{message}</li>)}
      </ul>}
      <details className="text-sm"><summary className="cursor-pointer font-medium">Resolved placeholders</summary>
        <dl className="mt-2 grid grid-cols-1 gap-2">{Object.entries(preview.data.values).map(([key, value]) =>
          <div key={key} className="min-w-0"><dt className="text-xs text-muted-foreground">{`{{${key}}}`}</dt><dd className="break-words">{value || "Missing"}</dd></div>)}</dl>
      </details>
    </> : <p role="status" className="text-sm text-muted-foreground">Loading preview…</p>}
  </section>;
}
