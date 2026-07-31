import { useMemo, useState } from "react";
import { Plus, ChevronDown, CheckCircle2, Trash2 } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Switch } from "@/components/ui/switch";
import { Checkbox } from "@/components/ui/checkbox";
import { Badge } from "@/components/ui/badge";
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
  SheetContent,
  SheetDescription,
  SheetFooter,
  SheetHeader,
  SheetTitle,
  SheetTrigger,
} from "@/components/ui/sheet";
import { FieldLabel } from "@/components/ui/tooltip";
import type { ProductPayload } from "@/api/hooks";
import {
  useCreateProduct,
  useProductSetups,
  useRemoveProduct,
  useSetupProducts,
} from "@/api/hooks";
import { useRegistry } from "@/api/registry";
import { LINE_LABELS, isProductAdded, lineForCode } from "@/lib/insuranceLines";
import { formatError } from "@/lib/errors";
import type { InsuranceLine } from "@/types";
import { toast } from "sonner";

interface Props {
  policyYearId: string;
  line: InsuranceLine;
  // Called with the codes of the products created, so the parent can open them.
  onCreated: (codes: string[]) => void;
}

const PARTICIPATION: { value: "standard" | "extended" | "eo_only"; label: string }[] = [
  { value: "standard", label: "Standard" },
  { value: "extended", label: "Extended (incl. dependants)" },
  { value: "eo_only", label: "Employee only" },
];

const DEFAULT_PROFILE: Record<InsuranceLine, string> = {
  medical: "tiered_medical",
  life: "sum_assured",
  flex: "tiered_medical",
};

// No insurer here: it is a per-benefit-year placement fact, entered on the
// product's own Header & Policy tab once the product is added.
const emptyCustom = (line: InsuranceLine) => ({
  code: "",
  displayName: "",
  formProfile: DEFAULT_PROFILE[line],
  participation: "standard" as "standard" | "extended" | "eo_only",
  hasDependants: false,
  isOutpatient: false,
});

