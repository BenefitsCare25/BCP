import { Fragment, useRef, useState } from "react";
import { Link, useNavigate, useSearch } from "@tanstack/react-router";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ArrowLeft, ChevronDown, ChevronRight, Download, Plus, Upload } from "lucide-react";
import { useMe } from "@/api/hooks";
import { wicaApi, STATUSES, type WicaEmployee, type WicaIncident, type WicaSettings } from "@/api/wica";
import { useSession } from "@/stores/session";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Choice, Failure, Field, Loading, dateLabel, today } from "@/components/wica/shared";
import { DocumentEditor } from "@/components/wica/DocumentEditor";
import { PackRow } from "@/components/wica/PackRow";
import "@/components/wica/wica.css";

export function WicaPage() {
  const clientId = useSession(s => s.activeClientId);
  const { data: me, isPending } = useMe();
  if (isPending) return <Loading />;
  if (!me || !["broker_admin", "broker_viewer", "system_admin"].includes(me.role)) return <p role="alert">Broker access required.</p>;
  return clientId ? <WicaCompany key={clientId} clientId={clientId} editable={me.role !== "broker_viewer"} /> : <p className="text-sm text-muted-foreground">Select a company.</p>;
}

function WicaCompany({ clientId, editable }: { clientId: string; editable: boolean }) {
  const search = useSearch({ strict: false }) as { incident?: string };
  const navigate = useNavigate();
  const settings = useQuery({ queryKey: ["wica", clientId, "settings"], queryFn: () => wicaApi(clientId).get<WicaSettings>("/settings") });
  const open = (id?: string) => void navigate({ to: "/claims/wica", search: { incident: id } });
  if (settings.isPending) return <Loading />;
  if (settings.isError) return <Failure error={settings.error} retry={() => void settings.refetch()} />;
  return search.incident ? <IncidentLoader key={search.incident} clientId={clientId} id={search.incident} editable={editable} enabled={settings.data.enabled} back={() => open()} /> : <IncidentList clientId={clientId} editable={editable} settings={settings.data} open={open} />;
}

