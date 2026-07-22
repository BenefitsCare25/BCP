import { useState } from "react";
import { toast } from "sonner";
import { AttributeSchemaEditor } from "@/components/primitives/AttributeSchemaEditor";
import { AlertDialog } from "@/components/ui/alert-dialog";
import { Button } from "@/components/ui/button";
import { SkeletonTable } from "@/components/ui/skeleton";
import { Card, CardContent } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Field, InfoHint } from "@/components/ui/tooltip";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import {
  Sheet,
  SheetBody,
  SheetClose,
  SheetContent,
  SheetFooter,
  SheetHeader,
  SheetTitle,
} from "@/components/ui/sheet";
import { Switch } from "@/components/ui/switch";
import {
  useCreateAttribute,
  useDeleteAttribute,
  useEmployeeAttributes,
  useMe,
  useUpdateAttribute,
  type CatalogScope,
} from "@/api/hooks";
import { ScopeToggle } from "@/components/schema/ScopeToggle";
import { formatError } from "@/lib/errors";
import { PageGuide } from "@/components/ui/page-guide";
import type { AttributeSchema } from "@/types";

const TYPES = ["string", "integer", "decimal", "boolean", "date", "enum"];

interface Draft {
  attribute_id: string;
  display_name: string;
  data_type: string;
  enum_values: string;
  is_required: boolean;
  is_pii: boolean;
  description: string;
}

const EMPTY_DRAFT: Draft = {
  attribute_id: "",
  display_name: "",
  data_type: "string",
  enum_values: "",
  is_required: false,
  is_pii: false,
  description: "",
};

function toDraft(attr: AttributeSchema): Draft {
  return {
    attribute_id: attr.attribute_id,
    display_name: attr.display_name,
    data_type: attr.data_type,
    enum_values: attr.enum_values?.join(", ") ?? "",
    is_required: attr.is_required,
    is_pii: attr.is_pii,
    description: attr.description ?? "",
  };
}

