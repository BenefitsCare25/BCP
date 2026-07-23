import { useState } from "react";
import { Pencil, Trash2 } from "lucide-react";
import { toast } from "sonner";
import {
  useCreateProduct,
  useDeleteProduct,
  useMe,
  useProducts,
  useUpdateProduct,
  type CatalogScope,
  type ProductPayload,
} from "@/api/hooks";
import { AlertDialog } from "@/components/ui/alert-dialog";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Globe } from "lucide-react";
import { Input } from "@/components/ui/input";
import { ScopeToggle } from "@/components/schema/ScopeToggle";
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
import { SkeletonTable } from "@/components/ui/skeleton";
import { Switch } from "@/components/ui/switch";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { formatError } from "@/lib/errors";
import { InsurerSelect } from "@/components/configuration/InsurerSelect";
import { PageGuide } from "@/components/ui/page-guide";
import type { Product } from "@/types";

const PARTICIPATION_MODELS: ProductPayload["participation_model"][] = [
  "standard",
  "extended",
  "eo_only",
];

const EMPTY_DRAFT: ProductPayload = {
  code: "",
  display_name: "",
  insurer: "",
  participation_model: "standard",
  has_dependants: false,
  is_outpatient: false,
  report_code: "",
};

function toDraft(p: Product): ProductPayload {
  return {
    code: p.code,
    display_name: p.display_name,
    insurer: p.insurer ?? "",
    participation_model:
      (p.participation_model as ProductPayload["participation_model"]) ??
      "standard",
    has_dependants: p.has_dependants,
    is_outpatient: p.is_outpatient,
    report_code: p.report_code ?? "",
  };
}

