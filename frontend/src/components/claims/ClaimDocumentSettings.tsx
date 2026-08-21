import { useEffect, useMemo, useState } from "react";
import {
  AlertTriangle,
  ChevronDown,
  ChevronUp,
  Copy,
  FileText,
  Loader2,
  Pencil,
  Plus,
  Search,
  Trash2,
  X,
} from "lucide-react";
import { toast } from "sonner";
import {
  useClaimDocumentSetups,
  useDuplicateClaimDocumentSetup,
  useSaveClaimDocumentSetup,
  type ClaimDocumentSetup,
  type ClaimDocumentSetupInput,
  type ClaimSetupDocument,
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
import { NativeSelect } from "@/components/ui/native-select";
import { SectionLabel } from "@/components/ui/section-label";
import {
  Sheet,
  SheetBody,
  SheetContent,
  SheetDescription,
  SheetFooter,
  SheetHeader,
  SheetTitle,
} from "@/components/ui/sheet";
import { Skeleton } from "@/components/ui/skeleton";
import { formatError } from "@/lib/errors";

function setupInput(
  setup: ClaimDocumentSetup,
  documents = setup.documents,
): ClaimDocumentSetupInput {
  return {
    claim_kind: setup.claim_kind,
    claim_key: setup.claim_key,
    scope_code: setup.scope_code,
    display_label: setup.display_label,
    documents,
    expected_updated_at: setup.updated_at,
  };
}

function copyDocuments(documents: ClaimSetupDocument[]): ClaimSetupDocument[] {
  return documents.map((document) => ({
    ...document,
    aliases: [...document.aliases],
    key_fields: document.key_fields.map((field) => ({
      ...field,
      keywords: [...field.keywords],
    })),
  }));
}

function uniqueDocumentKey(documents: ClaimSetupDocument[]): string {
  const base = `document_${crypto.randomUUID().replaceAll("-", "").slice(0, 12)}`;
  return documents.some((document) => document.key === base)
    ? `document_${Date.now().toString(36)}`.slice(0, 32)
    : base;
}

function TokenField({
  label,
  hint,
  values,
  placeholder,
  onChange,
}: {
  label: string;
  hint: string;
  values: string[];
  placeholder: string;
  onChange: (values: string[]) => void;
}) {
  const [value, setValue] = useState("");
  const add = () => {
    const next = value.trim();
    if (!next) return;
    if (!values.some((item) => item.toLowerCase() === next.toLowerCase())) {
      onChange([...values, next]);
    }
    setValue("");
  };
  return (
    <div className="space-y-2">
      <div>
        <SectionLabel as="span">{label}</SectionLabel>
        <p className="mt-0.5 text-xs text-subtle">{hint}</p>
      </div>
      <div className="flex flex-wrap items-center gap-1.5">
        {values.map((item, index) => (
          <span
            key={`${item}-${index}`}
            className="inline-flex min-h-8 items-center gap-1 rounded-md border border-border bg-muted pl-2.5 pr-1 text-xs text-foreground"
          >
            {item}
            <button
              type="button"
              className="grid size-6 place-items-center rounded text-muted-foreground hover:bg-card hover:text-error focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
              aria-label={`Remove ${item}`}
              onClick={() => onChange(values.filter((_, i) => i !== index))}
            >
              <X className="size-3" aria-hidden />
            </button>
          </span>
        ))}
        <div className="flex items-center gap-1">
          <Input
            value={value}
            className="h-8 w-48 text-xs"
            placeholder={placeholder}
            aria-label={placeholder}
            maxLength={128}
            onChange={(event) => setValue(event.target.value)}
            onKeyDown={(event) => {
              if (event.key === "Enter") {
                event.preventDefault();
                add();
              }
            }}
          />
          <Button
            type="button"
            variant="ghost"
            size="sm"
            className="size-8 p-0"
            disabled={!value.trim()}
            aria-label={`Add ${label.toLowerCase()}`}
            onClick={add}
          >
            <Plus className="size-3.5" aria-hidden />
          </Button>
        </div>
      </div>
    </div>
  );
}

function KeywordInput({
  values,
  label,
  onChange,
}: {
  values: string[];
  label: string;
  onChange: (values: string[]) => void;
}) {
  const [value, setValue] = useState(values.join(", "));
  useEffect(() => setValue(values.join(", ")), [values]);

  const commit = () => {
    const next = Array.from(
      new Set(
        value
          .split(",")
          .map((item) => item.trim())
          .filter(Boolean),
      ),
    );
    onChange(next);
    setValue(next.join(", "));
  };

  return (
    <Input
      value={value}
      placeholder="Matching terms, comma separated"
      aria-label={label}
      onChange={(event) => setValue(event.target.value)}
      onBlur={commit}
      onKeyDown={(event) => {
        if (event.key === "Enter") {
          event.preventDefault();
          commit();
        }
      }}
    />
  );
}

function DocumentEditor({
  document,
  index,
  total,
  onChange,
  onRemove,
  onMove,
}: {
  document: ClaimSetupDocument;
  index: number;
  total: number;
  onChange: (document: ClaimSetupDocument) => void;
  onRemove: () => void;
  onMove: (direction: -1 | 1) => void;
}) {
  const addKeyField = () =>
    onChange({
      ...document,
      key_fields: [
        ...document.key_fields,
        { name: "", keywords: [], optional: false },
      ],
    });

  return (
    <section className="space-y-5 px-4 py-5 sm:px-5" aria-labelledby={`${document.id}-title`}>
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <p className="text-xs font-medium text-subtle">Required document {index + 1}</p>
          <h3 id={`${document.id}-title`} className="truncate text-sm font-semibold text-foreground">
            {document.display.trim() || "Untitled document"}
          </h3>
        </div>
        <div className="flex shrink-0 items-center gap-0.5">
          <Button
            type="button"
            variant="ghost"
            size="sm"
            className="size-8 p-0"
            disabled={index === 0}
            aria-label={`Move ${document.display || "document"} up`}
            onClick={() => onMove(-1)}
          >
            <ChevronUp className="size-3.5" aria-hidden />
          </Button>
          <Button
            type="button"
            variant="ghost"
            size="sm"
            className="size-8 p-0"
            disabled={index === total - 1}
            aria-label={`Move ${document.display || "document"} down`}
            onClick={() => onMove(1)}
          >
            <ChevronDown className="size-3.5" aria-hidden />
          </Button>
          <Button
            type="button"
            variant="ghost"
            size="sm"
            className="size-8 p-0 text-muted-foreground hover:text-error"
            aria-label={`Remove ${document.display || "document"}`}
            onClick={onRemove}
          >
            <Trash2 className="size-3.5" aria-hidden />
          </Button>
        </div>
      </div>

      <div className="grid gap-4 sm:grid-cols-2">
        <label className="space-y-1.5">
          <SectionLabel as="span">Document name</SectionLabel>
          <Input
            value={document.display}
            maxLength={128}
            placeholder="e.g. Discharge summary"
            onChange={(event) => onChange({ ...document, display: event.target.value })}
          />
        </label>
        <label className="space-y-1.5">
          <SectionLabel as="span">Member instructions</SectionLabel>
          <Input
            value={document.instructions ?? ""}
            maxLength={240}
            placeholder="What the member should attach"
            onChange={(event) =>
              onChange({ ...document, instructions: event.target.value || null })
            }
          />
        </label>
      </div>

      <TokenField
        label="Recognition aliases"
        hint="Other titles that may be printed on this document."
        values={document.aliases}
        placeholder="Add an alias"
        onChange={(aliases) => onChange({ ...document, aliases })}
      />

      <div className="space-y-2">
        <div className="flex flex-wrap items-end justify-between gap-2">
          <div>
            <SectionLabel as="span">Recognition key fields</SectionLabel>
            <p className="mt-0.5 text-xs text-subtle">
              Fields expected on a genuine copy of this document.
            </p>
          </div>
          <Button type="button" variant="outline" size="sm" onClick={addKeyField}>
            <Plus className="size-3.5" aria-hidden />
            <span className="ml-1">Add key field</span>
          </Button>
        </div>
        {document.key_fields.length > 0 ? (
          <div className="divide-y divide-border rounded-md border border-border">
            {document.key_fields.map((field, fieldIndex) => (
              <div
                key={`${document.id}-field-${fieldIndex}`}
                className="grid gap-2 p-3 sm:grid-cols-[minmax(0,1fr)_minmax(0,1.2fr)_auto_auto] sm:items-center"
              >
                <Input
                  value={field.name}
                  maxLength={128}
                  placeholder="Field name"
                  aria-label={`Key field ${fieldIndex + 1} name`}
                  onChange={(event) => {
                    const key_fields = document.key_fields.map((item, i) =>
                      i === fieldIndex ? { ...item, name: event.target.value } : item,
                    );
                    onChange({ ...document, key_fields });
                  }}
                />
                <KeywordInput
                  values={field.keywords}
                  label={`Key field ${fieldIndex + 1} matching terms`}
                  onChange={(keywords) => {
                    const key_fields = document.key_fields.map((item, i) =>
                      i === fieldIndex ? { ...item, keywords } : item,
                    );
                    onChange({ ...document, key_fields });
                  }}
                />
                <Button
                  type="button"
                  variant="ghost"
                  size="sm"
                  className="justify-start sm:justify-center"
                  onClick={() => {
                    const key_fields = document.key_fields.map((item, i) =>
                      i === fieldIndex ? { ...item, optional: !item.optional } : item,
                    );
                    onChange({ ...document, key_fields });
                  }}
                >
                  {field.optional ? "Optional" : "Required"}
                </Button>
                <Button
                  type="button"
                  variant="ghost"
                  size="sm"
                  className="size-8 p-0 text-muted-foreground hover:text-error"
                  aria-label={`Remove ${field.name || `key field ${fieldIndex + 1}`}`}
                  onClick={() =>
                    onChange({
                      ...document,
                      key_fields: document.key_fields.filter((_, i) => i !== fieldIndex),
                    })
                  }
                >
                  <Trash2 className="size-3.5" aria-hidden />
                </Button>
              </div>
            ))}
          </div>
        ) : (
          <p className="rounded-md bg-muted/50 px-3 py-2.5 text-xs text-subtle">
            No completeness fields configured. The document can still be recognised by its aliases.
          </p>
        )}
      </div>
    </section>
  );
}

function SetupEditor({
  setup,
  onClose,
}: {
  setup: ClaimDocumentSetup | null;
  onClose: () => void;
}) {
  const save = useSaveClaimDocumentSetup();
  const [documents, setDocuments] = useState<ClaimSetupDocument[]>([]);
  const [confirmEmpty, setConfirmEmpty] = useState(false);
  const [confirmDiscard, setConfirmDiscard] = useState(false);

  useEffect(() => {
    setDocuments(setup ? copyDocuments(setup.documents) : []);
  }, [setup]);

  const dirty = setup
    ? JSON.stringify(documents) !== JSON.stringify(setup.documents)
    : false;

  const commit = () => {
    if (!setup) return;
    const invalid = documents.find((document) => !document.display.trim());
    if (invalid) {
      toast.error("Name every required document before saving.");
      return;
    }
    const normalized = documents.map((document) => ({
      ...document,
      display: document.display.trim(),
      instructions: document.instructions?.trim() || null,
      aliases:
        document.aliases.length > 0
          ? document.aliases
          : [document.display.trim()],
      key_fields: document.key_fields.filter((field) => field.name.trim()),
    }));
    save.mutate(setupInput(setup, normalized), {
      onSuccess: () => {
        toast.success(`Saved document setup for ${setup.display_label}`);
        setConfirmEmpty(false);
        onClose();
      },
      onError: (error) => toast.error(formatError(error)),
    });
  };

  const requestClose = () => {
    if (dirty && !save.isPending) setConfirmDiscard(true);
    else onClose();
  };

  return (
    <>
      <Sheet open={setup !== null} onOpenChange={(open) => !open && requestClose()}>
        <SheetContent className="sm:max-w-3xl">
          <SheetHeader>
            <SheetTitle>{setup?.display_label ?? "Document setup"}</SheetTitle>
            <SheetDescription>
              {setup?.product_label}. Required uploads and recognition rules here are private to this claim type.
            </SheetDescription>
          </SheetHeader>
          <SheetBody className="space-y-6 px-0">
            <div className="flex flex-wrap items-center justify-between gap-3 px-6">
              <div>
                <SectionLabel as="h3">Required documents</SectionLabel>
                <p className="mt-1 text-sm text-muted-foreground">
                  Members must attach every document below before submitting.
                </p>
              </div>
              <Badge variant={documents.length > 0 ? "outline" : "warn"}>
                {documents.length} required
              </Badge>
            </div>

            {documents.length > 0 ? (
              <div className="divide-y divide-border border-y border-border">
                {documents.map((document, index) => (
                  <DocumentEditor
                    key={document.id}
                    document={document}
                    index={index}
                    total={documents.length}
                    onChange={(next) =>
                      setDocuments((current) =>
                        current.map((item) => (item.id === document.id ? next : item)),
                      )
                    }
                    onRemove={() =>
                      setDocuments((current) =>
                        current.filter((item) => item.id !== document.id),
                      )
                    }
                    onMove={(direction) =>
                      setDocuments((current) => {
                        const next = [...current];
                        const destination = index + direction;
                        [next[index], next[destination]] = [next[destination], next[index]];
                        return next;
                      })
                    }
                  />
                ))}
              </div>
            ) : (
              <div className="mx-6 flex gap-3 rounded-md border border-warn/40 bg-warn-soft p-4 text-sm text-warn">
                <AlertTriangle className="mt-0.5 size-4 shrink-0" aria-hidden />
                <p>Members will be able to submit this claim type without attaching documents.</p>
              </div>
            )}

            <div className="px-6">
              <Button
                type="button"
                variant="outline"
                onClick={() =>
                  setDocuments((current) => [
                    ...current,
                    {
                      id: crypto.randomUUID(),
                      key: uniqueDocumentKey(current),
                      display: "",
                      instructions: null,
                      aliases: [],
                      key_fields: [],
                    },
                  ])
                }
              >
                <Plus className="size-4" aria-hidden />
                <span className="ml-1.5">Add required document</span>
              </Button>
            </div>

            <section className="mx-6 space-y-3 rounded-lg border border-border bg-muted/30 p-4">
              <div>
                <SectionLabel as="h3">Member preview</SectionLabel>
                <p className="mt-1 text-xs text-subtle">How the requirement list will appear before files are attached.</p>
              </div>
              <div className="flex items-center justify-between gap-3">
                <p className="text-sm font-semibold text-foreground">Documents for this claim</p>
                <span className="text-xs text-subtle">0 of {documents.length} attached</span>
              </div>
              {documents.length > 0 ? (
                <ul className="divide-y divide-border">
                  {documents.map((document) => (
                    <li key={document.id} className="flex gap-2 py-2.5 text-sm">
                      <span className="mt-0.5 size-4 shrink-0 rounded-full border border-input" aria-hidden />
                      <span>
                        <span className="block font-medium text-foreground">
                          {document.display.trim() || "Untitled document"}
                        </span>
                        {document.instructions && (
                          <span className="block text-xs text-subtle">{document.instructions}</span>
                        )}
                      </span>
                    </li>
                  ))}
                </ul>
              ) : (
                <p className="text-sm text-subtle">No documents required.</p>
              )}
            </section>
          </SheetBody>
          <SheetFooter>
            <Button type="button" variant="ghost" disabled={save.isPending} onClick={requestClose}>
              Cancel
            </Button>
            <Button
              type="button"
              disabled={!dirty || save.isPending}
              onClick={() => (documents.length === 0 ? setConfirmEmpty(true) : commit())}
            >
              {save.isPending && <Loader2 className="size-4 animate-spin" aria-hidden />}
              <span className={save.isPending ? "ml-1.5" : undefined}>Save setup</span>
            </Button>
          </SheetFooter>
        </SheetContent>
      </Sheet>

      <AlertDialog
        open={confirmEmpty}
        onOpenChange={setConfirmEmpty}
        title="Allow claims without documents?"
        description="Members will be able to submit this claim type without attaching any evidence. The AI review will also have no required submission documents to check."
        confirmLabel="Save without documents"
        loading={save.isPending}
        onConfirm={commit}
      />
      <AlertDialog
        open={confirmDiscard}
        onOpenChange={setConfirmDiscard}
        title="Discard unsaved changes?"
        description="The document requirements and recognition changes in this editor have not been saved."
        confirmLabel="Discard changes"
        onConfirm={() => {
          setConfirmDiscard(false);
          onClose();
        }}
      />
    </>
  );
}

function DuplicateEditor({
  source,
  setups,
  onClose,
}: {
  source: ClaimDocumentSetup | null;
  setups: ClaimDocumentSetup[];
  onClose: () => void;
}) {
  const duplicate = useDuplicateClaimDocumentSetup();
  const [targetKey, setTargetKey] = useState("");
  useEffect(() => setTargetKey(""), [source]);
  const target = setups.find((setup) => setup.scope_key === targetKey) ?? null;
  return (
    <Sheet open={source !== null} onOpenChange={(open) => !open && !duplicate.isPending && onClose()}>
      <SheetContent className="sm:max-w-md">
        <SheetHeader>
          <SheetTitle>Duplicate document setup</SheetTitle>
          <SheetDescription>
            Copy required documents and their recognition libraries. The copy becomes fully independent.
          </SheetDescription>
        </SheetHeader>
        <SheetBody className="space-y-5">
          <div className="rounded-md bg-muted/50 p-3 text-sm">
            Copy <strong className="text-foreground">{source?.display_label}</strong>
          </div>
          <label className="space-y-1.5">
            <SectionLabel as="span">Destination claim type</SectionLabel>
            <NativeSelect value={targetKey} onChange={(event) => setTargetKey(event.target.value)}>
              <option value="">Select a destination</option>
              {setups
                .filter((setup) => setup.scope_key !== source?.scope_key)
                .map((setup) => (
                  <option key={setup.scope_key} value={setup.scope_key}>
                    {setup.product_label} — {setup.group_label ? `${setup.group_label} — ` : ""}{setup.display_label}
                  </option>
                ))}
            </NativeSelect>
          </label>
          {target && (
            <div className="space-y-2">
              <p className="text-xs font-medium text-subtle">
                The current {target.documents.length} document{target.documents.length === 1 ? "" : "s"} for {target.display_label} will be replaced.
              </p>
              <ul className="space-y-1 text-sm text-foreground">
                {source?.documents.map((document) => (
                  <li key={document.id} className="flex items-center gap-2">
                    <FileText className="size-3.5 text-muted-foreground" aria-hidden />
                    {document.display}
                  </li>
                ))}
              </ul>
            </div>
          )}
        </SheetBody>
        <SheetFooter>
          <Button type="button" variant="ghost" disabled={duplicate.isPending} onClick={onClose}>
            Cancel
          </Button>
          <Button
            type="button"
            disabled={!source || !target || duplicate.isPending}
            onClick={() => {
              if (!source || !target) return;
              duplicate.mutate(
                { source_scope_key: source.scope_key, target: setupInput(target) },
                {
                  onSuccess: () => {
                    toast.success(`Copied document setup into ${target.display_label}`);
                    onClose();
                  },
                  onError: (error) => toast.error(formatError(error)),
                },
              );
            }}
          >
            {duplicate.isPending ? (
              <Loader2 className="size-4 animate-spin" aria-hidden />
            ) : (
              <Copy className="size-4" aria-hidden />
            )}
            <span className="ml-1.5">Duplicate setup</span>
          </Button>
        </SheetFooter>
      </SheetContent>
    </Sheet>
  );
}

function SetupRow({
  setup,
  onEdit,
  onDuplicate,
}: {
  setup: ClaimDocumentSetup;
  onEdit: () => void;
  onDuplicate: () => void;
}) {
  return (
    <div className="grid gap-3 px-5 py-4 sm:grid-cols-[minmax(12rem,0.8fr)_minmax(0,1.5fr)_auto] sm:items-center">
      <div className={setup.group_code ? "sm:pl-4" : undefined}>
        <p className="text-sm font-medium text-foreground">{setup.display_label}</p>
        <div className="mt-1 flex flex-wrap items-center gap-1.5">
          <Badge variant={setup.is_default ? "outline" : "info"}>
            {setup.is_default ? "System default" : "Custom"}
          </Badge>
          <span className="text-xs text-subtle">
            {setup.documents.length} required
          </span>
        </div>
      </div>
      <div>
        {setup.documents.length > 0 ? (
          <ul className="flex flex-wrap gap-1.5" aria-label={`Required documents for ${setup.display_label}`}>
            {setup.documents.map((document) => (
              <li
                key={document.id}
                className="inline-flex min-h-8 items-center gap-1.5 rounded-md border border-border bg-muted px-2.5 text-xs text-foreground"
              >
                <FileText className="size-3.5 text-muted-foreground" aria-hidden />
                {document.display}
              </li>
            ))}
          </ul>
        ) : (
          <span className="inline-flex items-center gap-1.5 text-xs font-medium text-warn">
            <AlertTriangle className="size-3.5" aria-hidden /> No documents required
          </span>
        )}
      </div>
      <div className="flex flex-wrap gap-1 sm:justify-end">
        <Button type="button" variant="ghost" size="sm" onClick={onDuplicate}>
          <Copy className="size-3.5" aria-hidden />
          <span className="ml-1">Duplicate</span>
        </Button>
        <Button type="button" variant="outline" size="sm" onClick={onEdit}>
          <Pencil className="size-3.5" aria-hidden />
          <span className="ml-1">Edit</span>
        </Button>
      </div>
    </div>
  );
}

export function ClaimDocumentSettings() {
  const setups = useClaimDocumentSetups();
  const [query, setQuery] = useState("");
  const [editing, setEditing] = useState<ClaimDocumentSetup | null>(null);
  const [duplicating, setDuplicating] = useState<ClaimDocumentSetup | null>(null);

  const filtered = useMemo(() => {
    const needle = query.trim().toLowerCase();
    if (!needle) return setups.data ?? [];
    return (setups.data ?? []).filter((setup) =>
      [
        setup.product_label,
        setup.group_label,
        setup.display_label,
        ...setup.documents.map((document) => document.display),
      ].some((value) => value?.toLowerCase().includes(needle)),
    );
  }, [query, setups.data]);

  const groups = Array.from(new Set(filtered.map((setup) => setup.product_label)));

  return (
    <Card>
      <CardHeader className="pb-5">
        <div className="flex flex-wrap items-start justify-between gap-4">
          <div className="min-w-0 flex-1 basis-80 space-y-1">
            <CardTitle>Required documents by claim type</CardTitle>
            <CardDescription className="max-w-prose">
              Every claim choice owns its required uploads and recognition library. Changes apply to new claims; existing claims keep the requirements they started with.
            </CardDescription>
          </div>
          {(setups.data?.length ?? 0) > 6 && (
            <label className="relative w-full sm:w-72">
              <Search className="pointer-events-none absolute left-3 top-1/2 size-4 -translate-y-1/2 text-muted-foreground" aria-hidden />
              <span className="sr-only">Search claim types or documents</span>
              <Input
                type="search"
                value={query}
                className="pl-9"
                placeholder="Search claim types or documents"
                onChange={(event) => setQuery(event.target.value)}
              />
            </label>
          )}
        </div>
      </CardHeader>
      <CardContent className="p-0">
        {setups.isLoading ? (
          <div className="space-y-3 px-5 pb-5">
            <Skeleton className="h-16 w-full" />
            <Skeleton className="h-16 w-full" />
            <Skeleton className="h-16 w-full" />
          </div>
        ) : setups.isError ? (
          <div className="flex items-start justify-between gap-4 px-5 pb-5">
            <p className="text-sm text-error">Couldn&apos;t load document settings. {formatError(setups.error)}</p>
            <Button type="button" variant="outline" size="sm" onClick={() => void setups.refetch()}>
              Retry
            </Button>
          </div>
        ) : groups.length === 0 ? (
          <div className="px-5 pb-6 text-sm text-muted-foreground">
            {query
              ? "No claim types or documents match your search."
              : "No claim types are available. Configure a current benefit year with member-claimable products first."}
          </div>
        ) : (
          <div className="border-t border-border">
            {groups.map((group) => {
              const rows = filtered.filter((setup) => setup.product_label === group);
              const sectorGroups = Array.from(
                new Set(rows.map((setup) => setup.group_label).filter(Boolean)),
              );
              const ungrouped = rows.filter((setup) => !setup.group_label);
              return (
                <section key={group} aria-labelledby={`document-group-${group.replaceAll(" ", "-")}`}>
                  <div className="bg-muted/40 px-5 py-3">
                    <SectionLabel as="h3" id={`document-group-${group.replaceAll(" ", "-")}`}>
                      {group}
                    </SectionLabel>
                  </div>
                  {ungrouped.length > 0 && (
                    <div className="divide-y divide-border border-t border-border">
                      {ungrouped.map((setup) => (
                        <SetupRow key={setup.scope_key} setup={setup} onEdit={() => setEditing(setup)} onDuplicate={() => setDuplicating(setup)} />
                      ))}
                    </div>
                  )}
                  {sectorGroups.map((sector) => (
                    <div key={sector}>
                      <div className="border-t border-border bg-muted/15 px-5 py-2.5 text-sm font-semibold text-foreground">
                        {sector}
                      </div>
                      <div className="divide-y divide-border border-t border-border">
                        {rows
                          .filter((setup) => setup.group_label === sector)
                          .map((setup) => (
                            <SetupRow key={setup.scope_key} setup={setup} onEdit={() => setEditing(setup)} onDuplicate={() => setDuplicating(setup)} />
                          ))}
                      </div>
                    </div>
                  ))}
                </section>
              );
            })}
          </div>
        )}
      </CardContent>

      <SetupEditor setup={editing} onClose={() => setEditing(null)} />
      <DuplicateEditor
        source={duplicating}
        setups={setups.data ?? []}
        onClose={() => setDuplicating(null)}
      />
    </Card>
  );
}