export function SchemaAttributesPage({
  open,
  onOpenChange,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
}) {
  const { data: attrs = [], isLoading } = useEmployeeAttributes();
  const { data: me } = useMe();
  const isAdmin = me?.role === "broker_admin" || me?.role === "system_admin";
  const create = useCreateAttribute();
  const update = useUpdateAttribute();
  const remove = useDeleteAttribute();
  const [scope, setScope] = useState<CatalogScope>("company");
  const [editing, setEditing] = useState<AttributeSchema | null>(null);
  const [draft, setDraft] = useState<Draft>(EMPTY_DRAFT);
  const [deleting, setDeleting] = useState<AttributeSchema | null>(null);

  // "Firm library" shows only the shared (client_id NULL) rows; "This company"
  // shows the effective set (its own rows + inherited firm-library defaults).
  const visible =
    scope === "firm" ? attrs.filter((a) => a.client_id === null) : attrs;
  const firmWriteBlocked = scope === "firm" && !isAdmin;

  const onSheetOpenChange = (next: boolean) => {
    onOpenChange(next);
    if (!next) {
      setDraft(EMPTY_DRAFT);
      setEditing(null);
    }
  };

  const beginEdit = (attr: AttributeSchema) => {
    setEditing(attr);
    setDraft(toDraft(attr));
    onOpenChange(true);
  };

  const submit = async () => {
    const enumValues =
      draft.data_type === "enum"
        ? draft.enum_values
            .split(",")
            .map((s) => s.trim())
            .filter(Boolean)
        : null;
    try {
      if (editing) {
        await update.mutateAsync({
          id: editing.id,
          patch: {
            display_name: draft.display_name,
            data_type: draft.data_type,
            enum_values: enumValues,
            is_required: draft.is_required,
            is_pii: draft.is_pii,
            description: draft.description || null,
          },
        });
        toast.success(`Updated ${draft.display_name}`);
      } else {
        await create.mutateAsync({
          payload: {
            attribute_id: draft.attribute_id,
            display_name: draft.display_name,
            data_type: draft.data_type,
            enum_values: enumValues,
            is_required: draft.is_required,
            is_pii: draft.is_pii,
            description: draft.description || null,
          },
          scope,
        });
        toast.success(`Added ${draft.display_name}`);
      }
      onOpenChange(false);
    } catch (err) {
      toast.error(formatError(err));
    }
  };

  return (
    <div className="space-y-4 max-w-7xl">
      <Sheet open={open} onOpenChange={onSheetOpenChange}>
          <SheetContent side="right">
            <SheetHeader>
              <div className="flex items-center gap-1.5">
                <SheetTitle>
                  {editing
                    ? "Edit attribute"
                    : scope === "firm"
                      ? "Add firm-library attribute"
                      : "Add company attribute"}
                </SheetTitle>
                <InfoHint>
                  {editing
                    ? editing.client_id === null
                      ? "Editing a firm-library default — changes apply to every company."
                      : "Updates this attribute for the current company only."
                    : scope === "firm"
                      ? "Adds a shared default visible to every company (admin-only)."
                      : "Adds an attribute scoped to the current company."}
                </InfoHint>
              </div>
            </SheetHeader>
            <SheetBody className="space-y-4">
              {!editing && firmWriteBlocked && (
                <p className="rounded-md border border-warn/40 bg-warn-soft px-2.5 py-2 text-sm text-warn">
                  Only firm admins can add firm-library defaults. Switch to “This
                  company” to add an attribute for the current company.
                </p>
              )}
              <div className="grid grid-cols-2 gap-3">
                <Field
                  label="Attribute ID"
                  hint="Stable machine key referenced by matching rules and roster columns — lowercase, no spaces. Can't change after creation."
                >
                  <Input
                    value={draft.attribute_id}
                    onChange={(e) =>
                      setDraft({ ...draft, attribute_id: e.target.value })
                    }
                    placeholder="site_location"
                    disabled={Boolean(editing)}
                  />
                </Field>
                <Field label="Display name">
                  <Input
                    value={draft.display_name}
                    onChange={(e) =>
                      setDraft({ ...draft, display_name: e.target.value })
                    }
                    placeholder="Site Location"
                  />
                </Field>
              </div>
              <Field
                label="Data type"
                hint="How the value is stored and validated. Enum shows a fixed dropdown of allowed values."
              >
                <Select
                  value={draft.data_type}
                  onValueChange={(v) => setDraft({ ...draft, data_type: v })}
                >
                  <SelectTrigger>
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    {TYPES.map((t) => (
                      <SelectItem key={t} value={t}>
                        {t}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </Field>
              {draft.data_type === "enum" && (
                <Field
                  label="Enum values"
                  hint="Comma-separated list of the allowed values."
                >
                  <Input
                    value={draft.enum_values}
                    onChange={(e) =>
                      setDraft({ ...draft, enum_values: e.target.value })
                    }
                    placeholder="SG, MY, ID"
                  />
                </Field>
              )}
              <Field label="Description">
                <Input
                  value={draft.description}
                  onChange={(e) =>
                    setDraft({ ...draft, description: e.target.value })
                  }
                />
              </Field>
              <div className="flex items-center justify-between rounded-md border border-border p-3">
                <div className="flex items-center gap-1">
                  <div className="text-sm font-medium">Required</div>
                  <InfoHint>Block uploads with missing values.</InfoHint>
                </div>
                <Switch
                  checked={draft.is_required}
                  onCheckedChange={(v) =>
                    setDraft({ ...draft, is_required: v })
                  }
                />
              </div>
              <div className="flex items-center justify-between rounded-md border border-border p-3">
                <div className="flex items-center gap-1">
                  <div className="text-sm font-medium">PII</div>
                  <InfoHint>
                    Redact for non-PII-cleared roles (PDPA).
                  </InfoHint>
                </div>
                <Switch
                  checked={draft.is_pii}
                  onCheckedChange={(v) => setDraft({ ...draft, is_pii: v })}
                />
              </div>
            </SheetBody>
            <SheetFooter>
              <SheetClose asChild>
                <Button variant="outline">Cancel</Button>
              </SheetClose>
              <Button
                onClick={submit}
                disabled={
                  !draft.attribute_id ||
                  !draft.display_name ||
                  (!editing && firmWriteBlocked) ||
                  create.isPending ||
                  update.isPending
                }
              >
                {editing ? "Save changes" : "Save attribute"}
              </Button>
            </SheetFooter>
          </SheetContent>
        </Sheet>

      <ScopeToggle
        scope={scope}
        onScopeChange={setScope}
        canWriteFirm={isAdmin}
      />

      <Card>
        <CardContent className="pt-6">
          {isLoading ? (
            <SkeletonTable rows={6} columns={5} />
          ) : visible.length === 0 ? (
            <p className="py-6 text-center text-sm text-muted-foreground">
              {scope === "firm"
                ? "No firm-library attributes yet."
                : "No attributes yet."}
            </p>
          ) : (
            <AttributeSchemaEditor
              attributes={visible}
              onEdit={beginEdit}
              onDelete={setDeleting}
              // Firm-library defaults are shared across companies — admin-only.
              lockRow={(a) => a.client_id === null && !isAdmin}
            />
          )}
        </CardContent>
      </Card>

      <PageGuide
        purpose="Define the shape of an employee record — each attribute becomes a column in the roster. Global defaults cover standard Singapore fields; add client-specific attributes for custom data."
        connections={[
          { label: "→ Roster profiling", description: "AI derives attribute values from uploaded roster columns" },
          { label: "→ Categories", description: "Matching rules reference these attributes to assign employees to benefit categories" },
          { label: "→ Products catalog", description: "Products define what insurance lines exist; attributes define who qualifies" },
        ]}
      />

      <AlertDialog
        open={Boolean(deleting)}
        onOpenChange={(o) => !o && setDeleting(null)}
        title={`Delete attribute ${deleting?.display_name ?? ""}?`}
        description={
          <>
            The attribute <code>{deleting?.attribute_id}</code> will be removed
            from this client. Any employee data already uploaded retains the
            raw value but the attribute disappears from the schema. This cannot
            be undone.
          </>
        }
        confirmLabel="Delete attribute"
        loading={remove.isPending}
        onConfirm={async () => {
          if (!deleting) return;
          try {
            await remove.mutateAsync(deleting.id);
            toast.success(`Deleted ${deleting.display_name}`);
            setDeleting(null);
          } catch (err) {
            toast.error(formatError(err));
          }
        }}
      />
    </div>
  );
}
