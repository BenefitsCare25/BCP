import { useEffect, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "@/api/client";
import { useMe } from "@/api/hooks";
import { AlertDialog } from "@/components/ui/alert-dialog";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { formatError } from "@/lib/errors";
import { isFirmOwnerRole } from "@/lib/roles";
import { useSession } from "@/stores/session";
import { Field } from "./TemplateEditor";
import { apiPath, clientOptions, fieldClass, SOURCE_LABELS, type Branding, type BrandingContent, type Scope } from "./types";
import { validEmail, validUrl } from "./validation";

export function BrandingEditor({ scope, editable, onDirty }: { scope: Scope; editable: boolean; onDirty: (value: boolean) => void }) {
  const clientId = useSession(s => s.activeClientId);
  const query = useQuery({ queryKey: ["email-branding", clientId, scope], queryFn: () => api.get<Branding>(apiPath(scope, "/branding"), clientOptions(clientId)) });
  if (query.isError) return <p role="alert" className="text-sm text-error">{formatError(query.error)} <Button variant="link" onClick={() => void query.refetch()}>Retry</Button></p>;
  if (!query.data) return <p role="status" className="text-sm text-muted-foreground">Loading sender and branding…</p>;
  return <BrandingForm key={`${clientId}-${scope}`} initial={query.data} scope={scope} editable={editable} onDirty={onDirty} />;
}
function BrandingForm({ initial, scope, editable, onDirty }: { initial: Branding; scope: Scope; editable: boolean; onDirty: (value: boolean) => void }) {
  const clientId = useSession(s => s.activeClientId);
  const { data: me } = useMe();
  const [confirmReset, setConfirmReset] = useState(false);
  const [content, setContent] = useState(initial.content);
  const [baseline, setBaseline] = useState(initial.content);
  const [revision, setRevision] = useState(initial.revision);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [message, setMessage] = useState("");
  const qc = useQueryClient();
  const dirty = JSON.stringify(content) !== JSON.stringify(baseline);
  useEffect(() => { onDirty(dirty || busy); return () => onDirty(false); }, [dirty, busy, onDirty]);
  const errors: Partial<Record<keyof BrandingContent, string>> = {};
  if (!content.sender_display_name.trim() || /[\r\n\x00-\x1f\x7f{}]/.test(content.sender_display_name)) errors.sender_display_name = "Enter a single-line sender name without placeholders.";
  if (!validEmail(content.support_email)) errors.support_email = "Enter a valid support email address.";
  if (content.logo_url && !validUrl(content.logo_url)) errors.logo_url = "Use an HTTPS logo URL.";
  if (content.footer.includes("{{") || content.footer.includes("}}")) errors.footer = "Footer text does not support placeholders.";
  const save = async () => {
    setBusy(true); setError(""); setMessage("");
    try {
      const saved = await api.put<Branding>(apiPath(scope, "/branding"), { content, revision }, clientOptions(clientId));
      setRevision(saved.revision); setBaseline(saved.content); setMessage("Branding saved.");
      await qc.invalidateQueries({ queryKey: ["email-branding"] });
      await qc.invalidateQueries({ queryKey: ["email-preview"] });
    } catch (caught) { setError(formatError(caught)); }
    finally { setBusy(false); }
  };
  const reset = async () => {
    setBusy(true); setError(""); setMessage("");
    try {
      await api.delete(apiPath(scope, "/branding"), clientOptions(clientId));
      const inherited = await api.get<Branding>(apiPath(scope, "/branding"), clientOptions(clientId));
      setContent(inherited.content); setBaseline(inherited.content); setRevision(inherited.revision);
      setConfirmReset(false); setMessage("Inherited branding restored.");
      await qc.invalidateQueries({ queryKey: ["email-branding"] });
      await qc.invalidateQueries({ queryKey: ["email-preview"] });
    } catch (caught) { setError(formatError(caught)); }
    finally { setBusy(false); }
  };
  return <div className="max-w-2xl space-y-5">
    <div><h3 className="text-lg font-semibold">Sender & branding</h3><p className="text-sm text-muted-foreground">{SOURCE_LABELS[initial.source]} · {scope === "firm" ? "Applies to companies inheriting broker defaults" : "Applies to this company"}</p></div>
    <p className="rounded-md bg-muted p-3 text-sm">Delivery is not configured. The approved From address will be supplied by the mail integration; changing these details does not enable sending.</p>
    {([
      ["sender_display_name", "Sender display name", 120], ["support_email", "Support / Reply-To email", 320], ["logo_url", "Logo URL (optional)", 2000],
    ] as const).map(([key, label, length]) => <Field key={key} id={`branding-${key}`} label={label} error={errors[key]}>
      <Input id={`branding-${key}`} value={content[key]} maxLength={length} disabled={!editable || busy} aria-invalid={!!errors[key]} aria-describedby={`branding-${key}-help`}
        onChange={e => { setContent(previous => ({ ...previous, [key]: e.target.value })); setMessage(""); }} />
    </Field>)}
    <Field id="branding-footer" label="Footer (optional)" error={errors.footer}>
      <textarea id="branding-footer" className={`${fieldClass} min-h-24`} value={content.footer} maxLength={1000} disabled={!editable || busy} aria-invalid={!!errors.footer}
        onChange={e => { setContent(previous => ({ ...previous, footer: e.target.value })); setMessage(""); }} />
    </Field>
    {error && <p role="alert" className="text-sm text-error">{error}</p>}
    <div className="flex flex-wrap gap-2">{editable && <Button disabled={!dirty || busy || Object.keys(errors).length > 0} loading={busy} onClick={() => void save()}>Save branding</Button>}
      {isFirmOwnerRole(me?.role) && revision > 0 && <Button variant="outline" disabled={busy} onClick={() => setConfirmReset(true)}>Restore inherited branding</Button>}</div>
    <p role="status" className="text-sm text-muted-foreground">{message}</p>
    <AlertDialog open={confirmReset} onOpenChange={setConfirmReset} title="Restore inherited branding?" description="This removes the saved branding at this scope and discards unsaved changes. Inherited defaults will apply." confirmLabel="Restore branding" loading={busy} onConfirm={reset} />
  </div>;
}
