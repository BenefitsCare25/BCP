import { useCallback, useEffect, useState } from "react";
import { useBlocker } from "@tanstack/react-router";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Copy, Mail, Plus, Trash2 } from "lucide-react";
import { api } from "@/api/client";
import { useMe } from "@/api/hooks";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { AlertDialog } from "@/components/ui/alert-dialog";
import { Badge } from "@/components/ui/badge";
import { formatError } from "@/lib/errors";
import { useSession } from "@/stores/session";
import { useSetupNavigation } from "@/stores/setupNavigation";
import { TemplateEditor } from "./TemplateEditor";
import { BrandingEditor } from "./BrandingEditor";
import { SendFlow } from "./SendFlow";
import { PreparedMessages } from "./PreparedMessages";
import { apiPath, clientOptions, fieldClass, PURPOSE_LABELS, SOURCE_LABELS, type Catalog, type Content, type Scope, type Template } from "./types";

export function EmailSettings() {
  const clientId = useSession(s => s.activeClientId);
  const yearId = useSession(s => s.currentPolicyYearId);
  return clientId ? <EmailWorkspace key={`${clientId}-${yearId}`} /> : <p className="text-sm text-muted-foreground">Select a company to configure email.</p>;
}
function EmailWorkspace() {
  const clientId = useSession(s => s.activeClientId);
  const { data: me } = useMe();
  const editable = me?.role === "broker_admin" || me?.role === "system_admin";
  const [scope, setScope] = useState<Scope>("company");
  const [tab, setTab] = useState<"templates" | "branding" | "prepared">("templates");
  const [selected, setSelected] = useState<string | null>(null);
  const [copyContent, setCopyContent] = useState<Content | undefined>();
  const [sendKey, setSendKey] = useState<string | null>(null);
  const [filter, setFilter] = useState("");
  const [dirty, setDirty] = useState(false);
  const [pending, setPending] = useState<(() => void) | null>(null);
  const [remove, setRemove] = useState<Template | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const qc = useQueryClient();
  const onDirty = useCallback((value: boolean) => setDirty(value), []);
  const guard = useCallback((action: () => void) => { if (dirty) setPending(() => action); else action(); }, [dirty]);
  useEffect(() => { useSetupNavigation.getState().setGuard(guard); return () => useSetupNavigation.getState().setGuard(null); }, [guard]);
  const blocker = useBlocker({ shouldBlockFn: () => dirty, enableBeforeUnload: () => dirty, disabled: !dirty, withResolver: true });
  const query = useQuery({ queryKey: ["email-templates", clientId, scope], queryFn: () => api.get<Catalog>(apiPath(scope), clientOptions(clientId)), retry: false });
  const row = query.data?.items.find(item => item.key === selected);
  const sending = query.data?.items.find(item => item.key === sendKey);
  const back = () => guard(() => { setSelected(null); setSendKey(null); setCopyContent(undefined); setDirty(false); });
  const resetView = () => { setSelected(null); setSendKey(null); setCopyContent(undefined); setDirty(false); };
  const refreshInheritedCatalogs = () => qc.invalidateQueries({
    queryKey: ["email-templates"],
    predicate: query => query.queryKey[2] === "company",
    refetchType: "all",
  });
  const saved = (item: Template) => {
    qc.setQueryData<Catalog>(["email-templates", clientId, scope], previous => previous ? ({ ...previous,
      items: previous.items.some(row => row.key === item.key) ? previous.items.map(row => row.key === item.key ? item : row) : [...previous.items, item] }) : previous);
    setSelected(item.key); setCopyContent(undefined); setDirty(false);
    void qc.invalidateQueries({ queryKey: ["email-versions"] });
    if (scope === "firm") void refreshInheritedCatalogs();
  };
  const confirmRemove = async () => {
    if (!remove) return;
    setBusy(true); setError("");
    try {
      await api.delete(apiPath(scope, `/${remove.key}`), clientOptions(clientId));
      setRemove(null);
      if (scope === "firm") await refreshInheritedCatalogs();
      await query.refetch();
    }
    catch (caught) { setError(formatError(caught)); }
    finally { setBusy(false); }
  };
  return <div className="min-w-0 space-y-5">
    <div className="flex flex-wrap items-center justify-between gap-3">
      <div><h2 className="text-xl font-semibold">Email</h2><p className="text-sm text-muted-foreground">Templates, branding and recipient preparation.</p></div>
      <div className="flex items-center gap-2"><label htmlFor="email-scope" className="text-sm">Configure</label>
        <select id="email-scope" className={`${fieldClass} w-auto`} value={scope} onChange={e => { const next = e.target.value as Scope; guard(() => { setScope(next); resetView(); }); }}>
          <option value="company">Company overrides</option><option value="firm">Broker defaults</option>
        </select></div>
    </div>
    {scope === "firm" && <p className="rounded-md bg-warn-soft p-3 text-sm text-warn">Broker defaults apply to all companies in this firm's template library. Existing company overrides are preserved.</p>}
    <div className="flex flex-wrap gap-1 border-b border-border pb-2" aria-label="Email settings sections">
      {(["templates", "branding", "prepared"] as const).map(value => <Button key={value} size="sm" variant={tab === value ? "secondary" : "ghost"} aria-pressed={tab === value}
        onClick={() => guard(() => { setTab(value); resetView(); })}>{value === "templates" ? "Templates" : value === "branding" ? "Sender & branding" : "Prepared messages"}</Button>)}
    </div>
    {error && <p role="alert" className="text-sm text-error">{error}</p>}
    {tab === "branding" ? <BrandingEditor key={scope} scope={scope} editable={editable} onDirty={onDirty} /> : tab === "prepared" ? <PreparedMessages /> :
      query.isError ? <p role="alert" className="text-sm text-error">{formatError(query.error)} <Button size="sm" variant="link" onClick={() => void query.refetch()}>Retry</Button></p> : !query.data || (query.isFetching && query.isStale && !selected && !sendKey) ?
        <p role="status" className="text-sm text-muted-foreground">Loading email templates…</p> : sending?.published_content ?
          <SendFlow key={`${clientId}-${sending.key}`} item={sending} onBack={back} onDirty={onDirty} onPrepared={() => { void qc.invalidateQueries({ queryKey: ["email-preparations"] }); }} /> : selected ?
          <TemplateEditor key={`${scope}-${selected}`} item={row} initial={copyContent} scope={scope} fields={query.data.placeholders} editable={editable}
            systemAdmin={me?.role === "system_admin"} onBack={back} onSaved={saved} onDirty={onDirty} /> : <>
          <div className="flex flex-wrap gap-3"><Input className="max-w-sm" aria-label="Search email templates" placeholder="Search template title or subject" value={filter} onChange={e => setFilter(e.target.value)} />
            {editable && <Button onClick={() => { setCopyContent(undefined); setSelected("new"); }}><Plus className="size-4" />New template</Button>}</div>
          <p className="text-sm text-muted-foreground">Email delivery is not configured. Editing, real-data previews and recipient preparation are available.</p>
          <div className="overflow-x-auto rounded-lg border border-border"><table className="w-full text-left text-sm"><thead className="bg-muted/50 text-muted-foreground"><tr>
            <th className="p-3">Template</th><th className="p-3">Audience</th><th className="p-3">Source</th><th className="p-3">Status</th><th className="p-3"><span className="sr-only">Actions</span></th>
          </tr></thead><tbody>{query.data.items.filter(item => `${item.content.title} ${item.content.subject}`.toLowerCase().includes(filter.toLowerCase())).map(item =>
            <tr key={item.key} className="border-t border-border align-top hover:bg-muted/20">
              <td className="p-3"><button className="text-left font-medium text-primary underline-offset-4 hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring" onClick={() => setSelected(item.key)}>{item.content.title}</button>
                <p className="mt-1 text-xs text-muted-foreground">{PURPOSE_LABELS[item.content.purpose]}</p></td>
              <td className="p-3">{item.content.audience === "employee" ? "Employees" : "HR users"}</td><td className="p-3 whitespace-nowrap">{SOURCE_LABELS[item.source]}</td>
              <td className="p-3"><Badge variant={item.has_changes ? "warn" : "good"}>{item.has_changes ? item.published_content ? "Draft changes" : "Draft" : "Published"}</Badge></td>
              <td className="p-3"><div className="flex flex-wrap justify-end gap-1">
                {editable && <><Button size="sm" variant="outline" disabled={!item.published_content || scope === "firm"} title={scope === "firm" ? "Select Company overrides to choose recipients" : undefined}
                  onClick={() => setSendKey(item.key)}><Mail className="size-4" />Send email</Button>
                  <Button size="sm" variant="ghost" aria-label={`Duplicate ${item.content.title}`} onClick={() => { setCopyContent({ ...item.content, title: `${item.content.title.slice(0, 113)} (copy)` }); setSelected(`new-${item.key}`); }}><Copy className="size-4" /></Button></>}
                {me?.role === "system_admin" && item.has_local_draft && <Button size="sm" variant="ghost" aria-label={`Remove ${item.content.title}`} onClick={() => setRemove(item)}><Trash2 className="size-4" /></Button>}
              </div></td>
            </tr>)}</tbody></table></div>
          {!query.data.items.some(item => `${item.content.title} ${item.content.subject}`.toLowerCase().includes(filter.toLowerCase())) && <p className="text-sm text-muted-foreground">No templates match your search.</p>}
        </>}
    <AlertDialog open={!!pending || blocker.status === "blocked"} onOpenChange={open => { if (!open) { setPending(null); if (blocker.status === "blocked") blocker.reset(); } }}
      title="Leave unsaved work?" description="Your unsaved template changes or recipient selection will be lost." confirmLabel="Leave without saving" onConfirm={() => {
        setDirty(false); const action = pending; setPending(null); if (blocker.status === "blocked") blocker.proceed(); else action?.();
      }} />
    <AlertDialog open={!!remove} onOpenChange={open => { if (!open) setRemove(null); }} title="Remove saved template?"
      description="Any inherited broker or Inspro template will become effective again. Saved preparations keep their content snapshot. No email is sent."
      loading={busy} onConfirm={confirmRemove} confirmLabel="Remove template" />
  </div>;
}
