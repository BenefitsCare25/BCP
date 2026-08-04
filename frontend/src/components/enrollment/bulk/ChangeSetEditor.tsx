/**
 * What changes — one row per product, applied to the same selection.
 *
 * A renewal moves several products at once, and doing that as three separate
 * runs meant rebuilding the selection three times and leaving the roster
 * half-moved in between. The set applies as one transaction.
 *
 * Two things the rows encode that are easy to miss:
 *
 * - **The FIRST product scopes the member filter.** "Currently on" and
 *   "Coverage" resolve against one product's effective plan, and a member can be
 *   on Plan 1 of GHS and Plan 3 of GTL — so the row says which product the
 *   filter is reading, rather than leaving it ambiguous.
 * - **Revert is its own action.** Setting the plan the cohort happens to use is
 *   not the same thing: it writes an override pinning the member off future
 *   cohort changes, where a revert removes it.
 */
import { Plus, X } from "lucide-react";
import type { BulkAction, BulkCoverageChange } from "@/api/enrollment";
import { Button } from "@/components/ui/button";
import { Label } from "@/components/ui/label";
import { NativeSelect } from "@/components/ui/native-select";
import { InfoHint } from "@/components/ui/tooltip";

export type ProductOption = {
  code: string;
  name: string | null;
  plans: string[];
  hasDependants: boolean;
  /** Members whose EFFECTIVE plan is this one today — a bare `PLAN 1` / `U04`
   *  means nothing without leaving the page. */
  planHeadcount: Map<string, number>;
};

export type DependantMode = "" | "include_all" | "exclude_all";

export type ChangeDraft = {
  productCode: string;
  action: BulkAction;
  targetPlan: string;
  dependantMode: DependantMode;
};

export const EMPTY_CHANGE: ChangeDraft = {
  productCode: "",
  action: "set_plan",
  targetPlan: "",
  dependantMode: "",
};

const ACTION_LABEL: Record<BulkAction, string> = {
  set_plan: "Move to a plan",
  decline: "Decline the product",
  revert_to_default: "Revert to cohort default",
};

export function toChanges(drafts: ChangeDraft[]): BulkCoverageChange[] {
  return drafts.map((d) => ({
    product_code: d.productCode,
    action: d.action,
    target_plan_code: d.action === "set_plan" ? d.targetPlan : null,
    dependant_action:
      d.action === "set_plan" && d.dependantMode
        ? { mode: d.dependantMode, dependant_ids: [] }
        : null,
  }));
}

/** The first thing wrong with the set, or null. Mirrors the server's validators
 *  so a broker is told before a request rather than by a 422. */
export function changeSetError(drafts: ChangeDraft[]): string | null {
  if (!drafts.length) return "Add a product to change.";
  for (const d of drafts) {
    if (!d.productCode) return "Pick a product for every change.";
    if (d.action === "set_plan" && !d.targetPlan) {
      return `Pick the plan to move members to on ${d.productCode}.`;
    }
  }
  const codes = drafts.map((d) => d.productCode);
  if (new Set(codes).size !== codes.length) {
    return "Each product can appear once — combine the changes.";
  }
  return null;
}

