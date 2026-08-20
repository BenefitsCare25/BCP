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
  DOC_TYPE_LABELS,
  useClaimDocTypes,
  useCreateClaimDocType,
  useDeleteClaimDocType,
  useResetClaimDocTypes,
  useReviewScopeOptions,
  useUpdateClaimDocType,
  type ClaimDocType,
  type ClaimDocTypeInput,
  type ReviewClaimType,
} from "@/api/claims";
import { AlertDialog } from "@/components/ui/alert-dialog";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { NativeSelect } from "@/components/ui/native-select";
import { SectionLabel } from "@/components/ui/section-label";
import { Skeleton } from "@/components/ui/skeleton";
import { NoCurrentYearNotice } from "@/components/shell/CurrentYearBanner";
import { formatError } from "@/lib/errors";

const SECTOR_LABELS: Record<string, string> = {
  govt: "Government hospital",
  private: "Private hospital",
};

/** A labelled control. The label sits ABOVE its control: inline labels put the
 * control mid-sentence, which reads as prose rather than as a form field. */
function Field({
  label,
  children,
}: {
  label: string;
  children: React.ReactNode;
}) {
  return (
    <label className="flex min-w-0 flex-col gap-1.5">
      <SectionLabel as="span">{label}</SectionLabel>
      {children}
    </label>
  );
}

function toInput(t: ClaimDocType): ClaimDocTypeInput {
  return {
    display: t.display,
    aliases: t.aliases,
    key_fields: t.key_fields,
    sector: t.sector,
    slot_key: t.slot_key,
    claim_scope_keys: [...t.claim_scope_keys],
  };
}

function scopePatternMatches(pattern: string, scopeKey: string): boolean {
  const expected = pattern.toLowerCase().split(":");
  const actual = scopeKey.toLowerCase().split(":");
  return (
    expected.length === actual.length &&
    expected.every((part, index) => part === "*" || part === actual[index])
  );
}

function scopeRows(type: ReviewClaimType) {
  return type.claim_kind === "insured" && type.scopes.length > 0
    ? type.scopes.map((scope) => ({ key: scope.key, label: scope.display_label }))
    : [{ key: type.key, label: type.display_label }];
}

/** Scope-first routing matrix. Aliases identify WHAT the document is; this
 * matrix says WHICH claim choice that recognised type may preselect. */
