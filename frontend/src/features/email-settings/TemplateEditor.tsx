import { useEffect, useRef, useState, type ReactNode } from "react";
import { useQuery } from "@tanstack/react-query";
import { ArrowLeft, Bold, Italic, List, Save } from "lucide-react";
import { api } from "@/api/client";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { AlertDialog } from "@/components/ui/alert-dialog";
import { formatError } from "@/lib/errors";
import { useSession } from "@/stores/session";
import { PreviewPanel } from "./PreviewPanel";
import { fieldsFor, validate } from "./validation";
import { apiPath, clientOptions, BLANK, fieldClass, PURPOSE_LABELS, type Content, type Scope, type Template } from "./types";

export function TemplateEditor({ item, initial, scope, fields, editable, systemAdmin, onBack, onSaved, onDirty }: {
  item?: Template; initial?: Content; scope: Scope; fields: Record<string, string>; editable: boolean;
  systemAdmin: boolean; onBack: () => void; onSaved: (row: Template) => void; onDirty: (value: boolean) => void;
}) {
  const [content, setContent] = useState<Content>(initial ?? item?.content ?? BLANK);
  const [baseline, setBaseline] = useState(content);
  const [revision, setRevision] = useState(item?.revision ?? 0);
  const [savedKey, setSavedKey] = useState(item?.key);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [publishOpen, setPublishOpen] = useState(false);
  const [saved, setSaved] = useState("");
  const [touched, setTouched] = useState<Set<string>>(new Set());
  const target = useRef<keyof Content>("body");
  const clientId = useSession(s => s.activeClientId);
  const errors = validate(content, fields);
  const dirty = JSON.stringify(content) !== JSON.stringify(baseline);
  useEffect(() => { onDirty(dirty || busy); return () => onDirty(false); }, [dirty, busy, onDirty]);
  const versions = useQuery({ queryKey: ["email-versions", clientId, scope, item?.key, item?.published_version],
    queryFn: () => api.get<{ version: number; content: Content; created_at: string }[]>(apiPath(scope, `/${item!.key}/versions`), clientOptions(clientId)),
    enabled: !!item?.has_local_draft });
  const change = (key: keyof Content, value: string) => {
    setContent(previous => ({ ...previous, [key]: value }));
    setTouched(previous => new Set([...previous, key])); setSaved("");
  };
  const insert = (before: string, after = "") => {
    const key = target.current;
    const element = document.getElementById(`email-${key}`) as HTMLInputElement | HTMLTextAreaElement | null;
    const start = element?.selectionStart ?? content[key].length;
    const end = element?.selectionEnd ?? start;
    const selected = content[key].slice(start, end);
    change(key, content[key].slice(0, start) + before + selected + after + content[key].slice(end));
    requestAnimationFrame(() => { element?.focus(); element?.setSelectionRange(start + before.length, end + before.length); });
  };
  const save = async (publish = false) => {
    if (busy) return;
    if (!content.title.trim() || (publish && Object.keys(errors).length)) {
      setTouched(new Set(Object.keys(content))); document.getElementById(`email-${Object.keys(errors)[0] ?? "title"}`)?.focus(); return;
    }
    setBusy(true); setError(""); setSaved("");
    try {
      const requestOptions = clientOptions(clientId);
      let row = savedKey ? await api.put<Template>(apiPath(scope, `/${savedKey}`), { content, revision }, requestOptions) :
        await api.post<Template>(apiPath(scope), { content, revision: 0 }, requestOptions);
      setSavedKey(row.key);
      setRevision(row.revision); setBaseline(row.content);
      if (publish) {
        row = await api.post<Template>(apiPath(scope, `/${row.key}/publish`), { revision: row.revision }, requestOptions);
        setRevision(row.revision);
      }
      setPublishOpen(false); setSaved(publish ? "Template published. No email was sent." : "Draft saved.");
      onSaved(row);
    } catch (caught) { setError(formatError(caught)); }
    finally { setBusy(false); }
  };
  const field = (key: "title" | "subject" | "preheader" | "button_label" | "button_url", label: string, limit: number, hint?: string) =>
    <Field id={`email-${key}`} label={label} error={touched.has(key) ? errors[key] : undefined} hint={hint}>
      <Input id={`email-${key}`} value={content[key]} maxLength={limit} disabled={!editable || busy}
        aria-invalid={touched.has(key) && !!errors[key]} aria-describedby={`email-${key}-help`}
        onFocus={() => { if (key !== "title") target.current = key; }}
        onBlur={() => setTouched(previous => new Set([...previous, key]))} onChange={e => change(key, e.target.value)} />
    </Field>;
  return <div className="space-y-5">
    <div className="flex flex-wrap items-center justify-between gap-3">
      <Button variant="ghost" size="sm" onClick={onBack}><ArrowLeft className="size-4" />Templates</Button>
      <p className="text-sm text-muted-foreground">{scope === "firm" ? "Editing broker defaults · applies to inheriting companies" : "Editing this company's template"}</p>
    </div>
    <div className="grid min-w-0 gap-8 xl:grid-cols-2">
      <section className="min-w-0 space-y-4" aria-label="Template editor">
        <h3 className="text-lg font-semibold">{item ? "Edit template" : "New template"}</h3>
        {field("title", "Template title", 120, "Internal name. This is separate from the email subject.")}
        <div className="grid gap-4 sm:grid-cols-2">
          <Field id="email-audience" label="Audience"><select id="email-audience" className={fieldClass} disabled={!editable || busy}
            value={content.audience} onChange={e => change("audience", e.target.value)}><option value="employee">Employees</option><option value="hr">HR users</option></select></Field>
          <Field id="email-purpose" label="Purpose"><select id="email-purpose" className={fieldClass} disabled={!editable || busy}
            value={content.purpose} onChange={e => {
              const purpose = e.target.value as Content["purpose"];
              setContent(previous => ({ ...previous, purpose, button_url: "", button_label: purpose === "general" ? "" : "Set up my account" }));
            }}>{Object.entries(PURPOSE_LABELS).map(([key, label]) => <option key={key} value={key}>{label}</option>)}</select></Field>
        </div>
        {field("subject", "Email subject", 200)}
        {field("preheader", "Inbox preview text (optional)", 200)}
        <div className="flex flex-wrap items-center gap-2">
          <Label htmlFor="email-insert-field">Insert placeholder</Label>
          <select id="email-insert-field" className={`${fieldClass} sm:w-auto`} value="" disabled={!editable || busy}
            onChange={e => { if (e.target.value) insert(`{{${e.target.value}}}`); }}>
            <option value="">Choose a field</option>{Object.entries(fieldsFor(content, fields)).map(([key, label]) => <option key={key} value={key}>{label}</option>)}
          </select>
        </div>
        <Field id="email-body" label="Email content" error={touched.has("body") ? errors.body : undefined}
          hint="Use **bold**, *italic*, bullet lines starting with - and HTTPS links. Placeholders are checked before publication.">
          <div className="mb-2 flex flex-wrap gap-1" role="group" aria-label="Text formatting">
            <Button variant="outline" size="sm" disabled={!editable || busy} onMouseDown={e => e.preventDefault()} onClick={() => { target.current = "body"; insert("**", "**"); }} aria-label="Bold text"><Bold className="size-4" /></Button>
            <Button variant="outline" size="sm" disabled={!editable || busy} onMouseDown={e => e.preventDefault()} onClick={() => { target.current = "body"; insert("*", "*"); }} aria-label="Italic text"><Italic className="size-4" /></Button>
            <Button variant="outline" size="sm" disabled={!editable || busy} onMouseDown={e => e.preventDefault()} onClick={() => { target.current = "body"; insert("\n- "); }} aria-label="Bullet list"><List className="size-4" /></Button>
          </div>
          <textarea id="email-body" className={`${fieldClass} min-h-64 resize-y`} value={content.body} maxLength={20000}
            disabled={!editable || busy} onFocus={() => { target.current = "body"; }} aria-invalid={touched.has("body") && !!errors.body}
            aria-describedby="email-body-help" onBlur={() => setTouched(previous => new Set([...previous, "body"]))} onChange={e => change("body", e.target.value)} />
        </Field>
        {field("button_label", content.purpose === "general" ? "Button label (optional)" : "Account-setup button label", 60)}
        {content.purpose === "general" ? field("button_url", "Button destination", 2000, "HTTPS address or {{portal_url}}.") :
          <p className="rounded-md bg-muted p-3 text-sm text-muted-foreground">When delivery is integrated, the invitation workflow will supply the recipient's sign-in ID, single-use setup link and expiry. Previewing or publishing does not create credentials.</p>}
        {error && <p role="alert" className="break-words text-sm text-error">{error}</p>}
        <div className="sticky bottom-0 flex flex-wrap items-center gap-2 border-t border-border bg-background py-3">
          {editable && <><Button variant="outline" loading={busy} disabled={!content.title.trim() || busy} onClick={() => void save()}><Save className="size-4" />Save draft</Button>
            <Button disabled={busy || Object.keys(errors).length > 0} onClick={() => setPublishOpen(true)}>Publish template</Button></>}
          <span role="status" className="text-xs text-muted-foreground">{saved || (dirty ? "Unsaved changes" : item?.has_changes ? "Draft changes are not published" : "")}</span>
        </div>
        {Object.keys(errors).length > 0 && <p className="text-xs text-muted-foreground">Complete required fields and correct placeholders to publish. Incomplete work can be saved as a draft.</p>}
        {versions.data && versions.data.length > 0 && <details className="text-sm"><summary className="cursor-pointer font-medium">Published versions ({versions.data.length})</summary>
          <ul className="mt-3 space-y-3">{versions.data.map(version => <li key={version.version} className="rounded-md border border-border p-3">
            <p className="font-medium">Version {version.version} · {new Date(version.created_at).toLocaleString()}</p>
            <p className="break-words text-muted-foreground">{version.content.subject}</p>
            <details><summary className="cursor-pointer text-xs">View content</summary><p className="mt-2 whitespace-pre-wrap break-words">{version.content.body}</p></details>
            {systemAdmin && <Button variant="link" size="sm" onClick={() => setContent(version.content)}>Use this version as draft</Button>}
          </li>)}</ul></details>}
      </section>
      <PreviewPanel content={content} scope={scope} canReadHr={editable} />
    </div>
    <AlertDialog open={publishOpen} onOpenChange={setPublishOpen} tone="info" title="Publish this template?"
      description={scope === "firm" ? "Future preparations in companies using this broker default will use this version. Company overrides stay unchanged. No email will be sent." : "Future preparations for this company will use this version. No email will be sent."}
      confirmLabel="Publish" confirmVariant="default" loading={busy} onConfirm={() => save(true)} />
  </div>;
}

export function Field({ id, label, error, hint, children }: {
  id: string; label: string; error?: string; hint?: string; children: ReactNode;
}) {
  return <div className="min-w-0 space-y-1.5"><Label htmlFor={id}>{label}</Label>{children}
    <p id={`${id}-help`} className={`text-xs ${error ? "text-error" : "text-muted-foreground"}`} role={error ? "alert" : undefined}>{error || hint}</p>
  </div>;
}
