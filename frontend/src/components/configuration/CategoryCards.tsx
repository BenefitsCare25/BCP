import { useMemo } from "react";
import { Plus } from "lucide-react";
import { Button } from "@/components/ui/button";
import {
  useCreateCategory,
  useMemberCounts,
  usePlans,
  useProducts,
} from "@/api/hooks";
import { formatError } from "@/lib/errors";
import { insuredNames } from "@/lib/insured";
import type {
  BasisModel,
  Category,
  PlanAssignment,
  RateModel,
  TemplateTier,
  VoluntaryRateBand,
} from "@/types";
import { toast } from "sonner";
import { VoluntaryAgeBandConfig } from "./VoluntaryAgeBandConfig";
import { CategoryCard, isAgeBanded, type MemberCount } from "./CategoryCard";

interface Props {
  policyYearId: string;
  productCode: string;
  productId: string | null;
  hasDependants: boolean;
  // Only sum-assured products (GCI/GTL/GPA) carry an "amount covered per
  // employee"; medical/outpatient coverage is a schedule, not a single amount.
  basisModel: BasisModel;
  // Drives the inline rate editor on each card: per_member → one premium/member,
  // tiered → an EO/ES/EC/EF rate grid, per_1000_si → amount-covered rate.
  rateModel: RateModel;
  tiers: TemplateTier[];
  categories: Category[];
  // Opens the slim rule editor (trimmed details panel) for this category.
  onEditRule: (c: Category) => void;
}

export function CategoryCards({
  policyYearId,
  productCode,
  productId,
  hasDependants,
  basisModel,
  rateModel,
  tiers,
  categories,
  onEditRule,
}: Props) {
  const { data: plans } = usePlans(policyYearId, productId ?? undefined);
  const createCategory = useCreateCategory();

  // Plan types offered by this product, listed ascending (numeric-aware so
  // "Plan 2" sorts before "Plan 10").
  const planOptions = useMemo(
    () =>
      [...(plans?.items ?? [])].sort((a, b) =>
        (a.display_name || a.code).localeCompare(b.display_name || b.code, undefined, {
          numeric: true,
        }),
      ),
    [plans],
  );

  // The entities actually gating this product's categories, in the matcher's
  // precedence: the product-level field when set, else each category's own
  // slip-parsed `insured`. Shown on every card so the gate is visible where the
  // categories are — it is now chosen on the header tab, and a card that gives
  // no hint of it looks unrestricted when it is not.
  const { data: products } = useProducts();
  const productEntities = useMemo(
    () => insuredNames(products?.find((p) => p.id === productId)?.entities),
    [products, productId],
  );
  const entitiesFor = (c: Category): string[] =>
    productEntities.length
      ? productEntities
      : insuredNames((c.plan_assignments as PlanAssignment | null)?.insured);

  // Live roster headcount per category, matched off its description. The
  // category's insured entities ride along so multi-subsidiary products count
  // each entity's employees separately (same gate as real matching).
  const countArgs = useMemo(
    () =>
      categories.map((c) => ({
        key: c.id,
        description: c.raw_description || c.display_name,
        // Server-side the product field wins anyway; sending it keeps the
        // request honest when a category carries a different slip value.
        insured: productEntities.length
          ? productEntities
          : insuredNames((c.plan_assignments as PlanAssignment | null)?.insured),
      })),
    [categories, productEntities],
  );
  const { data: memberCounts, isError: countsError } = useMemberCounts(
    policyYearId,
    productCode,
    hasDependants,
    countArgs,
  );
  const countsByKey = useMemo(() => {
    const map: Record<string, MemberCount> = {};
    for (const c of memberCounts?.counts ?? []) {
      map[c.key] = { employees: c.employees, dependants: c.dependants };
    }
    return map;
  }, [memberCounts]);

  // The product-wide voluntary age-band rate table (shared by all its voluntary
  // plans), read off the first age-banded voluntary category. Shown ONCE at the
  // bottom instead of repeated under each plan. null → product has no age-banded
  // voluntary plans (compulsory-only or flat voluntary) → panel hidden.
  const voluntary = useMemo(() => {
    const banded = categories.filter(isAgeBanded);
    if (banded.length === 0) return null;
    const pa = (banded[0].plan_assignments ?? {}) as {
      voluntary_rates?: VoluntaryRateBand[] | null;
    };
    return { bands: pa.voluntary_rates ?? [], planCount: banded.length };
  }, [categories]);

  const addCategory = () =>
    createCategory.mutate(
      {
        policy_year_id: policyYearId,
        product_id: productId,
        display_name: "New category",
        participation_model: "compulsory",
      },
      {
        onSuccess: () => toast.success("Category added — edit its details below"),
        onError: (e) => toast.error(formatError(e)),
      },
    );

  return (
    <div className="flex flex-col gap-3">
      <div className="flex items-center justify-between">
        <p className="text-xs text-muted-foreground">
          Each card is an employee category. Edit its plan type, participation,
          {basisModel === "sum_assured"
            ? " amount covered per employee, and rate"
            : " rate and premium"}
          . Changes save automatically; member counts match from the roster.
        </p>
        <Button
          size="sm"
          variant="outline"
          onClick={addCategory}
          disabled={createCategory.isPending || !productId}
          title={
            productId
              ? undefined
              : "Confirm & create the product before adding categories"
          }
        >
          <Plus className="size-3.5" /> Add category
        </Button>
      </div>

      {categories.length === 0 ? (
        <p className="text-xs text-muted-foreground">
          No categories yet. Add one to define who is covered.
        </p>
      ) : (
        categories.map((c) => {
          // Resolve the live name of this card's assigned plan; folding it into
          // the key remounts the card (refreshing the Radix Select's cached
          // label) when the plan is renamed from any other card.
          const assignedCode = (c.plan_assignments as PlanAssignment | null)
            ?.plan_code;
          const planName =
            planOptions.find((p) => String(p.code) === String(assignedCode ?? ""))
              ?.display_name ?? "";
          return (
            <CategoryCard
              // Include updated_at + the assigned plan name so the card remounts
              // (and its local field state re-inits from fresh props) whenever the
              // category changes server-side — a rule-panel rename, a sibling edit,
              // or a plan-type rename.
              key={`${c.id}:${c.updated_at}:${planName}`}
              category={c}
              planOptions={planOptions}
              basisModel={basisModel}
              rateModel={rateModel}
              tiers={tiers}
              hasDependants={hasDependants}
              count={countsByKey[c.id]}
              countsError={countsError}
              insuredEntities={entitiesFor(c)}
              onEditRule={() => onEditRule(c)}
            />
          );
        })
      )}

      {voluntary && productId && (
        <VoluntaryAgeBandConfig
          policyYearId={policyYearId}
          productId={productId}
          bands={voluntary.bands}
          planCount={voluntary.planCount}
        />
      )}
    </div>
  );
}
