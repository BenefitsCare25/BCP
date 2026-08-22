import { useMemo, useState } from "react";
import {
  ChevronDown,
  ChevronRight,
  Pencil,
  Plus,
  SlidersHorizontal,
} from "lucide-react";
import { toast } from "sonner";
import {
  useCreateCategory,
  useMemberCounts,
  usePlans,
  useProducts,
} from "@/api/hooks";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { formatError } from "@/lib/errors";
import { insuredNames } from "@/lib/insured";
import type {
  BasisModel,
  Category,
  EligibilityRuleStatus,
  PlanAssignment,
  PlanDetail,
  RateModel,
  TemplateTier,
  VoluntaryRateBand,
} from "@/types";
import { CategoryCard, isAgeBanded, type MemberCount } from "./CategoryCard";
import {
  groupEmployeeCategories,
  type EmployeeCategoryGroup,
} from "./employeeCategoryGroups";
import { PlanTypeSettings } from "./PlanTypeSettings";
import { VoluntaryAgeBandConfig } from "./VoluntaryAgeBandConfig";

interface Props {
  policyYearId: string;
  productCode: string;
  productId: string | null;
  hasDependants: boolean;
  basisModel: BasisModel;
  rateModel: RateModel;
  tiers: TemplateTier[];
  categories: Category[];
  onEditRule: (category: Category) => void;
}

export function EmployeeCategoryPlanTab(props: Props) {
  const data = useEmployeeCategoryData(props);
  const createCategory = useCreateCategory();
  const [issuesOnly, setIssuesOnly] = useState(false);
  const [expanded, setExpanded] = useState<Set<string>>(new Set());
  const [editing, setEditing] = useState<string | null>(null);
  const issueCount = data.groups.filter(
    (group) => group.ruleStatus !== "validated",
  ).length;
  const visibleGroups = issuesOnly
    ? data.groups.filter((group) => group.ruleStatus !== "validated")
    : data.groups;

  const addCategory = () =>
    createCategory.mutate(
      {
        policy_year_id: props.policyYearId,
        product_id: props.productId,
        display_name: "New employee category",
        participation_model: "compulsory",
      },
      {
        onSuccess: () => toast.success("Employee category added"),
        onError: (error) => toast.error(formatError(error)),
      },
    );

  const toggleGroup = (key: string) =>
    setExpanded((current) => {
      const next = new Set(current);
      if (next.has(key)) next.delete(key);
      else next.add(key);
      return next;
    });

  return (
    <div className="flex flex-col gap-3">
      <CategorySummary
        groups={data.groups}
        counts={data.counts}
        employeesTotal={data.employeesTotal}
        countsError={data.countsError}
        hasDependants={props.hasDependants}
        issueCount={issueCount}
        issuesOnly={issuesOnly}
        onToggleIssues={() => setIssuesOnly((value) => !value)}
        onAdd={addCategory}
        canAdd={Boolean(props.productId)}
        adding={createCategory.isPending}
      />
      <PlanTypeSettings
        plans={data.planOptions}
        policyYearId={props.policyYearId}
        productId={props.productId}
      />
      {visibleGroups.length === 0 ? (
        <EmptyCategories issuesOnly={issuesOnly} />
      ) : (
        visibleGroups.map((group) => (
          <EmployeeCategoryRow
            key={group.key}
            group={group}
            count={data.counts[group.key]}
            employeesAvailable={(data.employeesTotal ?? 0) > 0}
            hasDependants={props.hasDependants}
            planOptions={data.planOptions}
            basisModel={props.basisModel}
            rateModel={props.rateModel}
            tiers={props.tiers}
            expanded={expanded.has(group.key)}
            editing={editing}
            onToggle={() => toggleGroup(group.key)}
            onEditAssignment={(id) => setEditing((current) => (current === id ? null : id))}
            onEditRule={() => props.onEditRule(group.representative)}
            productEntities={data.productEntities}
          />
        ))
      )}
      {data.voluntary && props.productId && (
        <VoluntaryAgeBandConfig
          policyYearId={props.policyYearId}
          productId={props.productId}
          bands={data.voluntary.bands}
          planCount={data.voluntary.planCount}
        />
      )}
    </div>
  );
}

function useEmployeeCategoryData(props: Props) {
  const { data: plans } = usePlans(
    props.policyYearId,
    props.productId ?? undefined,
  );
  const { data: products } = useProducts();
  const planOptions = useMemo(() => sortPlans(plans?.items ?? []), [plans]);
  const productEntities = useMemo(
    () =>
      insuredNames(
        products?.find((item) => item.id === props.productId)?.entities,
      ),
    [products, props.productId],
  );
  const groups = useMemo(
    () => groupEmployeeCategories(props.categories),
    [props.categories],
  );
  const countArgs = useMemo(
    () =>
      groups.map((group) => ({
        key: group.key,
        description: group.representative.raw_description || group.name,
        insured: productEntities.length
          ? productEntities
          : insuredNames(
              (group.representative.plan_assignments as PlanAssignment | null)
                ?.insured,
            ),
      })),
    [groups, productEntities],
  );
  const query = useMemberCounts(
    props.policyYearId,
    props.productCode,
    props.hasDependants,
    countArgs,
  );
  const counts = useMemo(
    () =>
      Object.fromEntries(
        (query.data?.counts ?? []).map((row) => [row.key, row]),
      ),
    [query.data],
  );
  return {
    groups,
    counts,
    planOptions,
    productEntities,
    employeesTotal: query.data?.employees_total ?? null,
    countsError: query.isError,
    voluntary: getVoluntaryRates(props.categories),
  };
}

