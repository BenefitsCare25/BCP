import { useMemo } from "react";
import { Loader2 } from "lucide-react";
import {
  useProductSetups,
  useProductTerms,
  useSetupTemplate,
} from "@/api/hooks";
import { CoveragePeriodEditor } from "./CoveragePeriodEditor";
import { ProductSetupForm } from "./ProductSetupForm";
import { ProductSetupSummary } from "./ProductSetupSummary";
import type { Category, CategoryGroup } from "@/types";

interface Props {
  policyYearId: string;
  code: string;
  // The extracted-category group for this product, if any.
  group?: CategoryGroup;
  // Opens the slim rule editor (trimmed details panel) for a category.
  onSelectCategory: (c: Category) => void;
  isEditing: boolean;
  onDone: () => void;
  onDirtyChange?: (dirty: boolean, sections: string[]) => void;
}

export function ProductConfigurator({
  policyYearId,
  code,
  group,
  onSelectCategory,
  isEditing,
  onDone,
  onDirtyChange,
}: Props) {
  const { data: template, isLoading: loadingTpl } = useSetupTemplate(
    policyYearId,
    code,
  );
  const { data: setups = [] } = useProductSetups(policyYearId);
  const { data: terms = [] } = useProductTerms(policyYearId);

  const codeKey = code.toUpperCase();
  const draft =
    setups.find((s) => s.product_code.toUpperCase() === codeKey) ?? null;
  const term = useMemo(
    () => terms.find((t) => t.code.toUpperCase() === codeKey) ?? null,
    [terms, codeKey],
  );

  if (loadingTpl) {
    return (
      <div className="flex items-center gap-2 py-6 text-sm text-muted-foreground">
        <Loader2 className="size-4 animate-spin" /> Loading template...
      </div>
    );
  }

  if (!template) {
    return (
      <p className="py-6 text-sm text-muted-foreground">
        No setup template available for {code}.
      </p>
    );
  }

  if (!isEditing) {
    return (
      <ProductSetupSummary
        template={template}
        draft={draft}
        group={group}
        term={term}
      />
    );
  }

  return (
    <div>
      <ProductSetupForm
        // Key on the product code only, not draft.id. Keying on draft.id
        // remounted the form the moment the first save created a draft, which
        // discarded edits typed while that save was in flight.
        key={code}
        policyYearId={policyYearId}
        template={template}
        draft={draft}
        group={group}
        onEditRule={onSelectCategory}
        onConfirmed={onDone}
        onDirtyChange={onDirtyChange}
        insuranceLine={term?.line ?? "medical"}
        termEditor={
          term ? (
            <CoveragePeriodEditor
              // Remount on server values so save/reset discards local edits.
              key={`${term.product_id}:${term.coverage_start}:${term.coverage_end}:${term.is_default}:${term.gst_included}:${term.gst_rate ?? ""}:${term.free_cover_limit ?? ""}:${term.nel_age_limit ?? ""}:${term.underwriting_required}`}
              policyYearId={policyYearId}
              term={term}
            />
          ) : null
        }
      />
    </div>
  );
}