function ScopeAssignments({
  claimTypes,
  docTypes,
  fetching,
}: {
  claimTypes: ReviewClaimType[];
  docTypes: ClaimDocType[];
  fetching: boolean;
}) {
  const update = useUpdateClaimDocType();
  const allScopeKeys = claimTypes.flatMap((type) =>
    scopeRows(type).map((scope) => scope.key),
  );
  const busy = update.isPending || fetching;

  const toggle = (docType: ClaimDocType, scopeKey: string, checked: boolean) => {
    // Expand wildcard defaults to the current concrete catalogue on first edit,
    // so a broker can remove one product without silently removing its peers.
    const concrete = new Set<string>();
    for (const configured of docType.claim_scope_keys) {
      if (configured.includes("*")) {
        for (const available of allScopeKeys) {
          if (scopePatternMatches(configured, available)) concrete.add(available);
        }
      } else {
        concrete.add(configured);
      }
    }
    if (checked) concrete.add(scopeKey);
    else concrete.delete(scopeKey);
    update.mutate(
      {
        id: docType.id,
        expected_updated_at: docType.updated_at,
        ...toInput(docType),
        claim_scope_keys: [...concrete].sort(),
      },
      { onError: (error) => toast.error(formatError(error)) },
    );
  };

  return (
    <section className="border-t border-border">
      <div className="space-y-1 bg-muted/40 px-5 py-3">
        <SectionLabel as="h3">Claim type matching</SectionLabel>
        <p className="max-w-prose text-xs text-subtle">
          Choose which recognised documents can suggest each claim choice. The
          member still reviews the selection before submitting.
        </p>
      </div>
      <div className="divide-y divide-border border-t border-border">
        {claimTypes.map((type) => (
          <div key={type.key}>
            <div className="bg-muted/20 px-5 py-2.5 text-sm font-semibold text-foreground">
              {type.display_label}
            </div>
            <div className="divide-y divide-border">
              {scopeRows(type).map((scope) => (
                <div
                  key={scope.key}
                  className="grid gap-2 px-5 py-3 sm:grid-cols-[minmax(12rem,0.8fr)_minmax(0,2fr)] sm:items-start"
                >
                  <p className="text-sm text-foreground">{scope.label}</p>
                  <div className="flex flex-wrap gap-x-4 gap-y-2">
                    {docTypes.map((docType) => {
                      const checked = docType.claim_scope_keys.some((pattern) =>
                        scopePatternMatches(pattern, scope.key),
                      );
                      return (
                        <label
                          key={docType.id}
                          className="inline-flex min-h-8 items-center gap-2 text-xs text-foreground"
                        >
                          <Checkbox
                            checked={checked}
                            disabled={busy}
                            onCheckedChange={(value) =>
                              toggle(docType, scope.key, value === true)
                            }
                          />
                          {docType.display}
                        </label>
                      );
                    })}
                    {docTypes.length === 0 && (
                      <span className="text-xs text-subtle">
                        Add a document type below to configure matching.
                      </span>
                    )}
                  </div>
                </div>
              ))}
            </div>
          </div>
        ))}
      </div>
    </section>
  );
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
    <div className="space-y-2">
      {/* Only the label itself takes the uppercase grammar — the hint is a
          sentence, and setting a whole sentence in tracked caps is unreadable. */}
      <p className="flex flex-wrap items-baseline gap-x-1.5">
        <SectionLabel as="span">{label}</SectionLabel>
        {hint && <span className="text-xs text-subtle">— {hint}</span>}
      </p>
      <div className="flex flex-wrap items-center gap-1.5">
        {chips.map((chip, i) => (
          <span
            key={`${chip}-${i}`}
            title={chipTitle?.(chip, i)}
            className="inline-flex h-8 items-center gap-0.5 rounded-md border border-border bg-muted pl-2.5 pr-1 text-xs text-foreground"
          >
            {chip}
            <button
              type="button"
              disabled={disabled}
              onClick={() => onRemove(i)}
              className="grid size-6 place-items-center rounded text-muted-foreground transition-colors hover:bg-card hover:text-error disabled:opacity-50 disabled:hover:bg-transparent"
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
            aria-label={placeholder}
            className="h-8 w-44 text-xs"
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
            aria-label={placeholder}
            className="size-8 shrink-0 p-0"
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

/** One document type, as a row on the card's divided settings rail. It is
 * deliberately NOT its own bordered card — a stack of cards inside a card
 * double-frames every row and eats the padding that separates the groups. */
function DocTypeRow({
  docType,
  fetching,
}: {
  docType: ClaimDocType;
  fetching: boolean;
}) {
  const update = useUpdateClaimDocType();
  const del = useDeleteClaimDocType();
  const [confirmDelete, setConfirmDelete] = useState(false);
  // `fetching` (the list refetching after a save) is part of busy so a rapid
  // second edit can't be built on stale data and overwrite the first.
  const busy = update.isPending || del.isPending || fetching;

  const save = (patch: Partial<ClaimDocTypeInput>) => {
    update.mutate(
      {
        id: docType.id,
        expected_updated_at: docType.updated_at,
        ...toInput(docType),
        ...patch,
      },
      { onError: (e) => toast.error(formatError(e)) },
    );
  };

  return (
    <div className="space-y-5 px-5 py-6">
      <div className="flex items-start justify-between gap-3">
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
          className="-mr-1.5 -mt-1.5 grid size-8 shrink-0 place-items-center rounded-md text-muted-foreground transition-colors hover:bg-muted hover:text-error disabled:opacity-50 disabled:hover:bg-transparent"
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
        chips={docType.key_fields.map(
          (f) => f.name + (f.optional ? " (optional)" : ""),
        )}
        chipTitle={(_, i) => {
          const f = docType.key_fields[i];
          const parts = [];
          if (f?.keywords.length) parts.push(`Matches: ${f.keywords.join(", ")}`);
          if (f?.optional) parts.push("Optional — absence is not warned");
          return parts.length ? parts.join(". ") : undefined;
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

      {/* Hospital sector + the required-document slot this type fills — a
          sectored invoice type is classified govt/private, and the slot drives
          where an autofilled upload of this type lands on the claim form. */}
      <div className="grid max-w-xl gap-x-6 gap-y-4 sm:grid-cols-2">
        <Field label="Hospital sector">
          <NativeSelect
            disabled={busy}
            value={docType.sector ?? ""}
            onChange={(e) =>
              save({
                sector: (e.target.value || null) as "govt" | "private" | null,
              })
            }
          >
            <option value="">Any / not a hospital bill</option>
            <option value="govt">Government hospital</option>
            <option value="private">Private hospital</option>
          </NativeSelect>
        </Field>
        <Field label="Fills document slot">
          <NativeSelect
            disabled={busy}
            value={docType.slot_key ?? ""}
            onChange={(e) => save({ slot_key: e.target.value || null })}
          >
            <option value="">None</option>
            {Object.entries(DOC_TYPE_LABELS).map(([key, label]) => (
              <option key={key} value={key}>
                {label}
              </option>
            ))}
          </NativeSelect>
        </Field>
      </div>

      <AlertDialog
        open={confirmDelete}
        onOpenChange={setConfirmDelete}
        title={`Delete "${docType.display}"?`}
        description="Uploaded documents will no longer be recognised as this type, and its completeness check stops running. Restore defaults brings the seeded types back."
        confirmLabel="Delete"
        loading={del.isPending}
        onConfirm={() =>
          del.mutate({ id: docType.id, expected_updated_at: docType.updated_at }, {
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
  const scopeOptions = useReviewScopeOptions();
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
        claim_scope_keys: [],
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
      <CardHeader className="pb-5">
        {/* basis-80 + shrink-0: without a basis the description absorbs the
            whole row and pushes the action onto its own line, where it reads as
            a step in the description rather than the card's action. */}
        <div className="flex flex-wrap items-start justify-between gap-x-4 gap-y-3">
          <div className="min-w-0 flex-1 basis-80 space-y-1">
            <CardTitle>Claim document types</CardTitle>
            <CardDescription className="max-w-prose">
              How uploads are recognised and which claim choice they can
              suggest. Configure claim matching first, then maintain the shared
              document-recognition library below.
            </CardDescription>
          </div>
          <Button
            type="button"
            variant="outline"
            size="sm"
            className="shrink-0"
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
      {/* p-0 so the hairlines span the card and the rows own their padding —
          the separation between document types is the rule plus real space,
          not a border drawn around each one. */}
      <CardContent className="p-0">
        {docTypes.isLoading || scopeOptions.isLoading ? (
          <div className="px-5 pb-5">
            <Skeleton className="h-40 w-full" />
          </div>
        ) : docTypes.isError || scopeOptions.isError ? (
          <p className="px-5 pb-5 text-sm text-error">
            {formatError(docTypes.error ?? scopeOptions.error)}
          </p>
        ) : (
          <div>
            {scopeOptions.data?.has_current_year ? (
              <ScopeAssignments
                claimTypes={scopeOptions.data.claim_types}
                docTypes={docTypes.data ?? []}
                fetching={docTypes.isFetching}
              />
            ) : (
              <div className="border-t border-border px-5 py-4">
                <NoCurrentYearNotice />
              </div>
            )}
            <div className="space-y-1 border-t border-border bg-muted/40 px-5 py-3">
              <SectionLabel as="h3">Document recognition library</SectionLabel>
              <p className="max-w-prose text-xs text-subtle">
                Aliases identify the document; key fields warn assessors when a
                recognised document appears incomplete.
              </p>
            </div>
            <div className="divide-y divide-border border-t border-border">
            {(docTypes.data ?? []).map((t) => (
              <DocTypeRow key={t.id} docType={t} fetching={docTypes.isFetching} />
            ))}
            {adding ? (
              <div className="flex flex-wrap items-center gap-2 px-5 py-4">
                <Input
                  value={newName}
                  placeholder="Document type name (e.g. Referral Memo)"
                  aria-label="Document type name"
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
              <div className="px-5 py-4">
                <Button
                  type="button"
                  variant="outline"
                  size="sm"
                  onClick={() => setAdding(true)}
                >
                  <Plus className="size-3.5" />
                  <span className="ml-1.5">Add document type</span>
                </Button>
              </div>
            )}
            </div>
          </div>
        )}
      </CardContent>

      <AlertDialog
        open={confirmReset}
        onOpenChange={setConfirmReset}
        title="Restore default document types?"
        description="This discards every customisation (claim matching, added aliases, key fields, and custom types) and restores the seeded defaults."
        confirmLabel="Restore defaults"
        loading={reset.isPending}
        onConfirm={() =>
          reset.mutate(
            Object.fromEntries(
              (docTypes.data ?? []).map((docType) => [docType.id, docType.updated_at]),
            ),
            {
            onSuccess: () => setConfirmReset(false),
            onError: (e) => toast.error(formatError(e)),
            },
          )
        }
      />
    </Card>
  );
}
