/** Broker config: the claim document-type registry.
 *
 * One card per recognised document type (Discharge Summary, Final Tax
 * Invoice, Tax Invoice (Finalised), + custom), each with editable chip rows:
 * ALIASES — alternate titles hospitals print, matched against the AI-detected
 * document type at intake and review; KEY FIELDS — the completeness check
 * (fields a genuine copy always carries; missing ones surface as broker-side
 * warnings in the AI review, never a member-facing block). Rows are
 * per-client, lazily seeded from the backend defaults; "Restore defaults"
 * discards customisations.
 *
 * Chip edits save immediately (each change is one small PUT); a key field
 * added here matches on its own name — the seeded fields carry richer
 * keyword sets (e.g. Surgery also matches "operation"/"procedure").
 */
import { useState } from "react";
import { Loader2, Plus, RotateCcw, Trash2, X } from "lucide-react";
import { toast } from "sonner";
import {
  useClaimDocTypes,
  useCreateClaimDocType,
  useDeleteClaimDocType,
  useResetClaimDocTypes,
  useUpdateClaimDocType,
  type ClaimDocType,
  type ClaimDocTypeInput,
} from "@/api/claims";
import { AlertDialog } from "@/components/ui/alert-dialog";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Skeleton } from "@/components/ui/skeleton";
import { formatError } from "@/lib/errors";

const SECTOR_LABELS: Record<string, string> = {
  govt: "Government hospital",
  private: "Private hospital",
};

function toInput(t: ClaimDocType): ClaimDocTypeInput {
  return {
    display: t.display,
    aliases: t.aliases,
    key_fields: t.key_fields,
    sector: t.sector,
    slot_key: t.slot_key,
  };
}

/** A chip row with inline add + per-chip remove. */
function ChipRow({
  label,
  hint,
  chips,
  chipTitle,
  placeholder,
  disabled,
  onAdd,
  onRemove,
}: {
  label: string;
  hint?: string;
  chips: string[];
  chipTitle?: (chip: string, index: number) => string | undefined;
  placeholder: string;
  disabled: boolean;
  onAdd: (value: string) => void;
  onRemove: (index: number) => void;
}) {
  const [draft, setDraft] = useState("");
  const add = () => {
    const v = draft.trim();
    if (!v) return;
    if (chips.some((c) => c.toLowerCase() === v.toLowerCase())) {
      setDraft("");
      return;
    }
    onAdd(v);
    setDraft("");
  };
  return (
    <div className="space-y-1.5">
      <p className="text-xs font-medium text-muted-foreground">
        {label}
        {hint && <span className="font-normal text-muted-foreground/60"> — {hint}</span>}
      </p>
      <div className="flex flex-wrap items-center gap-1.5">
        {chips.map((chip, i) => (
          <span
            key={`${chip}-${i}`}
            title={chipTitle?.(chip, i)}
            className="inline-flex items-center gap-1 rounded-md bg-muted px-2 py-1 text-xs text-foreground"
          >
            {chip}
            <button
              type="button"
              disabled={disabled}
              onClick={() => onRemove(i)}
              className="text-muted-foreground hover:text-foreground disabled:opacity-50"
              aria-label={`Remove ${chip}`}
            >
              <X className="size-3" />
            </button>
          </span>
        ))}
        <div className="flex items-center gap-1">
          <Input
            value={draft}
            disabled={disabled}
            placeholder={placeholder}
            className="h-7 w-44 text-xs"
            maxLength={128}
            onChange={(e) => setDraft(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter") {
                e.preventDefault();
                add();
              }
            }}
          />
          <Button
            type="button"
            variant="ghost"
            size="sm"
            className="h-7 px-2"
            disabled={disabled || !draft.trim()}
            onClick={add}
          >
            <Plus className="size-3.5" />
          </Button>
        </div>
      </div>
    </div>
  );
}