export function SchemaProductsPage({
  open,
  onOpenChange,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
}) {
  const { data: products = [], isLoading } = useProducts();
  const { data: me } = useMe();
  const isAdmin = me?.role === "broker_admin" || me?.role === "system_admin";
  const create = useCreateProduct();
  const update = useUpdateProduct();
  const remove = useDeleteProduct();
  const [scope, setScope] = useState<CatalogScope>("company");
  const [editing, setEditing] = useState<Product | null>(null);
  const [draft, setDraft] = useState<ProductPayload>(EMPTY_DRAFT);
  const [deleting, setDeleting] = useState<Product | null>(null);

  // "Firm library" shows only the shared (client_id NULL) rows; "This company"
  // shows the effective set the company uses (its own rows + inherited globals).
  const visible =
    scope === "firm" ? products.filter((p) => p.client_id === null) : products;
  const firmWriteBlocked = scope === "firm" && !isAdmin;

  const onSheetOpenChange = (next: boolean) => {
    onOpenChange(next);
    if (!next) {
      setEditing(null);
      setDraft(EMPTY_DRAFT);
    }
  };

  const beginEdit = (p: Product) => {
    setEditing(p);
    setDraft(toDraft(p));
    onOpenChange(true);
  };

  const submit = async () => {
    const payload: ProductPayload = {
      ...draft,
      insurer: draft.insurer?.trim() ? draft.insurer.trim() : null,
      report_code: draft.report_code?.trim() ? draft.report_code.trim() : null,
    };
    try {
      if (editing) {
        await update.mutateAsync({ id: editing.id, patch: payload });
        toast.success(`Updated ${payload.display_name}`);
      } else {
        await create.mutateAsync({ payload, scope });
        toast.success(`Added ${payload.display_name}`);
      }
      onOpenChange(false);
    } catch (err) {
      toast.error(formatError(err));
    }
  };

  return (
    <div className="space-y-4">
      <Sheet open={open} onOpenChange={onSheetOpenChange}>
          <SheetContent side="right">
            <SheetHeader>
              <div className="flex items-center gap-1.5">
                <SheetTitle>
                  {editing
                    ? "Edit product"
                    : scope === "firm"
                      ? "Add firm-library product"
                      : "Add company product"}
                </SheetTitle>
                <InfoHint>
                  {editing
                    ? editing.client_id === null
                      ? "Editing a firm-library default — changes apply to every company."
                      : "Updates this product for the current company only."
                    : scope === "firm"
                      ? "Adds a shared default visible to every company (admin-only)."
                      : "Adds a product scoped to the current company."}
                </InfoHint>
              </div>
            </SheetHeader>
            <SheetBody className="space-y-4">
              {!editing && firmWriteBlocked && (
                <p className="rounded-md border border-warn/40 bg-warn-soft px-2.5 py-2 text-sm text-warn">
                  Only firm admins can add firm-library defaults. Switch to “This
                  company” to add a product for the current company.
                </p>
              )}
              <div className="grid grid-cols-2 gap-3">
                <Field
                  label="Code"
                  hint="Short uppercase product code (e.g. GHS, GTL). Links categories and matches placement-slip sheets. Can't change after creation."
                >
                  <Input
                    value={draft.code}
                    onChange={(e) =>
                      setDraft({ ...draft, code: e.target.value.toUpperCase() })
                    }
                    placeholder="PET"
                    disabled={Boolean(editing)}
                  />
                </Field>
                <Field label="Display name">
                  <Input
                    value={draft.display_name}
                    onChange={(e) =>
                      setDraft({ ...draft, display_name: e.target.value })
                    }
                    placeholder="Pet Insurance"
                  />
                </Field>
              </div>
              <div className="grid grid-cols-2 gap-3">
                <Field
                  label="Insurer (optional)"
                  hint="Underwriting insurer, picked from the Insurers tab. Groups this product's columns into that insurer's membership/billing reports on the Reports page. A name not in the list still saves."
                >
                  <InsurerSelect
                    value={draft.insurer ?? ""}
                    onChange={(v) => setDraft({ ...draft, insurer: v })}
                  />
                </Field>
                <Field
                  label="Report code (optional)"
                  hint="Column code used on insurer reports when it differs from the internal code (e.g. GCGP reports as GOGP)."
                >
                  <Input
                    value={draft.report_code ?? ""}
                    onChange={(e) =>
                      setDraft({
                        ...draft,
                        report_code: e.target.value.toUpperCase(),
                      })
                    }
                    placeholder="GOGP"
                  />
                </Field>
              </div>
              <Field
                label="Participation model"
                hint="Who can enrol: standard, extended (adds dependants), or eo_only (employee-only, no voluntary tiers)."
              >
                <Select
                  value={draft.participation_model}
                  onValueChange={(v) =>
                    setDraft({
                      ...draft,
                      participation_model:
                        v as ProductPayload["participation_model"],
                    })
                  }
                >
                  <SelectTrigger>
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    {PARTICIPATION_MODELS.map((m) => (
                      <SelectItem key={m} value={m}>
                        {m}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </Field>
              <div className="flex items-center justify-between rounded-md border border-border p-3">
                <div className="flex items-center gap-1">
                  <div className="text-sm font-medium">Has dependants</div>
                  <InfoHint>
                    Spouse + children eligible (e.g. GHS family plans).
                  </InfoHint>
                </div>
                <Switch
                  checked={draft.has_dependants}
                  onCheckedChange={(v) =>
                    setDraft({ ...draft, has_dependants: v })
                  }
                />
              </div>
              <div className="flex items-center justify-between rounded-md border border-border p-3">
                <div className="flex items-center gap-1">
                  <div className="text-sm font-medium">Outpatient</div>
                  <InfoHint>GP / Specialist outpatient benefit.</InfoHint>
                </div>
                <Switch
                  checked={draft.is_outpatient}
                  onCheckedChange={(v) =>
                    setDraft({ ...draft, is_outpatient: v })
                  }
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
                  !draft.code ||
                  !draft.display_name ||
                  (!editing && firmWriteBlocked) ||
                  create.isPending ||
                  update.isPending
                }
              >
                {editing ? "Save changes" : "Save product"}
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
            <SkeletonTable rows={5} columns={6} />
          ) : visible.length === 0 ? (
            <p className="py-6 text-center text-sm text-muted-foreground">
              {scope === "firm"
                ? "No firm-library products yet."
                : "No products yet."}
            </p>
          ) : (
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Code</TableHead>
                  <TableHead>Display name</TableHead>
                  <TableHead>Insurer</TableHead>
                  <TableHead>Participation</TableHead>
                  <TableHead>Features</TableHead>
                  <TableHead>Scope</TableHead>
                  <TableHead className="w-[100px]" />
                </TableRow>
              </TableHeader>
              <TableBody>
                {visible.map((p) => {
                  const isGlobal = p.client_id === null;
                  // Firm-library rows are shared across every company, so only
                  // admins may edit/delete them (mirrors the backend gate).
                  const locked = isGlobal && !isAdmin;
                  return (
                    <TableRow key={p.id}>
                      <TableCell>
                        <code className="font-mono text-xs bg-muted px-1.5 py-0.5 rounded">
                          {p.code}
                        </code>
                      </TableCell>
                      <TableCell className="font-medium">
                        {p.display_name}
                      </TableCell>
                      <TableCell className="text-muted-foreground">
                        {p.insurer ?? "—"}
                      </TableCell>
                      <TableCell>
                        <Badge variant="outline">{p.participation_model}</Badge>
                      </TableCell>
                      <TableCell>
                        <div className="flex gap-1 flex-wrap">
                          {p.has_dependants && (
                            <Badge variant="outline">Dependants</Badge>
                          )}
                          {p.is_outpatient && (
                            <Badge variant="outline">Outpatient</Badge>
                          )}
                        </div>
                      </TableCell>
                      <TableCell>
                        {isGlobal ? (
                          <Badge variant="default" className="gap-1">
                            <Globe className="size-3" /> Firm library
                          </Badge>
                        ) : (
                          <Badge variant="default">Company</Badge>
                        )}
                      </TableCell>
                      <TableCell>
                        <div className="flex gap-1 justify-end">
                          <Button
                            variant="ghost"
                            size="icon-sm"
                            disabled={locked}
                            title={
                              locked
                                ? "Firm-library defaults are admin-only"
                                : undefined
                            }
                            onClick={() => beginEdit(p)}
                            aria-label="Edit product"
                          >
                            <Pencil className="size-3.5" />
                          </Button>
                          <Button
                            variant="ghost"
                            size="icon-sm"
                            disabled={locked}
                            title={
                              locked
                                ? "Firm-library defaults are admin-only"
                                : undefined
                            }
                            onClick={() => setDeleting(p)}
                            aria-label="Delete product"
                            className="text-error hover:text-error"
                          >
                            <Trash2 className="size-3.5" />
                          </Button>
                        </div>
                      </TableCell>
                    </TableRow>
                  );
                })}
              </TableBody>
            </Table>
          )}
        </CardContent>
      </Card>

      <PageGuide
        purpose="The insurance product types available for benefit plans (e.g. GTL, GHS, GPA). Each product defines participation rules and whether dependants are eligible. Adding a new product is admin-only — no code deploy needed."
        connections={[
          { label: "→ Categories", description: "Each category is mapped to a product code from this catalog" },
          { label: "→ Employee attributes", description: "Attributes define employee fields; products define insurance lines they map into" },
          { label: "→ Placement slips", description: "Uploaded placement slips reference product codes parsed from insurer documents" },
        ]}
      />

      <AlertDialog
        open={Boolean(deleting)}
        onOpenChange={(o) => !o && setDeleting(null)}
        title={`Delete product ${deleting?.display_name ?? ""}?`}
        description={
          <>
            Product <code>{deleting?.code}</code> will be removed. Any
            categories already mapped to this product retain the link but the
            display name lookup will fall back to the raw code. This cannot be
            undone.
          </>
        }
        confirmLabel="Delete product"
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
