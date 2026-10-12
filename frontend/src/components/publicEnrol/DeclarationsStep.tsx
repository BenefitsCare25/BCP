import { useState } from "react";
import { Download, ExternalLink } from "lucide-react";
import { toast } from "sonner";
import { usePortalTranslation } from "@/i18n/portal";
import { fetchPublicDocument, type PublicDocument, type PublicForm } from "@/api/publicLinks";
import { Mount } from "@/components/portal/leaf/Mount";
import { choiceControl, choiceRowClass } from "@/components/portal/enrollment/choiceRow";
import { downloadResponseAsFile } from "@/lib/download";
import { applicableClauses, type Draft } from "./formState";

function DocumentRow({ token, doc }: { token: string; doc: PublicDocument }) {
  const pt = usePortalTranslation();
  const [busy, setBusy] = useState(false);
  const rowClass =
    "leaf-focus flex min-h-11 w-full items-center justify-between gap-3 rounded-control px-3 py-2 text-left " +
    "text-row font-semibold text-record hover:bg-shade/50";
  if (doc.has_file) {
    const open = async () => {
      setBusy(true);
      try {
        await downloadResponseAsFile(await fetchPublicDocument(token, doc.id), `${doc.label}.pdf`);
      } catch {
        toast.error(pt("That document could not be opened. Try again."));
      } finally {
        setBusy(false);
      }
    };
    return (
      <li>
        <button type="button" className={rowClass} onClick={() => void open()} disabled={busy} aria-busy={busy}>
          <span>{pt(doc.label)}</span>
          <Download className="size-4 shrink-0 text-action-ink" aria-hidden />
        </button>
      </li>
    );
  }
  if (!doc.url) return <li className="px-3 py-2 text-row text-record">{pt(doc.label)}</li>;
  return (
    <li>
      <a className={rowClass} href={doc.url} target="_blank" rel="noopener noreferrer">
        <span>{pt(doc.label)}</span>
        <ExternalLink className="size-4 shrink-0 text-action-ink" aria-hidden />
        <span className="sr-only">{pt("(opens in a new tab)")}</span>
      </a>
    </li>
  );
}

export function DeclarationsStep({
  token,
  form,
  draft,
  onChange,
}: {
  token: string;
  form: PublicForm;
  draft: Draft;
  onChange: (next: Draft) => void;
}) {
  const pt = usePortalTranslation();
  const clauses = applicableClauses(form, draft);
  const toggle = (id: string, on: boolean) =>
    onChange({ ...draft, accepted: on ? [...draft.accepted, id] : draft.accepted.filter(a => a !== id) });
  return (
    <div className="space-y-3">
      {form.documents.length > 0 && (
        <Mount label="Read before you sign" gloss="MAS guidelines ask you to read and understand these before applying for voluntary cover." rise={false}>
          <ul className="-mx-3 divide-y divide-hairline/75">
            {form.documents.map(doc => <DocumentRow key={doc.id} token={token} doc={doc} />)}
          </ul>
        </Mount>
      )}
      <Mount label="Declarations" gloss="Tick each one to confirm." rise={false}>
        <div className="space-y-1.5">
          {clauses.map(c => (
            <label key={c.id} className={choiceRowClass(draft.accepted.includes(c.id), "mx-0")}>
              <input type="checkbox" className={`${choiceControl} mt-0.5`} checked={draft.accepted.includes(c.id)}
                onChange={e => toggle(c.id, e.target.checked)} />
              <span className="text-row text-record">{pt(c.text)}</span>
            </label>
          ))}
        </div>
      </Mount>
    </div>
  );
}