function IncidentList({ clientId, editable, settings, open }: { clientId: string; editable: boolean; settings: WicaSettings; open: (id: string) => void }) {
  const [q, setQ] = useState("");
  const [period, setPeriod] = useState("");
  const [offset, setOffset] = useState(0);
  const [adding, setAdding] = useState(false);
  const query = useQuery({ queryKey: ["wica", clientId, "incidents", q, period, offset], queryFn: () => wicaApi(clientId).get<{ items: WicaIncident[]; total: number }>(`/incidents?${new URLSearchParams({ q, ...(period ? { period_id: period } : {}), offset: String(offset) })}`) });
  return <div className="space-y-4">
    <div className="flex flex-wrap items-center gap-3">
      <Input aria-label="Search incidents" placeholder="Search employee, staff ID or I-Report" value={q} onChange={e => { setQ(e.target.value); setOffset(0); }} className="sm:max-w-80" />
      <div className="w-full sm:w-56"><Choice aria-label="WICA period" value={period} onChange={e => { setPeriod(e.target.value); setOffset(0); }}><option value="">All WICA periods</option>{settings.periods.map(p => <option key={p.id} value={p.id}>{p.label}</option>)}</Choice></div>
      {editable && settings.enabled && <Button className="sm:ml-auto" onClick={() => setAdding(v => !v)} aria-expanded={adding}><Plus className="size-4" />New incident</Button>}
    </div>
    {!settings.enabled && <div className="flex flex-wrap items-center gap-3 rounded-lg border border-border bg-card p-4 text-sm"><span>WICA is not enabled for this company.</span>{editable && <Button variant="link" asChild><Link to="/settings/company" search={{ tab: "wica" }}>Open settings</Link></Button>}</div>}
    {adding && <NewIncident clientId={clientId} settings={settings} cancel={() => setAdding(false)} open={open} />}
    {query.isPending ? <Loading /> : query.isError ? <Failure error={query.error} retry={() => void query.refetch()} /> : <>
      <div className="wica-incidents overflow-x-auto rounded-xl border border-border bg-card">
        <table className="w-full text-left text-sm"><thead className="border-b border-border bg-muted/40 text-muted-foreground"><tr>{["Employee", "Incident date", "I-Report", "Period", ""].map((label, i) => <th key={i} scope="col" className="px-4 py-3 font-medium">{label}</th>)}</tr></thead>
          <tbody className="divide-y divide-border">{query.data.items.map(row => <tr key={row.id} className="hover:bg-muted/30"><td className="px-4 py-4"><button className="text-left font-medium text-primary underline-offset-4 hover:underline focus-visible:outline focus-visible:outline-2 focus-visible:outline-ring" onClick={() => open(row.id)}>{row.employee_name}</button><div className="mt-1 text-xs text-muted-foreground">{row.staff_id}</div></td><td className="whitespace-nowrap px-4 py-4">{dateLabel(row.incident_date)}</td><td className="px-4 py-4">{row.report_number || "—"}</td><td className="px-4 py-4">{settings.periods.find(p => p.id === row.period_id)?.label || "—"}</td><td className="px-4 py-4"><Button variant="ghost" size="icon" aria-label={`Open incident for ${row.employee_name}`} onClick={() => open(row.id)}><ChevronRight className="size-4" /></Button></td></tr>)}
          {!query.data.items.length && <tr><td colSpan={5} className="px-4 py-12 text-center text-muted-foreground">{q || period ? "No matching incidents." : "No incidents recorded."}</td></tr>}</tbody></table>
      </div>
      {query.data.total > 25 && <div className="flex items-center justify-end gap-3 text-sm"><span>{offset + 1}–{Math.min(offset + 25, query.data.total)} of {query.data.total}</span><Button variant="outline" disabled={!offset} onClick={() => setOffset(offset - 25)}>Previous</Button><Button variant="outline" disabled={offset + 25 >= query.data.total} onClick={() => setOffset(offset + 25)}>Next</Button></div>}
    </>}
  </div>;
}