function DocTypeCard({ docType }: { docType: ClaimDocType }) {
  const update = useUpdateClaimDocType();
  const del = useDeleteClaimDocType();
  const [confirmDelete, setConfirmDelete] = useState(false);
  const busy = update.isPending || del.isPending;

  const save = (patch: Partial<ClaimDocTypeInput>) => {
    update.mutate(
      { id: docType.id, ...toInput(docType), ...patch },
      { onError: (e) => toast.error(formatError(e)) },
    );
  };

  return (
    <div className="rounded-lg border border-border bg-card p-4 space-y-3">
      <div className="flex items-start justify-between gap-2">
        <div className="flex flex-wrap items-center gap-2">
          <h3 className="text-sm font-semibold text-foreground">{docType.display}</h3>
          {docType.sector && (
            <Badge variant="outline">{SECTOR_LABELS[docType.sector]}</Badge>
          )}
          {!docType.is_default && <Badge variant="info">Custom</Badge>}
        </div>
        <button
          type="button"
          disabled={busy}
          onClick={() => setConfirmDelete(true)}
          className="text-muted-foreground hover:text-error disabled:opacity-50"
          aria-label={`Delete ${docType.display}`}
        >
          <Trash2 className="size-4" />
        </button>
      </div>

      <ChipRow
        label="Aliases"
        hint="other titles this document appears under"
        chips={docType.aliases}
        placeholder="Add alias"
        disabled={busy}
        onAdd={(v) => save({ aliases: [...docType.aliases, v] })}
        onRemove={(i) =>
          save({ aliases: docType.aliases.filter((_, j) => j !== i) })
        }
      />

      <ChipRow
        label="Key fields (for completeness check)"
        hint="a genuine copy always shows these"
        chips={docType.key_fields.map((f) => f.name)}
        chipTitle={(_, i) => {
          const kws = docType.key_fields[i]?.keywords ?? [];
          return kws.length ? `Matches: ${kws.join(", ")}` : undefined;
        }}
        placeholder="Add key field"
        disabled={busy}
        onAdd={(v) =>
          save({
            key_fields: [...docType.key_fields, { name: v, keywords: [] }],
          })
        }
        onRemove={(i) =>
          save({ key_fields: docType.key_fields.filter((_, j) => j !== i) })
        }
      />

      <AlertDialog
        open={confirmDelete}
        onOpenChange={setConfirmDelete}
        title={`Delete "${docType.display}"?`}
        description="Uploaded documents will no longer be recognised as this type, and its completeness check stops running. Restore defaults brings the seeded types back."
        confirmLabel="Delete"
        loading={del.isPending}
        onConfirm={() =>
          del.mutate(docType.id, {
            onSuccess: () => setConfirmDelete(false),
            onError: (e) => toast.error(formatError(e)),
          })
        }
      />
    </div>
  );
}

export function DocTypeSettings() {
  const docTypes = useClaimDocTypes();
  const create = useCreateClaimDocType();
  const reset = useResetClaimDocTypes();
  const [adding, setAdding] = useState(false);
  const [newName, setNewName] = useState("");
  const [confirmReset, setConfirmReset] = useState(false);

  const addNew = () => {
    const name = newName.trim();
    if (!name) return;
    create.mutate(
      {
        display: name,
        aliases: [name.toLowerCase()],
        key_fields: [],
        sector: null,
        slot_key: null,
      },
      {
        onSuccess: () => {
          setNewName("");
          setAdding(false);
        },
        onError: (e) => toast.error(formatError(e)),
      },
    );
  };

  return (
    <Card>
      <CardHeader>
        <div className="flex items-start justify-between gap-3 flex-wrap">
          <div>
            <CardTitle>Claim document types</CardTitle>
            <CardDescription>
              How uploaded claim documents are recognised. Aliases match the
              document's title; key fields drive the completeness check —
              missing ones appear as warnings in the AI review, and never block
              the member.
            </CardDescription>
          </div>
          <Button
            type="button"
            variant="outline"
            size="sm"
            disabled={reset.isPending}
            onClick={() => setConfirmReset(true)}
          >
            {reset.isPending ? (
              <Loader2 className="size-3.5 animate-spin" />
            ) : (
              <RotateCcw className="size-3.5" />
            )}
            <span className="ml-1.5">Restore defaults</span>
          </Button>
        </div>
      </CardHeader>
      <CardContent className="space-y-3">
        {docTypes.isLoading ? (
          <Skeleton className="h-40 w-full" />
        ) : docTypes.isError ? (
          <p className="text-sm text-error">{formatError(docTypes.error)}</p>
        ) : (
          <>
            {(docTypes.data ?? []).map((t) => (
              <DocTypeCard key={t.id} docType={t} />
            ))}
            {adding ? (
              <div className="flex items-center gap-2">
                <Input
                  autoFocus
                  value={newName}
                  placeholder="Document type name (e.g. Referral Memo)"
                  className="h-8 max-w-xs text-sm"
                  maxLength={128}
                  onChange={(e) => setNewName(e.target.value)}
                  onKeyDown={(e) => {
                    if (e.key === "Enter") {
                      e.preventDefault();
                      addNew();
                    }
                  }}
                />
                <Button
                  type="button"
                  size="sm"
                  disabled={create.isPending || !newName.trim()}
                  onClick={addNew}
                >
                  {create.isPending ? (
                    <Loader2 className="size-3.5 animate-spin" />
                  ) : (
                    "Add"
                  )}
                </Button>
                <Button
                  type="button"
                  variant="ghost"
                  size="sm"
                  onClick={() => {
                    setAdding(false);
                    setNewName("");
                  }}
                >
                  Cancel
                </Button>
              </div>
            ) : (
              <Button
                type="button"
                variant="outline"
                size="sm"
                onClick={() => setAdding(true)}
              >
                <Plus className="size-3.5" />
                <span className="ml-1.5">Add document type</span>
              </Button>
            )}
          </>
        )}
      </CardContent>

      <AlertDialog
        open={confirmReset}
        onOpenChange={setConfirmReset}
        title="Restore default document types?"
        description="This discards every customisation (added aliases, key fields, and custom types) and restores the seeded defaults."
        confirmLabel="Restore defaults"
        loading={reset.isPending}
        onConfirm={() =>
          reset.mutate(undefined, {
            onSuccess: () => setConfirmReset(false),
            onError: (e) => toast.error(formatError(e)),
          })
        }
      />
    </Card>
  );
}