function CategorySummary({
  groups,
  counts,
  employeesTotal,
  countsError,
  hasDependants,
  issueCount,
  issuesOnly,
  onToggleIssues,
  onAdd,
  canAdd,
  adding,
}: {
  groups: EmployeeCategoryGroup[];
  counts: Record<string, MemberCount>;
  employeesTotal: number | null;
  countsError: boolean;
  hasDependants: boolean;
  issueCount: number;
  issuesOnly: boolean;
  onToggleIssues: () => void;
  onAdd: () => void;
  canAdd: boolean;
  adding: boolean;
}) {
  const validated = groups.length - issueCount;
  const employees = Object.values(counts).reduce((total, row) => total + row.employees, 0);
  const dependants = Object.values(counts).reduce((total, row) => total + row.dependants, 0);
  return (
    <div className="overflow-x-auto rounded-lg border border-border bg-card">
      <div className="flex min-w-max items-center gap-2 px-3 py-2">
        <Badge variant={issueCount === 0 ? "good" : "warn"}>
          {validated}/{groups.length} Employee Category Validated
        </Badge>
        {countsError ? (
          <Badge variant="error">Eligibility count unavailable</Badge>
        ) : employeesTotal === null ? (
          <Badge variant="outline">Calculating eligibility</Badge>
        ) : employeesTotal === 0 ? (
          <Badge variant="outline">No employee listing</Badge>
        ) : (
          <>
            <SummaryItem value={employees} label="Eligible Employees" />
            {hasDependants && <SummaryItem value={dependants} label="Eligible Dependants" />}
          </>
        )}
        <div className="ml-auto flex items-center gap-2 pl-3">
          {issueCount > 0 && (
            <Button
              size="icon-sm"
              variant={issuesOnly ? "secondary" : "outline"}
              onClick={onToggleIssues}
              aria-label={
                issuesOnly
                  ? "Show all employee categories"
                  : "Show employee categories needing attention"
              }
              title={
                issuesOnly
                  ? "Show all employee categories"
                  : "Show employee categories needing attention"
              }
            >
              <SlidersHorizontal className="size-3.5" />
            </Button>
          )}
          <Button size="sm" variant="outline" onClick={onAdd} disabled={!canAdd || adding}>
            <Plus className="size-3.5" /> Add employee category
          </Button>
        </div>
      </div>
    </div>
  );
}

function SummaryItem({
  value,
  label,
  tone = "default",
}: {
  value: number;
  label: string;
  tone?: "default" | "good" | "warn" | "muted";
}) {
  const variant = tone === "good" ? "good" : tone === "warn" ? "warn" : "outline";
  return (
    <Badge variant={variant} className={tone === "muted" ? "text-muted-foreground" : undefined}>
      {value} {label}
    </Badge>
  );
}

function EmployeeCategoryRow({
  group,
  count,
  employeesAvailable,
  hasDependants,
  planOptions,
  basisModel,
  rateModel,
  tiers,
  expanded,
  editing,
  onToggle,
  onEditAssignment,
  onEditRule,
  productEntities,
}: {
  group: EmployeeCategoryGroup;
  count?: MemberCount;
  employeesAvailable: boolean;
  hasDependants: boolean;
  planOptions: PlanDetail[];
  basisModel: BasisModel;
  rateModel: RateModel;
  tiers: TemplateTier[];
  expanded: boolean;
  editing: string | null;
  onToggle: () => void;
  onEditAssignment: (id: string) => void;
  onEditRule: () => void;
  productEntities: string[];
}) {
  return (
    <section className="rounded-lg border border-border bg-card">
      <div className="grid grid-cols-[minmax(16rem,1fr)_auto_auto_auto] items-center gap-3 overflow-x-auto p-3">
        <button type="button" onClick={onToggle} aria-expanded={expanded} className="flex min-w-0 items-center gap-2 text-left">
          {expanded ? <ChevronDown className="size-4 shrink-0" /> : <ChevronRight className="size-4 shrink-0" />}
          <span className="truncate text-sm font-semibold text-foreground">{group.name}</span>
        </button>
        <RuleStatus status={group.ruleStatus} employeesAvailable={employeesAvailable} />
        <span className="whitespace-nowrap text-xs text-muted-foreground">
          {group.categories.length} plan assignment{group.categories.length === 1 ? "" : "s"}
          {employeesAvailable && count ? ` · ${count.employees} employees${hasDependants ? ` · ${count.dependants} dependants` : ""}` : ""}
        </span>
        <Button size="sm" variant="outline" onClick={onEditRule}>
          <Pencil className="size-3.5" /> Employee category rule
        </Button>
      </div>
      {expanded && (
        <div className="grid gap-2 border-t border-border p-3">
          {group.categories.map((category) => {
            const plan = planFor(category, planOptions);
            const warning = assignmentWarning(category, group);
            return (
              <div key={category.id} className="rounded-md bg-muted/35 p-2">
                <div className="grid grid-cols-[minmax(10rem,0.8fr)_minmax(15rem,1.5fr)_auto] items-center gap-3">
                  <span className="flex min-w-0 items-center gap-2 text-sm font-medium text-foreground">
                    <span className="truncate">
                      {plan?.display_name || assignmentCode(category) || "Plan type missing"}
                    </span>
                    {warning && <Badge variant="warn">{warning}</Badge>}
                  </span>
                  <span className="truncate text-xs text-muted-foreground">{assignmentSummary(category, rateModel, hasDependants)}</span>
                  <Button size="sm" variant="ghost" onClick={() => onEditAssignment(category.id)}>
                    {editing === category.id ? "Close settings" : "Edit assignment"}
                  </Button>
                </div>
                {editing === category.id && (
                  <div className="mt-2">
                    <CategoryCard
                      category={category}
                      planOptions={planOptions}
                      basisModel={basisModel}
                      rateModel={rateModel}
                      tiers={tiers}
                      hasDependants={hasDependants}
                      insuredEntities={productEntities}
                      onEditRule={onEditRule}
                      assignmentOnly
                    />
                  </div>
                )}
              </div>
            );
          })}
        </div>
      )}
    </section>
  );
}

