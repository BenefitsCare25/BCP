import { useMemo, useState } from "react";
import { Loader2, Play, Search } from "lucide-react";
import { toast } from "sonner";
import { useSession } from "@/stores/session";
import { usePlans, useProducts } from "@/api/hooks";
import {
  type BulkRequest,
  type BulkResult,
  useApplyBulk,
  usePreviewBulk,
} from "@/api/enrollment";
import { formatError } from "@/lib/errors";
import { cn } from "@/lib/cn";
import { Button } from "@/components/ui/button";
import { Label } from "@/components/ui/label";
import { InfoHint } from "@/components/ui/tooltip";
import { AlertDialog } from "@/components/ui/alert-dialog";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";

const OUTCOME_COLOR: Record<string, string> = {
  applied: "text-good",
  would_apply: "text-primary",
  skipped: "text-warn",
  error: "text-error",
};

type SelectMode = "plan" | "staff";

export function EnrollmentBulkPage() {
  const policyYearId = useSession((s) => s.currentPolicyYearId) ?? undefined;
  const { data: plans } = usePlans(policyYearId);
  const { data: products } = useProducts();
  const preview = usePreviewBulk(policyYearId);
  const apply = useApplyBulk(policyYearId);

  // Products that actually have plans in this year, with their DISTINCT plan
  // codes — a plan_code can repeat within a product (e.g. GPA "(Option N)"),
  // so dedupe to avoid duplicate React keys / indistinguishable dropdown items.
  const plansByCode = useMemo(() => {
    const idToCode = new Map((products ?? []).map((p) => [p.id, p.code]));
    const sets: Record<string, Set<string>> = {};
    for (const pl of plans?.items ?? []) {
      const code = idToCode.get(pl.product_id);
      if (!code) continue;
      (sets[code] ??= new Set()).add(pl.code);
    }
    return Object.fromEntries(
      Object.entries(sets).map(([code, s]) => [code, [...s]]),
    ) as Record<string, string[]>;
  }, [plans, products]);
  const productCodes = Object.keys(plansByCode);

  const [productCode, setProductCode] = useState<string>("");
  const [targetPlan, setTargetPlan] = useState<string>("");
  // "plan" selects everyone currently on a given plan for this product — no
  // manual id entry. "staff" keeps the old free-text path for ad hoc lists.
  const [mode, setMode] = useState<SelectMode>("plan");
  const [sourcePlan, setSourcePlan] = useState<string>("");
  const [staffText, setStaffText] = useState("");
  const [result, setResult] = useState<BulkResult | null>(null);
  // Which resolved rows will actually be applied — defaults to every
  // would_apply row after a preview, but the broker can uncheck individuals
  // before confirming (skipped/error rows are never checkable).
  const [checked, setChecked] = useState<Set<string>>(new Set());
  const [confirmApply, setConfirmApply] = useState(false);

  if (!policyYearId) {
    return (
      <p className="text-sm text-muted-foreground">
        Select a policy year to run a bulk plan update.
      </p>
    );
  }

  const staffIds = staffText
    .split(/[\s,;]+/)
    .map((s) => s.trim())
    .filter(Boolean);

  function resetResult() {
    setResult(null);
    setChecked(new Set());
  }

  function buildPreviewRequest(): BulkRequest | null {
    if (!productCode) {
      toast.error("Pick a product.");
      return null;
    }
    if (!targetPlan) {
      toast.error("Pick a target plan.");
      return null;
    }
    if (mode === "plan") {
      if (!sourcePlan) {
        toast.error("Pick which current plan to move members from.");
        return null;
      }
      if (sourcePlan === targetPlan) {
        toast.error("Source and target plan are the same — nothing to move.");
        return null;
      }
      return {
        product_code: productCode,
        action: "set_plan",
        target_plan_code: targetPlan,
        selector: { current_plan_code: sourcePlan },
      };
    }
    if (!staffIds.length) {
      toast.error("Enter at least one staff ID.");
      return null;
    }
    return {
      product_code: productCode,
      action: "set_plan",
      target_plan_code: targetPlan,
      selector: { staff_ids: staffIds },
    };
  }

  const checkableIds = (result?.rows ?? [])
    .filter((r) => r.outcome === "would_apply" && r.employee_id)
    .map((r) => r.employee_id as string);

  function runPreview() {
    const req = buildPreviewRequest();
    if (!req) return;
    preview.mutate(req, {
      onSuccess: (r) => {
        setResult(r);
        setChecked(
          new Set(
            r.rows
              .filter((row) => row.outcome === "would_apply" && row.employee_id)
              .map((row) => row.employee_id as string),
          ),
        );
      },
      onError: (e) => toast.error(formatError(e)),
    });
  }

  function runApply() {
    if (!productCode || !targetPlan) return;
    const employeeIds = checkableIds.filter((id) => checked.has(id));
    if (!employeeIds.length) {
      toast.error("Select at least one member to apply.");
      return;
    }
    apply.mutate(
      {
        product_code: productCode,
        action: "set_plan",
        target_plan_code: targetPlan,
        selector: { employee_ids: employeeIds },
      },
      {
        onSuccess: (r) => {
          setResult(r);
          setChecked(new Set());
          setConfirmApply(false);
          toast.success(`Applied — ${r.counts.applied ?? 0} updated.`);
        },
        onError: (e) => {
          setConfirmApply(false);
          toast.error(formatError(e));
        },
      },
    );
  }

  const selectedCount = checkableIds.filter((id) => checked.has(id)).length;

  return (
    <div className="space-y-4">
      <div className="rounded-lg border border-border bg-card p-4">
        <div className="flex items-center gap-1">
          <h3 className="text-sm font-semibold text-foreground">Bulk plan update</h3>
          <InfoHint>
            Reassign a product&apos;s plan for many members at once. Select everyone
            currently on a plan (no manual id entry needed), preview who&apos;s
            affected, then apply.
          </InfoHint>
        </div>
        <div className="mt-3 grid gap-3 sm:grid-cols-2">
          <div>
            <Label>Product</Label>
            <Select
              value={productCode}
              onValueChange={(v) => {
                setProductCode(v);
                setTargetPlan("");
                setSourcePlan("");
                resetResult(); // inputs changed — the old preview no longer applies
              }}
            >
              <SelectTrigger>
                <SelectValue placeholder="Select product" />
              </SelectTrigger>
              <SelectContent>
                {productCodes.map((c) => (
                  <SelectItem key={c} value={c}>
                    {c}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
          <div>
            <Label>Move to plan</Label>
            <Select
              value={targetPlan}
              onValueChange={(v) => {
                setTargetPlan(v);
                resetResult(); // inputs changed — the old preview no longer applies
              }}
              disabled={!productCode}
            >
              <SelectTrigger>
                <SelectValue placeholder="Select plan" />
              </SelectTrigger>
              <SelectContent>
                {(plansByCode[productCode] ?? []).map((c) => (
                  <SelectItem key={c} value={c}>
                    {c}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
        </div>

        <div className="mt-4">
          <Label>Select members by</Label>
          <div className="mt-1 inline-flex rounded-md border border-border p-0.5">
            {(
              [
                { value: "plan" as const, label: "Current plan" },
                { value: "staff" as const, label: "Staff IDs" },
              ]
            ).map((opt) => (
              <button
                key={opt.value}
                type="button"
                onClick={() => {
                  setMode(opt.value);
                  resetResult();
                }}
                className={cn(
                  "rounded px-3 py-1 text-xs font-medium transition-colors",
                  mode === opt.value
                    ? "bg-primary text-primary-foreground"
                    : "text-muted-foreground hover:text-foreground",
                )}
              >
                {opt.label}
              </button>
            ))}
          </div>
        </div>

        {mode === "plan" ? (
          <div className="mt-3 max-w-xs">
            <div className="flex items-center gap-1">
              <Label>Move everyone currently on</Label>
              <InfoHint>
                Matches every active member whose effective plan for this product is
                this one today (including prior overrides) — Preview shows exactly
                who before anything is written.
              </InfoHint>
            </div>
            <Select
              value={sourcePlan}
              onValueChange={(v) => {
                setSourcePlan(v);
                resetResult();
              }}
              disabled={!productCode}
            >
              <SelectTrigger>
                <SelectValue placeholder="Select current plan" />
              </SelectTrigger>
              <SelectContent>
                {(plansByCode[productCode] ?? []).map((c) => (
                  <SelectItem key={c} value={c}>
                    {c}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
        ) : (
          <div className="mt-3">
            <Label htmlFor="staff-ids">Staff IDs ({staffIds.length})</Label>
            <textarea
              id="staff-ids"
              value={staffText}
              onChange={(e) => {
                setStaffText(e.target.value);
                resetResult(); // inputs changed — the old preview no longer applies
              }}
              placeholder="Paste staff IDs separated by spaces, commas, or newlines"
              className="mt-1 w-full rounded-md border border-border bg-background px-3 py-2 text-sm text-foreground placeholder:text-muted-foreground focus:outline-none focus:ring-2 focus:ring-ring min-h-[88px]"
            />
          </div>
        )}

        <div className="mt-3 flex items-center gap-2">
          <Button variant="outline" onClick={runPreview} disabled={preview.isPending}>
            {preview.isPending ? (
              <Loader2 className="size-4 animate-spin" />
            ) : (
              <Search className="size-4" />
            )}
            Preview
          </Button>
          <Button
            onClick={() => setConfirmApply(true)}
            disabled={apply.isPending || !result || selectedCount === 0}
            title={result ? undefined : "Run Preview first"}
          >
            <Play className="size-4" /> Apply{result ? ` (${selectedCount})` : ""}
          </Button>
          {!result && (
            <span className="text-xs text-muted-foreground">
              Run Preview first — Apply is enabled after a dry-run of the
              current inputs.
            </span>
          )}
        </div>
      </div>

      {result && (
        <div className="rounded-lg border border-border bg-card">
          <div className="flex flex-wrap items-center gap-4 border-b border-border px-4 py-2.5 text-xs">
            {Object.entries(result.counts).map(([k, v]) => (
              <span key={k} className={cn("font-medium", OUTCOME_COLOR[k] ?? "text-foreground")}>
                {k.replace("_", " ")}: {v}
              </span>
            ))}
            {checkableIds.length > 0 && (
              <button
                type="button"
                className="ml-auto text-xs font-medium text-primary hover:underline"
                onClick={() =>
                  setChecked(
                    selectedCount === checkableIds.length
                      ? new Set()
                      : new Set(checkableIds),
                  )
                }
              >
                {selectedCount === checkableIds.length
                  ? "Deselect all"
                  : "Select all"}
              </button>
            )}
          </div>
          <div className="max-h-[50vh] overflow-y-auto">
            <table className="w-full text-sm">
              <thead className="sticky top-0 bg-muted/60 text-left text-xs text-muted-foreground">
                <tr>
                  <th className="w-8 px-4 py-2" />
                  <th className="px-4 py-2 font-medium">Staff</th>
                  <th className="px-4 py-2 font-medium">Outcome</th>
                  <th className="px-4 py-2 font-medium">From → To</th>
                  <th className="px-4 py-2 font-medium">Reason</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-border">
                {result.rows.map((r, i) => {
                  const checkable = r.outcome === "would_apply" && !!r.employee_id;
                  return (
                    <tr key={`${r.employee_id ?? r.staff_id ?? i}`}>
                      <td className="px-4 py-2">
                        {checkable && (
                          <input
                            type="checkbox"
                            checked={checked.has(r.employee_id as string)}
                            onChange={(e) => {
                              setChecked((prev) => {
                                const next = new Set(prev);
                                if (e.target.checked) next.add(r.employee_id as string);
                                else next.delete(r.employee_id as string);
                                return next;
                              });
                            }}
                          />
                        )}
                      </td>
                      <td className="px-4 py-2 font-mono text-xs">{r.staff_id ?? r.employee_id}</td>
                      <td
                        className={cn(
                          "px-4 py-2 capitalize",
                          OUTCOME_COLOR[r.outcome] ?? "text-foreground",
                        )}
                      >
                        {r.outcome.replace("_", " ")}
                      </td>
                      <td className="px-4 py-2 text-xs text-muted-foreground">
                        {r.from_plan ?? "—"} → {r.to_plan ?? "—"}
                      </td>
                      <td className="px-4 py-2 text-xs text-muted-foreground">{r.reason ?? ""}</td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        </div>
      )}

      <AlertDialog
        open={confirmApply}
        onOpenChange={setConfirmApply}
        title="Apply this bulk plan update?"
        description={`This writes plan overrides for ${selectedCount} member(s) on product ${productCode}. It updates their effective coverage immediately.`}
        confirmLabel="Apply update"
        confirmVariant="default"
        loading={apply.isPending}
        onConfirm={runApply}
      />
    </div>
  );
}
