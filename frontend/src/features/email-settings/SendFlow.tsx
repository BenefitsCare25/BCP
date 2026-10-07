import { useEffect, useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { ArrowLeft, ChevronLeft, ChevronRight } from "lucide-react";
import { api } from "@/api/client";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { formatError } from "@/lib/errors";
import { useSession } from "@/stores/session";
import { clientOptions, fieldClass, type RecipientPage, type Review, type Template } from "./types";
import { PreviewPanel } from "./PreviewPanel";

export function SendFlow({ item, onBack, onPrepared, onDirty }: {
  item: Template; onBack: () => void; onPrepared: () => void; onDirty: (value: boolean) => void;
}) {
  const content = item.published_content!;
  const clientId = useSession(s => s.activeClientId);
  const yearId = useSession(s => s.currentPolicyYearId);
  const [search, setSearch] = useState("");
  const [query, setQuery] = useState("");
  const [status, setStatus] = useState("");
  const [offset, setOffset] = useState(0);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [review, setReview] = useState<Review | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [prepared, setPrepared] = useState(false);
  const [requestKey, setRequestKey] = useState(() => crypto.randomUUID());
  useEffect(() => { const timer = setTimeout(() => { setQuery(search); setOffset(0); }, 250); return () => clearTimeout(timer); }, [search]);
  useEffect(() => { onDirty(busy || (!prepared && selected.size > 0)); return () => onDirty(false); }, [busy, selected.size, prepared, onDirty]);
  const canQuery = content.audience === "hr" || !!yearId;
  const recipients = useQuery({ queryKey: ["email-recipients", clientId, yearId, content.audience, content.purpose, query, status, offset],
    queryFn: () => api.get<RecipientPage>(`/email-templates/recipients?${new URLSearchParams({
      audience: content.audience, purpose: content.purpose, policy_year_id: yearId ?? "", search: query,
      account_status: status, offset: String(offset), limit: "50",
    })}`, clientOptions(clientId)), enabled: canQuery, gcTime: 0, retry: false });
  const eligibleOnPage = useMemo(() => recipients.data?.items.filter(row => row.eligible) ?? [], [recipients.data]);
  const changeSelection = (value: Set<string>) => { setSelected(value); setReview(null); setPrepared(false); setRequestKey(crypto.randomUUID()); setError(""); };
  const toggle = (id: string) => { const value = new Set(selected); if (value.has(id)) value.delete(id); else value.add(id); changeSelection(value); };
  const selectionBody = { template_key: item.key, policy_year_id: yearId, recipient_ids: [...selected].sort() };
  const reviewRecipients = async () => {
    setBusy(true); setError("");
    try { setReview(await api.post<Review>("/email-templates/review", selectionBody, clientOptions(clientId))); }
    catch (caught) { setError(formatError(caught)); }
    finally { setBusy(false); }
  };
  const prepare = async () => {
    if (!review) return;
    setBusy(true); setError("");
    try {
      await api.post("/email-templates/prepare", { ...selectionBody, request_key: requestKey, review_token: review.review_token }, clientOptions(clientId));
      setPrepared(true); onPrepared();
    } catch (caught) { setError(formatError(caught)); }
    finally { setBusy(false); }
  };
  return <section className="space-y-5" aria-label="Select email recipients">
    <Button variant="ghost" size="sm" onClick={onBack}><ArrowLeft className="size-4" />Templates</Button>
    <div><h3 className="text-lg font-semibold">{content.title}</h3><p className="text-sm text-muted-foreground">Choose recipients for the published template. Each person receives a separate email.</p></div>
    <div className="flex flex-wrap items-end gap-3">
      <div className="min-w-0 flex-1 space-y-1.5"><Label htmlFor="email-recipient-search">Find {content.audience === "hr" ? "HR users" : "employees"}</Label>
        <Input id="email-recipient-search" placeholder="Name, staff ID or email" value={search} onChange={e => setSearch(e.target.value)} /></div>
      <div className="space-y-1.5"><Label htmlFor="email-recipient-status">Portal status</Label><select id="email-recipient-status" className={fieldClass} value={status}
        onChange={e => { setStatus(e.target.value); setOffset(0); }}>
        <option value="">All statuses</option><option value="not_invited">Not invited</option><option value="invited">Invited</option><option value="activated">Activated</option><option value="disabled">Disabled</option>
      </select></div>
    </div>
    {!canQuery && <p role="status" className="text-sm text-muted-foreground">Select a benefit year in the company header to load employees.</p>}
    {recipients.isError && <p role="alert" className="text-sm text-error">{formatError(recipients.error)} <Button variant="link" size="sm" onClick={() => void recipients.refetch()}>Retry</Button></p>}
    <div className="flex flex-wrap items-center justify-between gap-2 text-sm">
      <p role="status"><strong>{selected.size.toLocaleString()}</strong> selected · {recipients.data?.total.toLocaleString() ?? "…"} matching</p>
      <div className="flex flex-wrap gap-2">
        <Button size="sm" variant="outline" disabled={!recipients.data?.eligible_ids.length || busy || recipients.isFetching || (recipients.data?.eligible_ids.length ?? 0) > 10000}
          onClick={() => changeSelection(new Set([...selected, ...(recipients.data?.eligible_ids ?? [])]))}>Select all {recipients.data?.eligible_ids.length ?? 0} eligible matches</Button>
        <Button size="sm" variant="ghost" disabled={!selected.size || busy} onClick={() => changeSelection(new Set())}>Clear selection</Button>
      </div>
    </div>
    <div className="overflow-x-auto rounded-lg border border-border">
      <table className="w-full text-left text-sm"><thead className="bg-muted/50 text-muted-foreground"><tr>
        <th className="p-3"><input type="checkbox" aria-label="Select eligible recipients on this page" className="size-4 accent-primary"
          disabled={!eligibleOnPage.length || busy || recipients.isFetching} checked={eligibleOnPage.length > 0 && eligibleOnPage.every(row => selected.has(row.id))}
          onChange={e => { const value = new Set(selected); eligibleOnPage.forEach(row => e.target.checked ? value.add(row.id) : value.delete(row.id)); changeSelection(value); }} /></th>
        <th className="p-3">Recipient</th><th className="p-3">Email</th><th className="p-3">Portal status</th><th className="p-3">Availability</th>
      </tr></thead><tbody>
        {recipients.isLoading && canQuery && <tr><td colSpan={5} className="p-6 text-muted-foreground">Loading recipients…</td></tr>}
        {recipients.data?.items.map(row => <tr key={row.id} className="border-t border-border align-top hover:bg-muted/20">
          <td className="p-3"><input type="checkbox" className="size-4 accent-primary" aria-label={`Select ${row.name}`} checked={selected.has(row.id)} disabled={!row.eligible || busy} onChange={() => toggle(row.id)} /></td>
          <td className="p-3"><span className="font-medium">{row.name}</span><span className="block text-xs text-muted-foreground">{row.staff_id}</span></td>
          <td className="max-w-60 break-all p-3">{row.email || "No email"}</td><td className="p-3 whitespace-nowrap">{row.status.replaceAll("_", " ")}</td>
          <td className="min-w-40 p-3 text-xs text-muted-foreground">{row.eligible ? row.account_required ? "Ready · account setup required" : "Ready" : row.reason}</td>
        </tr>)}
        {recipients.data?.total === 0 && <tr><td colSpan={5} className="p-6 text-muted-foreground">No recipients match your search.</td></tr>}
      </tbody></table>
    </div>
    <div className="flex items-center justify-between gap-2"><p className="text-xs text-muted-foreground">Selection is retained when you search or change pages.</p>
      <div className="flex gap-2"><Button size="sm" variant="outline" aria-label="Previous recipient page" disabled={!offset || recipients.isFetching} onClick={() => setOffset(value => Math.max(0, value - 50))}><ChevronLeft className="size-4" /></Button>
        <Button size="sm" variant="outline" aria-label="Next recipient page" disabled={offset + 50 >= (recipients.data?.total ?? 0) || recipients.isFetching} onClick={() => setOffset(value => value + 50)}><ChevronRight className="size-4" /></Button></div>
    </div>
    {selected.size > 10000 && <p role="alert" className="text-sm text-error">Limit each preparation to 10,000 recipients.</p>}
    <Button disabled={!selected.size || selected.size > 10000 || busy} loading={busy} onClick={() => void reviewRecipients()}>Review {selected.size} selected recipients</Button>
    {error && <p role="alert" className="text-sm text-error">{error}</p>}
    {review && <div className="space-y-4 border-t border-border pt-5">
      <h3 className="font-semibold">Review selected recipients</h3>
      <p className="text-sm">{review.eligible_count} eligible · {review.excluded_count} excluded{review.account_creation_count > 0 ? ` · ${review.account_creation_count} would need account setup` : ""}</p>
      <div className="max-h-72 overflow-auto rounded-md border border-border p-3"><ul className="space-y-2 text-sm">
        {review.recipients.map(row => <li className="break-words" key={row.id}><strong>{row.name}</strong> · {row.email || "No email"}{!row.eligible && <span className="block text-error">Excluded: {row.reason}</span>}</li>)}
      </ul></div>
      <p className="text-sm text-muted-foreground">Email delivery is not configured. Save this preparation for review; it will not send automatically when delivery is enabled. No accounts or credentials are changed.</p>
      <div className="flex flex-wrap gap-2"><Button variant="outline" disabled={!review.eligible_count || busy || prepared} onClick={() => void prepare()}>Save preparation</Button>
        <Button disabled aria-describedby="email-send-disabled">Send to {review.eligible_count} recipients</Button></div>
      <p id="email-send-disabled" className="text-xs text-muted-foreground">Sending becomes available after the email delivery integration is configured and verified.</p>
      {prepared && <p role="status" className="text-sm text-good">Preparation saved. No email was queued or sent.</p>}
      <details><summary className="cursor-pointer text-sm font-medium">Preview the published message</summary><div className="mt-4 max-w-2xl"><PreviewPanel content={content} scope="company" canReadHr /></div></details>
    </div>}
  </section>;
}