function NewIncident({ clientId, settings, cancel, open }: { clientId: string; settings: WicaSettings; cancel: () => void; open: (id: string) => void }) {
  const queryClient = useQueryClient();
  const [id] = useState(() => crypto.randomUUID());
  const [search, setSearch] = useState("");
  const [manual, setManual] = useState(false);
  const [employee, setEmployee] = useState<WicaEmployee | null>(null);
  const [period, setPeriod] = useState(settings.periods.find(p => p.start_date <= today() && p.end_date >= today())?.id ?? settings.periods[0]?.id ?? "");
  const employees = useQuery({ queryKey: ["wica", clientId, "employees", search], queryFn: () => wicaApi(clientId).get<WicaEmployee[]>(`/employees?q=${encodeURIComponent(search)}`), enabled: search.trim().length >= 2 && !manual && !employee });
  const mutation = useMutation({ mutationFn: (body: unknown) => wicaApi(clientId).post<WicaIncident>("/incidents", body), onSuccess: row => {
    void queryClient.invalidateQueries({ queryKey: ["wica", clientId, "incidents"] });
    open(row.id);
  } });
  return <form aria-label="New incident" className="space-y-4 rounded-xl border border-border bg-card p-4 sm:p-5" onSubmit={e => {
    e.preventDefault(); const data = new FormData(e.currentTarget);
    mutation.mutate({ id, period_id: period, employee_id: manual ? null : employee?.id,
      employee_name: manual ? data.get("employee_name") : employee?.employee_name, staff_id: manual ? data.get("staff_id") : employee?.staff_id,
      incident_date: data.get("incident_date"), report_number: data.get("report_number"), remarks: data.get("remarks") });
  }}>
    <fieldset disabled={mutation.isPending} className="space-y-4">
      <div className="grid gap-4 md:grid-cols-2"><Field label="WICA period"><Choice value={period} onChange={e => setPeriod(e.target.value)} required>{settings.periods.map(p => <option key={p.id} value={p.id}>{p.label}</option>)}</Choice></Field><Field label="Incident date"><Input name="incident_date" type="date" max={today()} required /></Field></div>
      <label className="flex items-center gap-2 text-sm"><input type="checkbox" className="size-4 accent-primary" checked={manual} onChange={e => { setManual(e.target.checked); setEmployee(null); }} />Employee not in member listing</label>
      {manual ? <div className="grid gap-4 md:grid-cols-2"><Field label="Employee name"><Input name="employee_name" maxLength={255} required /></Field><Field label="Staff ID"><Input name="staff_id" maxLength={128} required /></Field></div> : <div className="space-y-2"><Field label="Employee"><Input value={employee ? `${employee.employee_name} · ${employee.staff_id}` : search} placeholder="Search name or staff ID" onChange={e => { setEmployee(null); setSearch(e.target.value); }} autoComplete="off" /></Field>
        {!employee && search.length >= 2 && <div className="max-h-48 overflow-auto rounded-md border border-border">{employees.isPending ? <p role="status" className="p-3 text-sm">Searching…</p> : employees.isError ? <Failure error={employees.error} /> : employees.data?.length ? employees.data.map(emp => <button type="button" key={emp.id} className="block w-full px-3 py-2 text-left text-sm hover:bg-muted focus-visible:bg-muted focus-visible:outline focus-visible:outline-ring" onClick={() => setEmployee(emp)}>{emp.employee_name} · {emp.staff_id}</button>) : <p className="p-3 text-sm text-muted-foreground">No matching employees.</p>}</div>}
      </div>}
      <div className="grid gap-4 md:grid-cols-2"><Field label="MOM I-Report number (optional)"><Input name="report_number" maxLength={128} /></Field><Field label="Broker remarks (optional)"><Input name="remarks" maxLength={4000} /></Field></div>
      <div className="flex justify-end gap-2"><Button type="button" variant="outline" onClick={cancel}>Cancel</Button><Button loading={mutation.isPending} disabled={!period || (!manual && !employee)}>Create incident</Button></div>
    </fieldset>{mutation.isError && <Failure error={mutation.error} />}
  </form>;
}

function IncidentLoader({ clientId, id, editable, enabled, back }: { clientId: string; id: string; editable: boolean; enabled: boolean; back: () => void }) {
  const query = useQuery({ queryKey: ["wica", clientId, "incident", id], queryFn: () => wicaApi(clientId).get<WicaIncident>(`/incidents/${id}`) });
  if (query.isPending) return <Loading />;
  if (query.isError) return <div className="space-y-4"><Button variant="ghost" onClick={back}><ArrowLeft className="size-4" />Back</Button><Failure error={query.error} retry={() => void query.refetch()} /></div>;
  return <IncidentWorkspace clientId={clientId} row={query.data} editable={editable} enabled={enabled} back={back} />;
}

