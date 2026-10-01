/** The documents a member must read and the declarations they agree to —
 * Sections D and E of the paper form.
 *
 * MAS requires that a member READS the health-insurance guide, product summary
 * and benefit schedule before taking voluntary cover, so the documents come
 * first and each says whether it has been opened in this session. Opening is
 * recorded, not enforced: a member who read the guide last year is not made to
 * download it again to tick a box. */
import { ExternalLink, FileDown } from "lucide-react";
import { toast } from "sonner";
import type { FormClause, FormDocument } from "@/api/enrollmentForms";
import { downloadFormResource } from "@/api/portalEnrollmentForms";
import { Mount, MountRule } from "@/components/portal/leaf/Mount";
import { formatError } from "@/lib/errors";
import { choiceControl, choiceRowClass } from "../choiceRow";

export function AgreeMount({
  documents,
  clauses,
  accepted,
  opened,
  disabled,
  onAcceptedChange,
  onOpened,
}: {
  documents: FormDocument[];
  clauses: FormClause[];
  accepted: Set<string>;
  opened: Set<string>;
  disabled: boolean;
  onAcceptedChange: (next: Set<string>) => void;
  onOpened: (documentKey: string) => void;
}) {
  const toggle = (id: string, on: boolean) => {
    const next = new Set(accepted);
    if (on) next.add(id);
    else next.delete(id);
    onAcceptedChange(next);
  };
  const allOn = clauses.length > 0 && clauses.every((c) => accepted.has(c.id));

  async function open(doc: FormDocument) {
    try {
      if (doc.document_id) {
        await downloadFormResource(doc.document_id, doc.file_name ?? `${doc.label}.pdf`);
      }
      onOpened(doc.id);
    } catch (e) {
      toast.error(formatError(e));
    }
  }

  return (
    <Mount
      as="article"
      rise={false}
      label="Read and agree"
      gloss="Please read these before you sign. They explain what the cover includes and excludes."
    >
      {documents.length > 0 && (
        <ul className="flex flex-col">
          {documents.map((doc) => (
            <li key={doc.id} className="flex min-h-11 items-center justify-between gap-3 py-1">
              {doc.url ? (
                <a
                  href={doc.url}
                  target="_blank"
                  rel="noopener noreferrer"
                  onClick={() => onOpened(doc.id)}
                  className="leaf-focus inline-flex items-center gap-2 text-row font-medium text-action-ink"
                >
                  <ExternalLink className="size-4" aria-hidden />
                  {doc.label}
                </a>
              ) : (
                <button
                  type="button"
                  onClick={() => void open(doc)}
                  className="leaf-focus inline-flex items-center gap-2 text-row font-medium text-action-ink"
                >
                  <FileDown className="size-4" aria-hidden />
                  {doc.label}
                </button>
              )}
              <span className="text-row text-label">{opened.has(doc.id) ? "Opened" : ""}</span>
            </li>
          ))}
        </ul>
      )}

      {documents.length > 0 && clauses.length > 0 && <MountRule />}

      {clauses.length > 0 && (
        <fieldset className="flex flex-col">
          <legend className="sr-only">Declarations</legend>
          {clauses.map((clause) => {
            const on = accepted.has(clause.id);
            return (
              <label key={clause.id} className={choiceRowClass(on, "items-start")}>
                <input
                  type="checkbox"
                  className={`${choiceControl} mt-0.5`}
                  checked={on}
                  disabled={disabled}
                  onChange={(e) => toggle(clause.id, e.target.checked)}
                />
                <span className="text-row text-record">{clause.text}</span>
              </label>
            );
          })}
        </fieldset>
      )}

      {!disabled && clauses.length > 1 && (
        <button
          type="button"
          className="leaf-focus self-start text-row font-medium text-action-ink"
          onClick={() =>
            onAcceptedChange(allOn ? new Set() : new Set(clauses.map((c) => c.id)))
          }
        >
          {allOn ? "Untick all" : "I agree to all of the above"}
        </button>
      )}
    </Mount>
  );
}