export function AddProductDialog({ policyYearId, line, onCreated }: Props) {
  const [open, setOpen] = useState(false);
  const [picked, setPicked] = useState<Set<string>>(new Set());
  const [showCustom, setShowCustom] = useState(false);
  const [custom, setCustom] = useState(emptyCustom(line));

  const { data: allProducts = [] } = useSetupProducts(policyYearId);
  const { data: setups = [] } = useProductSetups(policyYearId);
  const { data: registry } = useRegistry();
  const create = useCreateProduct();
  const remove = useRemoveProduct(policyYearId);

  // Selectable form profiles come from the backend registry (fallback keeps
  // the dialog usable before the registry query resolves).
  const formProfiles = registry?.profiles?.length
    ? registry.profiles.map((p) => ({ value: p.id, label: p.label }))
    : [{ value: DEFAULT_PROFILE[line], label: "Standard" }];

  const onRemove = async (code: string, name: string) => {
    try {
      await remove.mutateAsync(code);
      toast.success(`Removed ${name}`);
    } catch (err) {
      toast.error(formatError(err));
    }
  };

  const draftCodes = useMemo(
    () => new Set(setups.map((s) => s.product_code)),
    [setups],
  );

  // Every standard product for this line — the multi-select list. Already-added
  // ones stay visible but disabled (badged "Added") so the full catalogue is
  // always shown; not-yet-added ones are pickable. `line` falls back to a
  // code-based guess so the list survives a not-yet-refreshed API response.
  const lineProducts = useMemo(
    () =>
      allProducts
        .map((p) => ({ ...p, added: isProductAdded(p, draftCodes) }))
        .filter((p) => (p.line ?? lineForCode(p.code, registry?.entries)) === line)
        .sort(
          (a, b) =>
            Number(a.added) - Number(b.added) || a.code.localeCompare(b.code),
        ),
    [allProducts, line, draftCodes, registry],
  );
  const pickableCount = lineProducts.filter((p) => !p.added).length;

  const reset = () => {
    setPicked(new Set());
    setShowCustom(false);
    setCustom(emptyCustom(line));
  };

  const toggle = (code: string) => {
    setPicked((prev) => {
      const next = new Set(prev);
      if (next.has(code)) next.delete(code);
      else next.add(code);
      return next;
    });
  };

  const customCode = custom.code.trim().toUpperCase();
  const customValid =
    showCustom && customCode.length > 0 && custom.displayName.trim().length > 0;
  const customPartial =
    showCustom && (customCode.length > 0 || custom.displayName.trim().length > 0);
  const canSubmit =
    (picked.size > 0 || customValid) && !(customPartial && !customValid);

  const submit = async () => {
    if (!canSubmit || create.isPending) return;
    const byCode = new Map(allProducts.map((p) => [p.code, p]));
    const payloads: ProductPayload[] = [];
    for (const code of picked) {
      const summary = byCode.get(code);
      payloads.push({
        code,
        display_name: summary?.display_name ?? code,
        participation_model: "standard",
        has_dependants: false,
        is_outpatient: false,
        line,
      });
    }
    if (customValid) {
      payloads.push({
        code: customCode,
        display_name: custom.displayName.trim(),
        participation_model: custom.participation,
        has_dependants: custom.hasDependants,
        is_outpatient: custom.isOutpatient,
        line,
        form_profile: custom.formProfile,
      });
    }

    const results = await Promise.allSettled(
      payloads.map((p) => create.mutateAsync(p)),
    );
    const created = payloads
      .filter((_, i) => results[i].status === "fulfilled")
      .map((p) => p.code);
    const failed = results.length - created.length;

    if (created.length) {
      toast.success(
        `Added ${created.length} product${created.length === 1 ? "" : "s"} to ${
          LINE_LABELS[line]
        }${failed ? ` · ${failed} failed` : ""}`,
      );
      setOpen(false);
      reset();
      onCreated(created);
    } else {
      const reason = results.find((r) => r.status === "rejected") as
        | PromiseRejectedResult
        | undefined;
      toast.error(reason ? formatError(reason.reason) : "Could not add products");
    }
  };

  return (
    <Sheet
      open={open}
      onOpenChange={(o) => {
        setOpen(o);
        if (!o) reset();
      }}
    >
      <SheetTrigger asChild>
        <Button variant="outline" size="sm">
          <Plus className="size-4" /> Add product
        </Button>
      </SheetTrigger>
      <SheetContent>
        <SheetHeader>
          <SheetTitle>Add {LINE_LABELS[line]} products</SheetTitle>
          <SheetDescription>
            Pick from the standard {LINE_LABELS[line]} products, or define a
            custom one. Everything you add is filed under this tab.
          </SheetDescription>
        </SheetHeader>
        <SheetBody className="space-y-4">
          <div className="space-y-2">
            <div className="flex items-center justify-between">
              <Label>Standard products</Label>
              {pickableCount > 0 && (
                <span className="text-xs text-muted-foreground">
                  Tick one or more to add
                </span>
              )}
            </div>
            {lineProducts.length === 0 ? (
              <p className="text-sm text-muted-foreground">
                No standard {LINE_LABELS[line]} products — define a custom one
                below.
              </p>
            ) : (
              <div className="grid grid-cols-1 gap-1.5 max-h-[280px] overflow-y-auto pr-1">
                {lineProducts.map((p) =>
                  p.added ? (
                    <div
                      key={p.code}
                      className="flex items-center gap-3 rounded-lg border border-border px-3 py-2.5"
                    >
                      <CheckCircle2 className="size-4 text-good shrink-0" />
                      <code className="text-[11px] font-mono bg-muted px-1.5 py-0.5 rounded">
                        {p.code}
                      </code>
                      <span className="text-sm text-foreground flex-1">
                        {p.display_name}
                      </span>
                      <Button
                        variant="outline"
                        size="sm"
                        disabled={remove.isPending}
                        onClick={() => onRemove(p.code, p.display_name)}
                        className="text-error hover:text-error"
                      >
                        <Trash2 className="size-3.5" /> Remove
                      </Button>
                    </div>
                  ) : (
                    <label
                      key={p.code}
                      className="flex items-center gap-3 rounded-lg border border-border px-3 py-2.5 cursor-pointer hover:bg-muted/40"
                    >
                      <Checkbox
                        checked={picked.has(p.code)}
                        onCheckedChange={() => toggle(p.code)}
                      />
                      <code className="text-[11px] font-mono bg-muted px-1.5 py-0.5 rounded">
                        {p.code}
                      </code>
                      <span className="text-sm text-foreground flex-1">
                        {p.display_name}
                      </span>
                    </label>
                  ),
                )}
              </div>
            )}
          </div>

          <div className="space-y-3">
            <button
              type="button"
              onClick={() => setShowCustom((v) => !v)}
              className="flex items-center gap-2 text-sm font-medium text-foreground"
            >
              <ChevronDown
                className={`size-4 transition-transform ${showCustom ? "" : "-rotate-90"}`}
              />
              Add a custom product
              {customValid && <Badge variant="good">1</Badge>}
            </button>

            {showCustom && (
              <div className="space-y-4 rounded-lg border border-border p-4">
                <div className="grid grid-cols-2 gap-3">
                  <div className="space-y-1.5">
                    <Label htmlFor="ap-code">Product code</Label>
                    <Input
                      id="ap-code"
                      placeholder="e.g. GHS"
                      value={custom.code}
                      onChange={(e) =>
                        setCustom((c) => ({ ...c, code: e.target.value }))
                      }
                      className="font-mono uppercase"
                    />
                  </div>
                  <div className="space-y-1.5">
                    <Label htmlFor="ap-name">Display name</Label>
                    <Input
                      id="ap-name"
                      placeholder="e.g. Group Hospital & Surgical"
                      value={custom.displayName}
                      onChange={(e) =>
                        setCustom((c) => ({ ...c, displayName: e.target.value }))
                      }
                    />
                  </div>
                </div>

                <div className="grid gap-3 sm:grid-cols-2">
                  <div className="space-y-1.5">
                    <FieldLabel hint="How this product's benefit form is laid out — drives the rate/schedule editors.">
                      Form structure
                    </FieldLabel>
                    <Select
                      value={custom.formProfile}
                      onValueChange={(v) =>
                        setCustom((c) => ({ ...c, formProfile: v }))
                      }
                    >
                      <SelectTrigger>
                        <SelectValue />
                      </SelectTrigger>
                      <SelectContent>
                        {formProfiles.map((p) => (
                          <SelectItem key={p.value} value={p.value}>
                            {p.label}
                          </SelectItem>
                        ))}
                      </SelectContent>
                    </Select>
                  </div>

                  <div className="space-y-1.5">
                    <Label>Participation model</Label>
                    <Select
                      value={custom.participation}
                      onValueChange={(v) =>
                        setCustom((c) => ({
                          ...c,
                          participation: v as "standard" | "extended" | "eo_only",
                        }))
                      }
                    >
                      <SelectTrigger>
                        <SelectValue />
                      </SelectTrigger>
                      <SelectContent>
                        {PARTICIPATION.map((p) => (
                          <SelectItem key={p.value} value={p.value}>
                            {p.label}
                          </SelectItem>
                        ))}
                      </SelectContent>
                    </Select>
                  </div>
                </div>

                <div className="flex items-center justify-between rounded-lg border border-border p-3">
                  <FieldLabel
                    className="text-sm font-medium text-foreground"
                    hint="Spouse / children eligible under this product."
                  >
                    Covers dependants
                  </FieldLabel>
                  <Switch
                    checked={custom.hasDependants}
                    onCheckedChange={(v) =>
                      setCustom((c) => ({ ...c, hasDependants: v }))
                    }
                  />
                </div>

                <div className="flex items-center justify-between rounded-lg border border-border p-3">
                  <FieldLabel
                    className="text-sm font-medium text-foreground"
                    hint="GP / specialist outpatient cover."
                  >
                    Outpatient product
                  </FieldLabel>
                  <Switch
                    checked={custom.isOutpatient}
                    onCheckedChange={(v) =>
                      setCustom((c) => ({ ...c, isOutpatient: v }))
                    }
                  />
                </div>
              </div>
            )}
          </div>
        </SheetBody>
        <SheetFooter>
          <Button variant="outline" onClick={() => setOpen(false)}>
            Cancel
          </Button>
          <Button onClick={submit} disabled={!canSubmit || create.isPending}>
            {create.isPending
              ? "Adding…"
              : `Add ${picked.size + (customValid ? 1 : 0) || ""} & configure`.trim()}
          </Button>
        </SheetFooter>
      </SheetContent>
    </Sheet>
  );
}
