import { useEffect, useMemo, useState, type ReactNode } from "react";
import { Pencil, Trash2, Upload, X } from "lucide-react";
import {
  Card,
  CardContent,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { AlertDialog } from "@/components/ui/alert-dialog";
import { Button } from "@/components/ui/button";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { useProductSetups, useRemoveProduct, useSetupProducts } from "@/api/hooks";
import { useRegistry } from "@/api/registry";
import { ProductConfigurator } from "./ProductConfigurator";
import { AddProductDialog } from "./AddProductDialog";
import { LINE_LABELS, isProductAdded, lineForCode } from "@/lib/insuranceLines";
import { formatError } from "@/lib/errors";
import type {
  Category,
  CategoryGroup,
  InsuranceLine,
  SetupProductSummary,
} from "@/types";
import { toast } from "sonner";

interface Props {
  policyYearId: string;
  line: InsuranceLine;
  // Category groups already scoped to this line.
  groups: CategoryGroup[];
  onSelectCategory: (c: Category) => void;
  // Benefit-year picker rendered beside the product title (single node — only
  // the active product card mounts, so it appears in one place at a time).
  yearSelector?: ReactNode;
  onBlockingEditChange?: (
    line: InsuranceLine,
    edit: { code: string; name: string; actionLabel: string } | null,
  ) => void;
}

export function LineTab({
  policyYearId,
  line,
  groups,
  onSelectCategory,
  yearSelector,
  onBlockingEditChange,
}: Props) {
  const [activeCode, setActiveCode] = useState("");
  // Codes created this session — shown optimistically until the refetch confirms
  // them, so a just-added product's tab appears immediately.
  const [justAdded, setJustAdded] = useState<string[]>([]);
  const [removeTarget, setRemoveTarget] = useState<SetupProductSummary | null>(null);
  const [editingCode, setEditingCode] = useState<string | null>(null);
  const [dirtyByCode, setDirtyByCode] = useState<Record<string, boolean>>({});
  const [unsavedPromptOpen, setUnsavedPromptOpen] = useState(false);

  const { data: allSetupProducts = [] } = useSetupProducts(policyYearId);
  const { data: setups = [] } = useProductSetups(policyYearId);
  const { data: registry } = useRegistry();
  const removeProduct = useRemoveProduct(policyYearId);

  const draftCodes = useMemo(
    () => new Set(setups.map((s) => s.product_code)),
    [setups],
  );

  // Products configured under this tab — each gets its own sub-tab + form.
  const products = useMemo(() => {
    const real = allSetupProducts.filter(
      (p) =>
        (p.line ?? lineForCode(p.code, registry?.entries)) === line &&
        isProductAdded(p, draftCodes),
    );
    const realCodes = new Set(real.map((p) => p.code));
    const synthetic = justAdded
      .filter((code) => !realCodes.has(code))
      .map((code) => {
        const known = allSetupProducts.find((p) => p.code === code);
        return {
          code,
          display_name: known?.display_name ?? code,
          has_template_file: known?.has_template_file ?? false,
          has_slip_data: false,
          line,
          is_client_product: true,
        } satisfies SetupProductSummary;
      });
    return [...real, ...synthetic].sort((a, b) => a.code.localeCompare(b.code));
  }, [allSetupProducts, line, draftCodes, justAdded, registry]);

  const groupByCode = useMemo(() => {
    const m = new Map<string, CategoryGroup>();
    for (const g of groups) m.set(g.product_code, g);
    return m;
  }, [groups]);

  const unassigned = groupByCode.get("(unassigned)");
  const editingProduct = products.find((p) => p.code === editingCode) ?? null;
  const hasUnsavedEdit =
    Boolean(editingCode) && Boolean(editingCode && dirtyByCode[editingCode]);
  const setupActionLabel = (code: string | null | undefined) =>
    setups.some(
      (s) =>
        s.product_code.toUpperCase() === String(code ?? "").toUpperCase() &&
        (s.status === "confirmed" || s.materialized_product_id),
    )
      ? "Update setup"
      : "Confirm setup";

  useEffect(() => {
    onBlockingEditChange?.(
      line,
      hasUnsavedEdit && editingProduct
        ? {
            code: editingProduct.code,
            name: editingProduct.display_name,
            actionLabel: setupActionLabel(editingProduct.code),
          }
        : null,
    );
    return () => onBlockingEditChange?.(line, null);
  }, [
    dirtyByCode,
    editingProduct,
    hasUnsavedEdit,
    line,
    onBlockingEditChange,
    setups,
  ]);

  // Keep the active sub-tab valid as the product set changes. Depends on
  // `products` only (not `activeCode`): a freshly-added code set via the dialog
  // survives until the refetch lands; a stale selection self-corrects.
  useEffect(() => {
    if (!products.length) return;
    if (!products.some((p) => p.code === activeCode)) {
      setActiveCode(products[0].code);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [products]);

  useEffect(() => {
    if (editingCode && !products.some((p) => p.code === editingCode)) {
      setEditingCode(null);
    }
  }, [editingCode, products]);

  const handleCreated = (codes: string[]) => {
    if (!codes.length) return;
    setJustAdded((prev) => [...new Set([...prev, ...codes])]);
    if (hasUnsavedEdit) {
      setUnsavedPromptOpen(true);
      return;
    }
    setActiveCode(codes[0]);
  };

  const requestProductTab = (code: string) => {
    if (code === activeCode) return;
    if (hasUnsavedEdit) {
      setUnsavedPromptOpen(true);
      return;
    }
    if (editingCode) closeEdit(editingCode);
    setActiveCode(code);
  };

  const closeEdit = (code: string) => {
    setEditingCode(null);
    setDirtyByCode((prev) => ({ ...prev, [code]: false }));
  };

  const toggleEdit = (p: SetupProductSummary, isEditing: boolean) => {
    if (isEditing) {
      if (dirtyByCode[p.code]) {
        setUnsavedPromptOpen(true);
        return;
      }
      closeEdit(p.code);
      return;
    }
    setEditingCode(p.code);
  };

  const doRemove = async (p: SetupProductSummary) => {
    try {
      await removeProduct.mutateAsync(p.code);
      setJustAdded((prev) => prev.filter((c) => c !== p.code));
      toast.success(`Removed ${p.display_name}`);
    } catch (err) {
      toast.error(formatError(err));
    } finally {
      setRemoveTarget(null);
    }
  };

  return (
    <div className="space-y-5">
      {products.length > 0 ? (
        <Tabs value={activeCode} onValueChange={requestProductTab}>
          <div className="flex items-start justify-between gap-3">
            <TabsList className="flex-wrap h-auto">
              {products.map((p) => (
                <TabsTrigger key={p.code} value={p.code} title={p.display_name}>
                  <code className="font-mono text-xs font-semibold">{p.code}</code>
                </TabsTrigger>
              ))}
            </TabsList>
            <AddProductDialog
              policyYearId={policyYearId}
              line={line}
              onCreated={handleCreated}
            />
          </div>
          {products.map((p) => {
            const isEditing = editingCode === p.code;
            return (
              <TabsContent key={p.code} value={p.code}>
                <Card>
                  <CardHeader>
                    <div className="flex flex-wrap items-center justify-between gap-3">
                      <div className="flex flex-wrap items-center gap-3">
                        <CardTitle>{p.display_name}</CardTitle>
                        {yearSelector}
                      </div>
                      <div className="flex items-center gap-2">
                        <Button
                          variant={isEditing ? "secondary" : "outline"}
                          size="sm"
                          onClick={() => toggleEdit(p, isEditing)}
                        >
                          {isEditing ? (
                            <X className="size-3.5" />
                          ) : (
                            <Pencil className="size-3.5" />
                          )}
                          {isEditing ? "Close edit" : "Edit"}
                        </Button>
                        <Button
                          variant="outline"
                          size="sm"
                          disabled={removeProduct.isPending}
                          onClick={() => setRemoveTarget(p)}
                          className="text-error hover:text-error"
                        >
                          <Trash2 className="size-3.5" /> Remove
                        </Button>
                      </div>
                    </div>
                  </CardHeader>
                  <CardContent>
                    <ProductConfigurator
                      policyYearId={policyYearId}
                      code={p.code}
                      group={groupByCode.get(p.code)}
                      onSelectCategory={onSelectCategory}
                      isEditing={isEditing}
                      onDone={() => closeEdit(p.code)}
                      onDirtyChange={(dirty) =>
                        setDirtyByCode((prev) => ({
                          ...prev,
                          [p.code]: dirty,
                        }))
                      }
                    />
                  </CardContent>
                </Card>
              </TabsContent>
            );
          })}
        </Tabs>
      ) : (
        <div className="flex flex-col items-center gap-3 rounded-lg border border-dashed border-border p-10 text-center">
          {yearSelector}
          <Upload className="size-6 text-muted-foreground" />
          <div className="text-sm text-muted-foreground">
            No {LINE_LABELS[line]} products yet — upload a placement slip above,
            or add a product to start configuring.
          </div>
          <AddProductDialog
            policyYearId={policyYearId}
            line={line}
            onCreated={handleCreated}
          />
        </div>
      )}

      {unassigned && unassigned.categories.length > 0 && (
        <Card>
          <CardHeader>
            <CardTitle className="text-sm">Unassigned categories</CardTitle>
          </CardHeader>
          <CardContent className="space-y-2">
            {unassigned.categories.map((c) => (
              <button
                key={c.id}
                onClick={() => onSelectCategory(c)}
                className="w-full text-left rounded-lg border border-border bg-card hover:border-ring/40 hover:bg-muted/20 transition-colors p-3 text-sm"
              >
                {c.display_name}
              </button>
            ))}
          </CardContent>
        </Card>
      )}

      <AlertDialog
        open={removeTarget !== null}
        onOpenChange={(open) => !open && setRemoveTarget(null)}
        title={`Remove ${removeTarget?.display_name ?? "product"}?`}
        description="This deletes the product's setup, plans, coverage period, and extracted categories for this policy year. You can add it again later. This cannot be undone."
        confirmLabel="Remove product"
        confirmVariant="destructive"
        loading={removeProduct.isPending}
        onConfirm={async () => {
          if (removeTarget) await doRemove(removeTarget);
        }}
      />
      <AlertDialog
        open={unsavedPromptOpen}
        onOpenChange={setUnsavedPromptOpen}
        title="Save current setup first"
        description={
          <span>
            <strong>{editingProduct?.display_name ?? "This product"}</strong>{" "}
            has unsaved setup changes. Use the{" "}
            <strong>{setupActionLabel(editingProduct?.code)}</strong>{" "}
            button before leaving this product.
          </span>
        }
        confirmLabel="Got it"
        cancelLabel={null}
        confirmVariant="default"
        tone="info"
        onConfirm={() => setUnsavedPromptOpen(false)}
      />
    </div>
  );
}