function IncidentWorkspace({ clientId, row, editable, enabled, back }: { clientId: string; row: WicaIncident; editable: boolean; enabled: boolean; back: () => void }) {
  const qc = useQueryClient();
  const client = wicaApi(clientId);
  const [expanded, setExpanded] = useState<string | null>(null);
  const [selected, setSelected] = useState<string[]>([]);
  const [packsOpen, setPacksOpen] = useState(false);
  const [progress, setProgress] = useState("");
  const fileInput = useRef<HTMLInputElement>(null);
  const path = `/incidents/${row.id}`;
  const cache = (data: WicaIncident) => qc.setQueryData(["wica", clientId, "incident", row.id], data);
  const mutation = useMutation({ mutationFn: (task: () => Promise<WicaIncident>) => task(), onSuccess: data => { cache(data); void qc.invalidateQueries({ queryKey: ["wica", clientId, "incidents"] }); }, onError: () => { void qc.invalidateQueries({ queryKey: ["wica", clientId, "incident", row.id] }); } });
  const download = useMutation({ mutationFn: ({ url, name }: { url: string; name: string }) => client.download(url, name) });
  const run = (task: () => Promise<WicaIncident>) => mutation.mutate(task);
  const busy = mutation.isPending;
  const eligible = row.documents.filter(d => d.status === "pending_insurer" || d.status === "supporting");
  const prepare = (ids: string[]) => run(async () => {
    const data = await client.post<WicaIncident>(`${path}/packs`, { id: crypto.randomUUID(), revision: row.revision, document_ids: ids });
    setPacksOpen(true); setSelected([]); return data;
  });
  return <div className="space-y-4">
    <div className="flex flex-wrap items-center gap-x-5 gap-y-2 border-b border-border pb-4">
      <Button variant="ghost" size="sm" onClick={back}><ArrowLeft className="size-4" />Back</Button>
      <span className="font-medium">{row.employee_name}<span className="ml-2 text-sm font-normal text-muted-foreground">{row.staff_id}</span></span>
      <span className="text-sm">{dateLabel(row.incident_date)}</span>
      {row.report_number && <span className="text-sm text-muted-foreground">I-Report {row.report_number}</span>}
    </div>
    {row.remarks && <p className="text-sm text-muted-foreground">{row.remarks}</p>}
    <div className="flex flex-wrap items-center gap-2">
      {editable && enabled && <><input ref={fileInput} type="file" multiple accept=".pdf,.png,.jpg,.jpeg" className="hidden" aria-label="Upload documents" onChange={e => {
        const files = Array.from(e.target.files ?? []); e.target.value = "";
        if (!files.length) return;
        run(async () => { let current = row; try {
          if (files.some(f => f.size > 15 * 1024 * 1024)) throw new Error("Each file must be 15 MB or smaller.");
          if (files.length + row.documents.length > 100) throw new Error("An incident can hold up to 100 documents.");
          for (const [i, file] of files.entries()) { setProgress(`Uploading ${i + 1} of ${files.length}: ${file.name}`); const form = new FormData(); form.append("file", file); form.append("revision", String(current.revision)); form.append("document_id", crypto.randomUUID()); current = await client.upload(`${path}/documents`, form); cache(current); }
          return current;
        } finally { setProgress(""); } });
      }} /><Button disabled={busy} onClick={() => fileInput.current?.click()}><Upload className="size-4" />Upload documents</Button></>}
      {editable && <><Button variant="outline" disabled={busy || !selected.some(id => row.documents.some(d => d.id === id && d.status === "pending_insurer"))} onClick={() => prepare(selected)}>Prepare selected{selected.length ? ` (${selected.length})` : ""}</Button><Button variant="ghost" disabled={busy || !eligible.some(d => d.claim_id)} onClick={() => prepare(eligible.map(d => d.id))}>Prepare all pending</Button></>}
      <span className="text-xs text-muted-foreground sm:ml-auto">PDF, PNG, JPG · 15 MB per file</span>
    </div>
    {progress && <p role="status" className="text-sm text-muted-foreground">{progress}</p>}
    {mutation.isError && <Failure error={mutation.error} />}{download.isError && <Failure error={download.error} />}
    <div className="wica-documents overflow-x-auto rounded-xl border border-border bg-card">
      <table className="w-full text-left text-sm"><thead className="border-b border-border bg-muted/40 text-muted-foreground"><tr><th scope="col" className="w-10 px-3 py-3"><span className="sr-only">Select</span></th>{["Document", "Benefit", "Document date", "Status", ""].map((label, i) => <th key={i} scope="col" className="whitespace-nowrap px-3 py-3 font-medium">{label}</th>)}</tr></thead>
        <tbody className="divide-y divide-border">{row.documents.map(doc => <Fragment key={doc.id}><tr className={expanded === doc.id ? "bg-muted/40" : "hover:bg-muted/20"}>
          <td className="px-3 py-4">{editable && <input type="checkbox" aria-label={`Select ${doc.file_name}`} className="size-4 accent-primary" disabled={busy || !eligible.some(d => d.id === doc.id)} checked={selected.includes(doc.id)} onChange={e => setSelected(e.target.checked ? [...selected, doc.id] : selected.filter(id => id !== doc.id))} />}</td>
          <td className="max-w-80 px-3 py-4"><button className="flex max-w-full items-center gap-2 text-left font-medium hover:text-primary focus-visible:outline focus-visible:outline-2 focus-visible:outline-ring" aria-expanded={expanded === doc.id} onClick={() => setExpanded(expanded === doc.id ? null : doc.id)}>{expanded === doc.id ? <ChevronDown className="size-4 shrink-0" /> : <ChevronRight className="size-4 shrink-0" />}<span className="break-words">{doc.file_name}</span></button><div className="mt-1 pl-6 text-xs text-muted-foreground">{doc.doc_type || "Tag document"}<span className="mt-1 block md:hidden">{STATUSES[doc.status]}</span></div></td>
          <td className="px-3 py-4">{doc.benefit_type || "—"}</td><td className="whitespace-nowrap px-3 py-4">{doc.document_date ? dateLabel(doc.document_date) : "—"}</td><td className="px-3 py-4"><span className={doc.status === "settled" ? "text-success" : doc.status === "rejected" ? "text-error" : doc.status === "pending_insurer" ? "text-info" : "text-muted-foreground"}>{STATUSES[doc.status]}</span></td><td className="px-3 py-4"><Button variant="ghost" size="icon" aria-label={`Download ${doc.file_name}`} disabled={download.isPending} onClick={() => download.mutate({ url: `${path}/documents/${doc.id}/download`, name: doc.file_name })}><Download className="size-4" /></Button></td>
        </tr>{expanded === doc.id && <tr><td colSpan={6} className="bg-muted/20 p-4 sm:p-5"><DocumentEditor key={`${doc.id}-${row.revision}`} doc={doc} row={row} editable={editable} busy={busy} save={body => run(() => client.patch<WicaIncident>(`${path}/documents/${doc.id}`, { revision: row.revision, ...body }))} action={body => run(() => client.post<WicaIncident>(`${path}/documents/${doc.id}/status`, { revision: row.revision, ...body }))} remove={() => run(() => client.post<WicaIncident>(`${path}/documents/${doc.id}/remove`, { revision: row.revision }))} /></td></tr>}</Fragment>)}
          {!row.documents.length && <tr><td colSpan={6} className="px-4 py-12 text-center text-muted-foreground">No documents uploaded.</td></tr>}</tbody></table>
    </div>
    {row.packs.length > 0 && <div className="rounded-xl border border-border bg-card"><button className="flex w-full items-center gap-2 px-4 py-3 text-left text-sm font-medium hover:bg-muted/30 focus-visible:outline focus-visible:outline-2 focus-visible:outline-ring" aria-expanded={packsOpen} onClick={() => setPacksOpen(v => !v)}>{packsOpen ? <ChevronDown className="size-4" /> : <ChevronRight className="size-4" />}Submission packs ({row.packs.length})</button>{packsOpen && <div className="divide-y divide-border border-t border-border">{row.packs.map(pack => <PackRow key={pack.id} pack={pack} editable={editable} busy={busy} downloadBusy={download.isPending} download={() => download.mutate({ url: `${path}/packs/${pack.id}/download`, name: `WICA-${pack.id}.zip` })} sent={body => run(() => client.post<WicaIncident>(`${path}/packs/${pack.id}/sent`, { revision: row.revision, ...body }))} />)}</div>}</div>}
  </div>;
}