function RuleStatus({ status, employeesAvailable }: { status: EligibilityRuleStatus; employeesAvailable: boolean }) {
  if (status === "validated") return <Badge variant="good">Rule validated</Badge>;
  if (status === "unmapped") return <Badge variant="error">Employee category rule missing</Badge>;
  if (status === "proposed" && !employeesAvailable) {
    return <Badge variant="info">Proposed — awaiting employee listing</Badge>;
  }
  return <Badge variant="warn">Rule needs attention</Badge>;
}

function assignmentSummary(category: Category, rateModel: RateModel, hasDependants: boolean): string {
  const assignment = (category.plan_assignments ?? {}) as PlanAssignment;
  const employee = category.participation_detail?.employee || category.participation_model || "not set";
  const dependant = category.participation_detail?.dependant || "not covered";
  const parts = [`Employee: ${labelValue(employee)}`];
  if (rateModel === "tiered") parts.push(`${Object.keys(assignment.rate_tiers ?? {}).length} premium tiers`);
  else if (rateModel === "flat") parts.push(`Annual premium: ${money(assignment.annual_premium)}`);
  else if (assignment.premium_rate != null) parts.push(`Employee rate: ${money(assignment.premium_rate)}`);
  if (hasDependants) {
    parts.push(`Dependants: ${labelValue(dependant)}`);
    if (rateModel !== "tiered" && assignment.dependant_rate != null) parts.push(`Dependant rate: ${money(assignment.dependant_rate)}`);
  }
  if (assignment.num_employees != null) parts.push(`Slip states ${assignment.num_employees} employees`);
  return parts.join(" · ");
}

function assignmentCode(category: Category): string {
  return String((category.plan_assignments as PlanAssignment | null)?.plan_code ?? "");
}

function assignmentWarning(
  category: Category,
  group: EmployeeCategoryGroup,
): string | null {
  const code = assignmentCode(category).trim().toLocaleLowerCase();
  if (!code) return "Plan type missing";
  const samePlan = group.categories.filter(
    (item) => assignmentCode(item).trim().toLocaleLowerCase() === code,
  );
  return samePlan.length > 1 ? "Duplicate assignment" : null;
}

function planFor(category: Category, plans: PlanDetail[]): PlanDetail | undefined {
  const code = assignmentCode(category);
  return plans.find((plan) => String(plan.code) === code);
}

function money(value: number | null | undefined): string {
  return value == null ? "Not set" : value.toLocaleString("en-SG", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
}

function labelValue(value: string): string {
  return value.replaceAll("_", " ").replace(/^./, (letter) => letter.toUpperCase());
}

function sortPlans(plans: PlanDetail[]): PlanDetail[] {
  return [...plans].sort((a, b) =>
    (a.display_name || a.code).localeCompare(b.display_name || b.code, undefined, { numeric: true }),
  );
}

function getVoluntaryRates(categories: Category[]): { bands: VoluntaryRateBand[]; planCount: number } | null {
  const banded = categories.filter(isAgeBanded);
  if (banded.length === 0) return null;
  const assignment = (banded[0].plan_assignments ?? {}) as PlanAssignment & { voluntary_rates?: VoluntaryRateBand[] | null };
  return { bands: assignment.voluntary_rates ?? [], planCount: banded.length };
}

function EmptyCategories({ issuesOnly }: { issuesOnly: boolean }) {
  return (
    <p className="rounded-lg border border-border bg-card p-4 text-sm text-muted-foreground">
      {issuesOnly ? "No employee category rules need attention." : "No employee categories yet. Add one to define who is covered."}
    </p>
  );
}