export function ChangeSetEditor({
  drafts,
  products,
  disabled,
  onChange,
}: {
  drafts: ChangeDraft[];
  products: ProductOption[];
  disabled?: boolean;
  onChange: (next: ChangeDraft[]) => void;
}) {
  const used = new Set(drafts.map((d) => d.productCode).filter(Boolean));

  function patch(index: number, next: Partial<ChangeDraft>) {
    onChange(drafts.map((d, i) => (i === index ? { ...d, ...next } : d)));
  }

  function pickProduct(index: number, code: string) {
    const option = products.find((p) => p.code === code);
    patch(index, {
      productCode: code,
      targetPlan: "",
      // A product with no plans configured this year can only be declined or
      // reverted. Leaving "Move to a plan" selected against an empty plan list
      // is a control that cannot be completed.
      action:
        option && option.plans.length === 0 && drafts[index].action === "set_plan"
          ? "decline"
          : drafts[index].action,
      // Dependant cover means nothing on a product that has none.
      dependantMode: option?.hasDependants ? drafts[index].dependantMode : "",
    });
  }

  return (
    <div className="divide-y divide-border">
      {drafts.map((draft, index) => {
        const option = products.find((p) => p.code === draft.productCode);
        const plans = option?.plans ?? [];
        const isSetPlan = draft.action === "set_plan";
        return (
          <div key={index} className="py-3 first:pt-0 last:pb-0">
            <div className="flex items-start gap-3">
              <div className="grid flex-1 gap-3 sm:grid-cols-2 lg:grid-cols-4">
                <div>
                  <div className="flex items-center gap-1">
                    <Label htmlFor={`bulk-product-${index}`}>Product</Label>
                    {index === 0 && drafts.length > 1 && (
                      <InfoHint>
                        The member filters &ldquo;Currently on&rdquo; and
                        &ldquo;Coverage&rdquo; read this product. They resolve
                        against one product&apos;s effective plan, so scoping
                        them per change would make one filter mean two different
                        groups of people.
                      </InfoHint>
                    )}
                  </div>
                  <NativeSelect
                    id={`bulk-product-${index}`}
                    className="w-full"
                    value={draft.productCode}
                    disabled={disabled}
                    onChange={(e) => pickProduct(index, e.target.value)}
                  >
                    <option value="">Select product</option>
                    {products
                      .filter(
                        (p) => p.code === draft.productCode || !used.has(p.code),
                      )
                      .map((p) => (
                        <option key={p.code} value={p.code}>
                          {p.code}
                          {p.name && p.name !== p.code ? ` — ${p.name}` : ""}
                        </option>
                      ))}
                  </NativeSelect>
                </div>

                <div>
                  <Label htmlFor={`bulk-action-${index}`}>Action</Label>
                  <NativeSelect
                    id={`bulk-action-${index}`}
                    className="w-full"
                    value={draft.action}
                    disabled={disabled || !draft.productCode}
                    onChange={(e) =>
                      patch(index, {
                        action: e.target.value as BulkAction,
                        targetPlan: "",
                        dependantMode:
                          e.target.value === "set_plan" ? draft.dependantMode : "",
                      })
                    }
                  >
                    <option value="set_plan" disabled={plans.length === 0}>
                      {ACTION_LABEL.set_plan}
                    </option>
                    <option value="decline">{ACTION_LABEL.decline}</option>
                    <option value="revert_to_default">
                      {ACTION_LABEL.revert_to_default}
                    </option>
                  </NativeSelect>
                </div>

                <div>
                  <Label htmlFor={`bulk-target-${index}`}>Move to plan</Label>
                  <NativeSelect
                    id={`bulk-target-${index}`}
                    className="w-full"
                    value={draft.targetPlan}
                    disabled={disabled || !isSetPlan || !plans.length}
                    onChange={(e) => patch(index, { targetPlan: e.target.value })}
                  >
                    <option value="">
                      {draft.productCode && !plans.length
                        ? "No plans configured this year"
                        : !isSetPlan
                          ? "—"
                          : "Select plan"}
                    </option>
                    {plans.map((code) => (
                      <option key={code} value={code}>
                        {code}
                        {option?.planHeadcount.has(code)
                          ? ` — ${option.planHeadcount.get(code)} today`
                          : ""}
                      </option>
                    ))}
                  </NativeSelect>
                </div>

                <div>
                  <div className="flex items-center gap-1">
                    <Label htmlFor={`bulk-deps-${index}`}>Dependant cover</Label>
                    <InfoHint>
                      Leave unchanged unless you mean to move it — &ldquo;Cover
                      all&rdquo; elects every active dependant, &ldquo;Cover
                      none&rdquo; removes them all. A revert restores the
                      cohort&apos;s own dependant cover, so it takes no setting.
                    </InfoHint>
                  </div>
                  <NativeSelect
                    id={`bulk-deps-${index}`}
                    className="w-full"
                    value={draft.dependantMode}
                    disabled={
                      disabled || !isSetPlan || !option?.hasDependants
                    }
                    onChange={(e) =>
                      patch(index, {
                        dependantMode: e.target.value as DependantMode,
                      })
                    }
                  >
                    <option value="">
                      {option && !option.hasDependants
                        ? "No dependant cover on this product"
                        : "Leave unchanged"}
                    </option>
                    <option value="include_all">Cover all dependants</option>
                    <option value="exclude_all">Cover no dependants</option>
                  </NativeSelect>
                </div>
              </div>

              {drafts.length > 1 && (
                <Button
                  variant="ghost"
                  size="icon"
                  aria-label={`Remove the ${draft.productCode || "empty"} change`}
                  className="mt-5 shrink-0"
                  disabled={disabled}
                  onClick={() =>
                    onChange(drafts.filter((_, i) => i !== index))
                  }
                >
                  <X className="size-4" />
                </Button>
              )}
            </div>
          </div>
        );
      })}

      <div className="pt-3">
        <Button
          variant="outline"
          size="sm"
          disabled={disabled || drafts.length >= products.length || drafts.length >= 10}
          onClick={() => onChange([...drafts, EMPTY_CHANGE])}
        >
          <Plus className="size-4" /> Add another product
        </Button>
      </div>
    </div>
  );
}
