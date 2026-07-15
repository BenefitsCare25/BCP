import { useMemo, useState } from "react";
import {
  ArrowRight,
  CircleCheck,
  Loader2,
  Package,
  Sparkles,
  TriangleAlert,
  Wand2,
} from "lucide-react";
import { toast } from "sonner";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Checkbox } from "@/components/ui/checkbox";
import { Switch } from "@/components/ui/switch";
import { InfoHint } from "@/components/ui/tooltip";
import { useApplyConfig, useRecommendConfig } from "@/api/hooks";
import { cn } from "@/lib/cn";
import { formatError } from "@/lib/errors";
import type {
  ApplyAttributeItem,
  ApplyProductItem,
  AttributeRecommendation,
  ConfigRecommendation,
  ProductRecommendation,
} from "@/types";

interface Props {
  policyYearId: string;
  aiConfigured: boolean;
  hasCategories: boolean;
}

function ruleSummary(rule: Record<string, unknown> | null): string {
  if (!rule) return "—";
  const op = String(rule.op ?? "");
  const source = String(rule.source ?? "");
  if (op === "passthrough") return `copy ${source} as-is`;
  if (op === "regex_extract")
    return `extract ${rule.cast ? `${rule.cast} ` : ""}from ${source}`;
  if (op === "regex_case") {
    const n = Array.isArray(rule.cases) ? rule.cases.length : 0;
    return `map ${source} (${n} case${n === 1 ? "" : "s"})`;
  }
  return op;
}

