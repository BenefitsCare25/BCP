import { useMemo } from "react";
import { Loader2 } from "lucide-react";
import {
  useProductSetups,
  useProductTerms,
  useSetupTemplate,
} from "@/api/hooks";
import { ProductSetupForm } from "./ProductSetupForm";
import { CoveragePeriodEditor } from "./CoveragePeriodEditor";
import type { Category, CategoryGroup } from "@/types";

interface Props {
  policyYearId: string;
  code: string;
  // The extracted-category group for this product, if any.
  group?: CategoryGroup;
  // Opens the slim rule editor (trimmed details panel) for a category.
  onSelectCategory: (c: Category) => void;
}

export function ProductConfigurator({
  policyYearId,
  code,
  group,
  onSelectCategory,
}: Props) {
  const { data: template, isLoading: loadingTpl } = useSetupTemplate(
    policyYearId,
    code,
  );
  const { data: setups = [] } = useProductSetups(policyYearId);
  const { data: terms = [] } = useProductTerms(policyYearId);

  const draft = setups.find((s) => s.product_code === code) ?? null;
  const term = useMemo(
    () => terms.find((t) => t.code === code) ?? null,
    [terms, code],
  );

  return (
    <div className="space-y-5">
      {term && (
        <CoveragePeriodEditor
          // The editor's inputs are a pure function of server state — remount
          // on the server values so save/reset discards local edits.
          key={`${term.product_id}:${term.coverage_start}:${term.coverage_end}:${term.is_default}:${term.gst_included}:${term.gst_rate ?? ""}:${term.free_cover_limit ?? ""}`}
          policyYearId={policyYearId}
          term={term}
        />
      )}

      {loadingTpl ? (
        <div className="flex items-center gap-2 py-6 text-sm text-muted-foreground">
          <Loader2 className="size-4 animate-spin" /> Loading template…
        </div>
      ) : !template ? (
        <p className="py-6 text-sm text-muted-foreground">
          No setup template available for {code}.
        </p>
      ) : (
        <ProductSetupForm
          // Key on the product code only — NOT draft.id. Keying on draft.id
          // remounted the form the moment the first save created a draft, which
          // discarded any edits typed while that save was in flight. The form
          // instead rebuilds from a genuinely different draft via an effect (see
          // ProductSetupForm) so a slip re-upload still refreshes it.
          key={code}
          policyYearId={policyYearId}
          template={template}
          draft={draft}
          group={group}
          onEditRule={onSelectCategory}
        />
      )}
    </div>
  );
}
