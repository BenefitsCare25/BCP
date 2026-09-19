import { useState } from "react";
import { BENEFIT_TYPES, DOCUMENT_TYPES, type WicaDocument, type WicaIncident } from "@/api/wica";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Choice, Field, dateLabel, today } from "./shared";

type Props = { doc: WicaDocument; row: WicaIncident; editable: boolean; busy: boolean;
  save: (body: Record<string, unknown>) => void; action: (body: Record<string, unknown>) => void; remove: () => void };

export function DocumentEditor({ doc, row, editable, busy, save, action, remove }: Props) {
  const [benefit, setBenefit] = useState(doc.benefit_type ?? "");
  const [related, setRelated] = useState(doc.related_ids);
  const [outcome, setOutcome] = useState<"settled" | "rejected" | null>(null);
  const [confirmRemove, setConfirmRemove] = useState(false);
  const [editing, setEditing] = useState(!doc.doc_type);
  const tagAllowed = editable && ["untagged", "supporting", "submitted"].includes(doc.status);
  const canTag = tagAllowed && editing;
  return <div className="space-y-4">
    {doc.claim_id && <p className="break-all text-xs text-muted-foreground">Claim ID · {doc.claim_id}</p>}
    {canTag ? <form aria-label={`Tag ${doc.file_name}`} className="space-y-4" onSubmit={e => {
      e.preventDefault(); const data = new FormData(e.currentTarget);
      save({ doc_type: data.get("doc_type"), document_date: data.get("document_date"), benefit_type: benefit,
        provider: data.get("provider"), invoice_number: data.get("invoice_number"), incurred_amount: data.get("incurred_amount") || null,
        related_ids: benefit === "Others" ? [] : related });
    }}><fieldset disabled={busy} className="space-y-4">
      <div className="grid gap-4 md:grid-cols-3">
        <Field label="Document type"><Choice name="doc_type" defaultValue={doc.doc_type ?? ""} required><option value="">Select type</option>{DOCUMENT_TYPES.map(v => <option key={v}>{v}</option>)}</Choice></Field>
        <Field label="Document date"><Input name="document_date" type="date" defaultValue={doc.document_date ?? ""} required /></Field>
        <Field label="Benefit type"><Choice value={benefit} onChange={e => setBenefit(e.target.value)} disabled={!!doc.claim_id} required><option value="">Select benefit</option>{BENEFIT_TYPES.map(v => <option key={v}>{v}</option>)}</Choice></Field>
        <Field label="Provider (optional)"><Input name="provider" defaultValue={doc.provider} maxLength={255} /></Field>
        <Field label="Invoice number (optional)"><Input name="invoice_number" defaultValue={doc.invoice_number} maxLength={128} /></Field>
        <Field label="Incurred amount (SGD, optional)"><Input name="incurred_amount" type="number" min="0" max="999999999999.99" step="0.01" defaultValue={doc.incurred_amount ?? ""} /></Field>
      </div>
      {benefit && benefit !== "Others" && <fieldset className="space-y-2"><legend className="mb-2 text-sm font-medium">Related documents</legend>{row.documents.filter(d => d.id !== doc.id && d.status !== "untagged").map(d => <label key={d.id} className="flex items-center gap-2 text-sm"><input type="checkbox" className="size-4 shrink-0 accent-primary" checked={related.includes(d.id)} onChange={e => setRelated(e.target.checked ? [...related, d.id] : related.filter(id => id !== d.id))} /><span className="break-all">{d.file_name}</span></label>)}{!row.documents.some(d => d.id !== doc.id && d.status !== "untagged") && <p className="text-sm text-muted-foreground">No other tagged documents.</p>}</fieldset>}
      <div className="flex flex-wrap items-center gap-2"><Button loading={busy}>Save tags</Button>{doc.doc_type && <Button type="button" variant="ghost" onClick={() => setEditing(false)}>Cancel</Button>}{doc.status === "untagged" && <Button type="button" variant="ghost" onClick={() => setConfirmRemove(v => !v)}>Remove upload</Button>}</div>
      {confirmRemove && <div className="flex flex-wrap items-center gap-3 text-sm"><span>Remove this untagged upload from the incident?</span><Button type="button" variant="destructiveOutline" onClick={remove}>Confirm removal</Button><Button type="button" variant="ghost" onClick={() => setConfirmRemove(false)}>Cancel</Button></div>}
    </fieldset></form> : <dl className="grid gap-x-6 gap-y-3 text-sm sm:grid-cols-2 lg:grid-cols-3">
      {[["Benefit", doc.benefit_type], ["Document date", doc.document_date ? dateLabel(doc.document_date) : null], ["Provider", doc.provider], ["Invoice number", doc.invoice_number], ["Incurred amount (SGD)", doc.incurred_amount], ["Settlement amount (SGD)", doc.settlement_amount], ["Settlement date", doc.settlement_date ? dateLabel(doc.settlement_date) : null], ["Insurer reference", doc.insurer_reference], ["Broker remarks", doc.remarks]].filter(([,v]) => v !== null && v !== "").map(([label, value]) => <div key={label} className={label === "Benefit" || label === "Document date" ? "md:hidden" : undefined}><dt className="text-xs text-muted-foreground">{label}</dt><dd className="mt-1 break-words">{value}</dd></div>)}
      {doc.related_ids.length > 0 && <div className="sm:col-span-2"><dt className="text-xs text-muted-foreground">Related documents</dt><dd className="mt-1 break-words">{doc.related_ids.map(id => row.documents.find(d => d.id === id)?.file_name).filter(Boolean).join(", ")}</dd></div>}
    </dl>}
    {tagAllowed && !editing && <Button variant="outline" disabled={busy} onClick={() => setEditing(true)}>Edit tags</Button>}
    {editable && !editing && doc.claim_id && ["submitted", "pending_insurer"].includes(doc.status) && <div className="flex flex-wrap gap-2 border-t border-border pt-4">
      {doc.status === "submitted" ? <Button disabled={busy} onClick={() => action({ status: "pending_insurer" })}>Set pending insurer approval</Button> : <><Button variant="outline" disabled={busy} onClick={() => action({ status: "submitted" })}>Undo pending insurer approval</Button><Button disabled={busy} onClick={() => setOutcome("settled")}>Record settlement</Button></>}
      <Button variant="destructiveOutline" disabled={busy} onClick={() => setOutcome("rejected")}>Reject claim</Button>
    </div>}
    {outcome && <form aria-label={outcome === "settled" ? "Record settlement" : "Reject claim"} className="space-y-4 border-t border-border pt-4" onSubmit={e => {
      e.preventDefault(); const data = new FormData(e.currentTarget);
      action({ status: outcome, insurer_reference: data.get("insurer_reference"), remarks: data.get("remarks"), ...(outcome === "settled" ? { settlement_date: data.get("settlement_date"), settlement_amount: data.get("settlement_amount") } : {}) });
    }}><fieldset disabled={busy} className="space-y-4"><div className="grid gap-4 md:grid-cols-2">
      {outcome === "settled" && <><Field label="Settlement amount (SGD)"><Input name="settlement_amount" type="number" min="0" step="0.01" max="999999999999.99" required /></Field><Field label="Settlement date"><Input name="settlement_date" type="date" min={row.incident_date} max={today()} required /></Field></>}
      <Field label="Insurer reference (optional)"><Input name="insurer_reference" maxLength={128} defaultValue={doc.insurer_reference} /></Field><Field label={outcome === "rejected" ? "Rejection reason" : "Broker remarks (optional)"}><Input name="remarks" maxLength={2000} required={outcome === "rejected"} /></Field>
    </div><div className="flex gap-2"><Button loading={busy}>{outcome === "settled" ? "Save settlement" : "Confirm rejection"}</Button><Button type="button" variant="ghost" onClick={() => setOutcome(null)}>Cancel</Button></div></fieldset></form>}
  </div>;
}