export function RecommendationPanel({
  policyYearId,
  aiConfigured,
  hasCategories,
}: Props) {
  const recommend = useRecommendConfig();
  const apply = useApplyConfig();
  const [result, setResult] = useState<ConfigRecommendation | null>(null);
  const [selectedAttrs, setSelectedAttrs] = useState<Record<string, boolean>>({});
  const [selectedProducts, setSelectedProducts] = useState<Record<string, boolean>>({});
  const [rerun, setRerun] = useState(true);

  const run = async () => {
    try {
      const res = await recommend.mutateAsync(policyYearId);
      setResult(res);
      // Pre-select everything that's genuinely new; existing rows are
      // informational and left unticked.
      const attrSeed: Record<string, boolean> = {};
      for (const a of res.attributes) attrSeed[a.attribute_id] = !a.already_exists;
      const prodSeed: Record<string, boolean> = {};
      for (const p of res.products) prodSeed[p.code] = !p.already_exists;
      setSelectedAttrs(attrSeed);
      setSelectedProducts(prodSeed);
    } catch (err) {
      toast.error(formatError(err));
    }
  };

  const attrItems: ApplyAttributeItem[] = useMemo(() => {
    if (!result) return [];
    return result.attributes
      .filter((a) => selectedAttrs[a.attribute_id])
      .map((a) => ({
        attribute_id: a.attribute_id,
        display_name: a.display_name,
        data_type: a.data_type,
        enum_values: a.enum_values,
        is_pii: a.is_pii,
        description: a.description,
        // Only carry a derivation rule that actually produced values.
        derived_from: a.valid ? a.derived_from : null,
        derivation_rule: a.valid ? a.derivation_rule : null,
      }));
  }, [result, selectedAttrs]);

  const productItems: ApplyProductItem[] = useMemo(() => {
    if (!result) return [];
    return result.products
      .filter((p) => selectedProducts[p.code])
      .map((p) => ({
        code: p.code,
        display_name: p.display_name,
        insurer: p.insurer,
        participation_model: p.participation_model,
        has_dependants: p.has_dependants,
        is_outpatient: p.is_outpatient,
      }));
  }, [result, selectedProducts]);

  const totalSelected = attrItems.length + productItems.length;

  const applyAll = async () => {
    if (totalSelected === 0) return;
    try {
      const res = await apply.mutateAsync({
        policyYearId,
        attributes: attrItems,
        products: productItems,
        rerun_matching: rerun,
      });
      const parts: string[] = [];
      if (res.attributes_created.length)
        parts.push(`${res.attributes_created.length} attribute(s) added`);
      if (res.attributes_updated.length)
        parts.push(`${res.attributes_updated.length} updated`);
      if (res.products_created.length)
        parts.push(`${res.products_created.length} product(s) added`);
      if (res.categories_relinked)
        parts.push(`${res.categories_relinked} categories re-linked`);
      if (res.rematched)
        parts.push(`re-matched ${res.employees_matched ?? 0} employees`);
      toast.success(parts.join(" · ") || "Applied");
      setResult(null);
      setSelectedAttrs({});
      setSelectedProducts({});
    } catch (err) {
      toast.error(formatError(err));
    }
  };

  const newAttrs = result?.attributes.filter((a) => !a.already_exists) ?? [];
  const existingAttrs = result?.attributes.filter((a) => a.already_exists) ?? [];
  const newProducts = result?.products.filter((p) => !p.already_exists) ?? [];

  return (
    <Card>
      <CardHeader>
        <div className="flex items-start justify-between gap-4 flex-wrap">
          <div>
            <CardTitle className="flex items-center gap-2">
              <Sparkles className="size-4" /> AI configuration recommendations
              <InfoHint>
                Reads this policy year's placement-slip categories and
                recommends the employee attributes and products you need —
                flagging what's missing. When a roster is uploaded, it also
                proposes how to auto-fill each attribute. Uses up to 2 AI calls
                (cached).
              </InfoHint>
            </CardTitle>
          </div>
          <Button
            onClick={run}
            disabled={recommend.isPending || !aiConfigured || !hasCategories}
            title={
              !aiConfigured
                ? "Configure an AI provider first"
                : !hasCategories
                  ? "Upload a placement slip first"
                  : undefined
            }
          >
            {recommend.isPending ? (
              <Loader2 className="size-4 animate-spin" />
            ) : (
              <Wand2 className="size-4" />
            )}
            {result ? "Re-analyze" : "Recommend attributes & products"}
          </Button>
        </div>
      </CardHeader>

      {result && (
        <CardContent className="space-y-4 border-t border-border pt-4">
          <div className="flex flex-wrap items-center gap-2 text-sm text-muted-foreground">
            Analyzed {result.category_count} categories
            {result.roster_present ? (
              <Badge variant="outline">
                roster: {result.employee_count} employees
              </Badge>
            ) : (
              <Badge variant="outline">no roster yet</Badge>
            )}
            {result.model && <Badge variant="outline">{result.model}</Badge>}
            {result.cache_hit && <Badge variant="default">cached</Badge>}
          </div>

          {/* Attributes */}
          <div className="space-y-2">
            <h3 className="text-sm font-medium text-foreground">
              Employee attributes ({newAttrs.length} new)
            </h3>
            {newAttrs.length === 0 && (
              <p className="text-sm text-muted-foreground">
                Your schema already covers every attribute the categories imply.
              </p>
            )}
            {newAttrs.map((a) => (
              <AttributeCard
                key={a.attribute_id}
                rec={a}
                selected={Boolean(selectedAttrs[a.attribute_id])}
                onToggle={(v) =>
                  setSelectedAttrs((s) => ({ ...s, [a.attribute_id]: v }))
                }
              />
            ))}
            {existingAttrs.length > 0 && (
              <details className="text-sm text-muted-foreground">
                <summary className="cursor-pointer py-1">
                  {existingAttrs.length} already in your schema
                </summary>
                <div className="mt-2 flex flex-wrap gap-1.5">
                  {existingAttrs.map((a) => (
                    <Badge key={a.attribute_id} variant="outline">
                      {a.display_name}
                    </Badge>
                  ))}
                </div>
              </details>
            )}
          </div>

          {/* Products */}
          <div className="space-y-2">
            <h3 className="text-sm font-medium text-foreground">
              Products catalog ({newProducts.length} missing)
            </h3>
            {newProducts.length === 0 && (
              <p className="text-sm text-muted-foreground">
                Every product detected on the slip already exists in your catalog.
              </p>
            )}
            {newProducts.map((p) => (
              <ProductCard
                key={p.code}
                rec={p}
                selected={Boolean(selectedProducts[p.code])}
                onToggle={(v) =>
                  setSelectedProducts((s) => ({ ...s, [p.code]: v }))
                }
              />
            ))}
          </div>

          {/* Apply bar */}
          <div className="flex items-center justify-between gap-4 border-t border-border pt-4">
            <label className="flex items-center gap-2 text-sm text-muted-foreground">
              <Switch checked={rerun} onCheckedChange={setRerun} />
              Re-run matching after applying
            </label>
            <div className="flex items-center gap-3">
              <span className="text-sm text-muted-foreground">
                {totalSelected} selected
              </span>
              <Button
                onClick={applyAll}
                disabled={totalSelected === 0 || apply.isPending}
              >
                {apply.isPending && <Loader2 className="size-4 animate-spin" />}
                Apply {totalSelected > 0 ? `${totalSelected} ` : ""}
                {totalSelected === 1 ? "item" : "items"}
              </Button>
            </div>
          </div>
        </CardContent>
      )}
    </Card>
  );
}

