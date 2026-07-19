import { useState } from "react";
import { Pencil, Trash2 } from "lucide-react";
import { toast } from "sonner";
import {
  useCreateProduct,
  useDeleteProduct,
  useProducts,
  useUpdateProduct,
  type ProductPayload,
} from "@/api/hooks";
import { AlertDialog } from "@/components/ui/alert-dialog";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Globe } from "lucide-react";
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
  const create = useCreateProduct();
  const update = useUpdateProduct();
  const remove = useDeleteProduct();
  const [editing, setEditing] = useState<Product | null>(null);
  const [draft, setDraft] = useState<ProductPayload>(EMPTY_DRAFT);
  const [deleting, setDeleting] = useState<Product | null>(null);

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
        await create.mutateAsync(payload);
        toast.success(`Added ${payload.display_name}`);
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
                  {editing ? "Edit product" : "Add client product"}
                </SheetTitle>
                <InfoHint>
                  {editing
                    ? editing.client_id === null
                      ? "Editing a global default — changes apply to all clients."
                      : "Updates this product for the current client only."
                    : "Adds a product scoped to the current client."}
                </InfoHint>
              </div>
            </SheetHeader>
            <SheetBody className="space-y-4">
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
                  hint="Underwriting insurer. Groups this product's columns into that insurer's membership/billing reports on the Reports page."
                >
                  <Input
                    value={draft.insurer ?? ""}
                    onChange={(e) =>
                      setDraft({ ...draft, insurer: e.target.value })
                    }
                    placeholder="AIA"
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
                  create.isPending ||
                  update.isPending
                }
              >
                {editing ? "Save changes" : "Save product"}
              </Button>
            </SheetFooter>
          </SheetContent>
        </Sheet>

      <Card>
        <CardContent className="pt-6">
          {isLoading ? (
            <SkeletonTable rows={5} columns={6} />
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
                {products.map((p) => {
                  const isGlobal = p.client_id === null;
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
                            <Globe className="size-3" /> Global
                          </Badge>
                        ) : (
                          <Badge variant="default">Client-specific</Badge>
                        )}
                      </TableCell>
                      <TableCell>
                        <div className="flex gap-1 justify-end">
                          <Button
                            variant="ghost"
                            size="icon-sm"
                            onClick={() => beginEdit(p)}
                            aria-label="Edit product"
                          >
                            <Pencil className="size-3.5" />
                          </Button>
                          <Button
                            variant="ghost"
                            size="icon-sm"
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