function AttributeCard({
  rec,
  selected,
  onToggle,
}: {
  rec: AttributeRecommendation;
  selected: boolean;
  onToggle: (next: boolean) => void;
}) {
  return (
    <div
      className={cn(
        "rounded-lg border bg-card p-4 transition-colors",
        selected ? "border-primary" : "border-border",
      )}
    >
      <div className="flex items-start gap-3">
        <Checkbox
          className="mt-0.5"
          checked={selected}
          onCheckedChange={(v) => onToggle(Boolean(v))}
          aria-label={`Add ${rec.display_name}`}
        />
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-2">
            <span className="font-medium text-foreground">{rec.display_name}</span>
            <code className="text-xs text-muted-foreground">{rec.attribute_id}</code>
            <Badge variant="outline">{rec.data_type}</Badge>
            {rec.is_pii && <Badge variant="warn">PII</Badge>}
          </div>
          {rec.enum_values && rec.enum_values.length > 0 && (
            <div className="mt-1.5 flex flex-wrap gap-1">
              {rec.enum_values.map((v) => (
                <code
                  key={v}
                  className="rounded bg-muted px-1.5 py-0.5 text-[11px] text-foreground"
                >
                  {v}
                </code>
              ))}
            </div>
          )}
          {rec.reasoning && (
            <p className="mt-1.5 text-sm text-muted-foreground">{rec.reasoning}</p>
          )}

          {rec.derivation_rule && (
            <div className="mt-3 rounded-md border border-border bg-muted/40 p-2.5">
              <div className="flex items-center gap-2 text-xs">
                <Badge variant={rec.valid ? "good" : "warn"}>
                  {rec.valid ? (
                    <CircleCheck className="mr-1 size-3" />
                  ) : (
                    <TriangleAlert className="mr-1 size-3" />
                  )}
                  auto-fill
                </Badge>
                <span className="text-foreground">{ruleSummary(rec.derivation_rule)}</span>
                <span className="text-muted-foreground">
                  · matched {rec.match_count}/{rec.sample_size}
                </span>
              </div>
              {rec.samples.length > 0 && (
                <div className="mt-2 space-y-1">
                  {rec.samples.slice(0, 4).map((s, i) => (
                    <div key={i} className="flex items-center gap-2 font-mono text-xs">
                      <span className="truncate text-muted-foreground">{s.input}</span>
                      <ArrowRight className="size-3 shrink-0 text-muted-foreground/60" />
                      <span
                        className={cn(
                          "shrink-0",
                          s.output === null || s.output === undefined
                            ? "text-muted-foreground/60"
                            : "text-foreground",
                        )}
                      >
                        {s.output === null || s.output === undefined
                          ? "—"
                          : String(s.output)}
                      </span>
                    </div>
                  ))}
                </div>
              )}
              {rec.warning && (
                <p className="mt-2 flex items-center gap-1.5 text-xs text-warn">
                  <TriangleAlert className="size-3.5 shrink-0" />
                  {rec.warning}
                </p>
              )}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

function ProductCard({
  rec,
  selected,
  onToggle,
}: {
  rec: ProductRecommendation;
  selected: boolean;
  onToggle: (next: boolean) => void;
}) {
  return (
    <div
      className={cn(
        "rounded-lg border bg-card p-4 transition-colors",
        selected ? "border-primary" : "border-border",
      )}
    >
      <div className="flex items-start gap-3">
        <Checkbox
          className="mt-0.5"
          checked={selected}
          onCheckedChange={(v) => onToggle(Boolean(v))}
          aria-label={`Add ${rec.code}`}
        />
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-2">
            <Package className="size-4 text-muted-foreground" />
            <span className="font-medium text-foreground">{rec.display_name}</span>
            <code className="text-xs text-muted-foreground">{rec.code}</code>
            {rec.has_dependants && <Badge variant="outline">dependants</Badge>}
            {rec.is_outpatient && <Badge variant="outline">outpatient</Badge>}
            <Badge variant="outline">{rec.participation_model}</Badge>
            {rec.category_count > 0 && (
              <span className="text-xs text-muted-foreground">
                {rec.category_count} categor{rec.category_count === 1 ? "y" : "ies"}
              </span>
            )}
          </div>
          {rec.insurer && (
            <p className="mt-1 text-sm text-muted-foreground">Insurer: {rec.insurer}</p>
          )}
          {rec.reasoning && (
            <p className="mt-1.5 text-sm text-muted-foreground">{rec.reasoning}</p>
          )}
        </div>
      </div>
    </div>
  );
}
