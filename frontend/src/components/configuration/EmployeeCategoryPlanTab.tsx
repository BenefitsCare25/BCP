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
  useCategoryOverlaps,
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
  CategoryOverlap,
  PlanAssignment,
  PlanDetail,
  RateModel,
  TemplateTier,
  VoluntaryRateBand,
} from "@/types";
import { CategoryCard, isAgeBanded, type MemberCount } from "./CategoryCard";
import {
  groupEmployeeCategories,
  onlySavedOverlapWarnings,
  type EmployeeCategoryGroup,
} from "./employeeCategoryGroups";
import { PlanTypeSettings } from "./PlanTypeSettings";
import { VoluntaryAgeBandConfig } from "./VoluntaryAgeBandConfig";
import { UnmatchedEmployeeNotice } from "./UnmatchedEmployeeNotice";

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
  const overlapQuery = useCategoryOverlaps(props.policyYearId);
  const overlapsByCategory = useMemo(
    () => new Map((overlapQuery.data ?? []).map((item) => [item.category_id, item.employees])),
    [overlapQuery.data],
  );
  const createCategory = useCreateCategory();
  const [issuesOnly, setIssuesOnly] = useState(false);
  const [expanded, setExpanded] = useState<Set<string>>(new Set());
  const [editing, setEditing] = useState<string | null>(null);
  const hasIssue = (group: EmployeeCategoryGroup) => {
    if (groupOverlapEmployees(group, overlapsByCategory).length > 0) return true;
    return group.ruleStatus !== "validated";
  };
  const issueCount = data.groups.filter(hasIssue).length;
  const visibleGroups = issuesOnly
    ? data.groups.filter(hasIssue)
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
        productCode={props.productCode}
        groups={data.groups}
        counts={data.counts}
        employeesTotal={data.employeesTotal}
        employeesInScope={data.employeesInScope}
        employeesMatched={data.employeesMatched}
        memberCounts={data.memberCounts}
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
      {overlapQuery.isError && (
        <p role="alert" className="text-xs text-warn">
          Current overlap details are unavailable. Refresh to check the employee listing again.
        </p>
      )}
      {visibleGroups.length === 0 ? (
        <EmptyCategories issuesOnly={issuesOnly} />
      ) : (
        visibleGroups.map((group) => (
          <EmployeeCategoryRow
            key={group.key}
            group={group}
            overlapEmployees={groupOverlapEmployees(group, overlapsByCategory)}
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
            onEditRule={props.onEditRule}
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

type OverlapEmployee = CategoryOverlap["employees"][number] & {
  matching_category_ids: string[];
};

function groupOverlapEmployees(
  group: EmployeeCategoryGroup,
  byCategory: Map<string, CategoryOverlap["employees"]>,
): OverlapEmployee[] {
  const employees = new Map<string, OverlapEmployee>();
  for (const category of group.categories) {
    for (const employee of byCategory.get(category.id) ?? []) {
      const previous = employees.get(employee.employee_id);
      employees.set(employee.employee_id, {
        ...employee,
        matching_category_ids: [
          ...new Set([...(previous?.matching_category_ids ?? []), category.id]),
        ],
        other_categories: [...new Set([
          ...(previous?.other_categories ?? []),
          ...employee.other_categories,
        ])],
      });
    }
  }
  return [...employees.values()].sort((a, b) => a.staff_id.localeCompare(b.staff_id));
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
    employeesInScope: query.data?.employees_in_scope ?? null,
    employeesMatched: query.data?.employees_matched ?? null,
    memberCounts: query.data,
    countsError: query.isError,
    voluntary: getVoluntaryRates(props.categories),
  };
}

function CategorySummary({
  productCode,
  groups,
  counts,
  employeesTotal,
  employeesInScope,
  employeesMatched,
  memberCounts,
  countsError,
  hasDependants,
  issueCount,
  issuesOnly,
  onToggleIssues,
  onAdd,
  canAdd,
  adding,
}: {
  productCode: string;
  groups: EmployeeCategoryGroup[];
  counts: Record<string, MemberCount>;
  employeesTotal: number | null;
  employeesInScope: number | null;
  employeesMatched: number | null;
  memberCounts: import("@/types").MemberCounts | undefined;
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
  const unmatched =
    employeesInScope !== null && employeesMatched !== null
      ? Math.max(0, employeesInScope - employeesMatched)
      : 0;
  return (
    <div className="overflow-x-auto rounded-lg border border-border bg-card">
      <div className="flex min-w-max items-center gap-2 px-3 py-2">
        <Badge variant={issueCount === 0 ? "good" : "warn"}>
          {validated}/{groups.length} category rules pass checks
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
            {unmatched > 0 && <SummaryItem value={unmatched} label="Employees without a category" tone="warn" />}
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
      {unmatched > 0 && (
        <div className="border-t border-warn/30 p-2">
          <UnmatchedEmployeeNotice productCode={productCode} counts={memberCounts} />
        </div>
      )}
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
  overlapEmployees,
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
  overlapEmployees: OverlapEmployee[];
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
  onEditRule: (category: Category) => void;
  productEntities: string[];
}) {
  return (
    <section className="rounded-lg border border-border bg-card">
      <div className="grid grid-cols-[minmax(16rem,1fr)_auto_auto_auto] items-center gap-3 overflow-x-auto p-3">
        <button type="button" onClick={onToggle} aria-expanded={expanded} className="flex min-w-0 items-center gap-2 text-left">
          {expanded ? <ChevronDown className="size-4 shrink-0" /> : <ChevronRight className="size-4 shrink-0" />}
          <span className="truncate text-sm font-semibold text-foreground">{group.name}</span>
        </button>
        <RuleStatus
          group={group}
          employeesAvailable={employeesAvailable}
          overlapEmployees={overlapEmployees}
          expanded={expanded}
          onToggle={onToggle}
        />
        <span className="whitespace-nowrap text-xs text-muted-foreground">
          {group.categories.length} plan assignment{group.categories.length === 1 ? "" : "s"}
          {employeesAvailable && count ? ` · ${count.employees} employees${hasDependants ? ` · ${count.dependants} dependants` : ""}` : ""}
        </span>
        <Button size="sm" variant="outline" onClick={onToggle}>
          {expanded ? "Hide plan rules" : "Review plan rules"}
        </Button>
      </div>
      {expanded && (
        <div className="grid gap-2 border-t border-border p-3">
          {overlapEmployees.length === 0 && onlySavedOverlapWarnings(group) && (
            <p className="rounded-md border border-warn/40 bg-warn-soft/40 p-3 text-xs text-foreground">
              The saved rule check found an employee who also matched another category. Review both category rules before confirming. Confirmation checks again and may still find a conflict.
            </p>
          )}
          {overlapEmployees.length > 0 && (
            <div id={`overlap-details-${group.representative.id}`} className="rounded-md border border-warn/40 bg-warn-soft/40 p-3 text-xs">
              <p className="font-medium text-foreground">Active employees matching multiple categories</p>
              <ul className="mt-2 max-h-48 space-y-1 overflow-y-auto">
                {overlapEmployees.map((employee) => (
                  <li key={employee.employee_id}>
                    <span className="font-medium">{employee.employee_name || employee.staff_id}</span>
                    {employee.employee_name && <span> · {employee.staff_id}</span>}
                    {employee.job_category && <span> · Job Category Code: {employee.job_category}</span>}
                    <span>
                      {" · Matched in: "}
                      {employee.matching_category_ids.map((id) => {
                        const category = group.categories.find((item) => item.id === id);
                        return category ? planFor(category, planOptions)?.display_name || assignmentCode(category) || "Unknown plan" : "Unknown plan";
                      }).join(", ")}
                    </span>
                    <span> · Also matches: {employee.other_categories.join(", ")}</span>
                  </li>
                ))}
              </ul>
            </div>
          )}
          {group.categories.map((category) => {
            const plan = planFor(category, planOptions);
            const warning = assignmentWarning(category, group);
            return (
              <div key={category.id} className="rounded-md bg-muted/35 p-2">
                <div className="flex flex-wrap items-center gap-3">
                  <span className="flex min-w-40 flex-1 items-center gap-2 text-sm font-medium text-foreground">
                    <span className="truncate">
                      {plan?.display_name || assignmentCode(category) || "Plan type missing"}
                    </span>
                    {warning && <Badge variant="warn">{warning}</Badge>}
                    {overlapEmployees.some((employee) => employee.matching_category_ids.includes(category.id)) && (
                      <Badge variant="warn">Overlap in this plan</Badge>
                    )}
                  </span>
                  <span className="min-w-60 flex-[1.5] text-xs text-muted-foreground">{assignmentSummary(category, rateModel, hasDependants)}</span>
                  <Button size="sm" variant="outline" onClick={() => onEditRule(category)}>
                    <Pencil className="size-3.5" /> Edit rule
                  </Button>
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
                      onEditRule={() => onEditRule(category)}
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

function RuleStatus({
  group,
  employeesAvailable,
  overlapEmployees,
  expanded,
  onToggle,
}: {
  group: EmployeeCategoryGroup;
  employeesAvailable: boolean;
  overlapEmployees: OverlapEmployee[];
  expanded: boolean;
  onToggle: () => void;
}) {
  const status = group.ruleStatus;
  if (overlapEmployees.length > 0) {
    const count = overlapEmployees.length;
    return (
      <button
        type="button"
        onClick={onToggle}
        aria-expanded={expanded}
        aria-controls={expanded ? `overlap-details-${group.representative.id}` : undefined}
        aria-label={`${expanded ? "Hide" : "Show"} ${count} overlapping employees`}
      >
        <Badge variant="warn">{count} overlapping employee{count === 1 ? "" : "s"}</Badge>
      </button>
    );
  }
  if (status === "validated") {
    return group.categories.every((category) => category.status === "confirmed")
      ? <Badge variant="good">Mapping confirmed</Badge>
      : <Badge variant="info">Rule checks passed · confirm mapping</Badge>;
  }
  if (status === "unmapped") return <Badge variant="error">Employee category rule missing</Badge>;
  if (status === "proposed" && !employeesAvailable) {
    return <Badge variant="info">Proposed — awaiting employee listing</Badge>;
  }
  if (onlySavedOverlapWarnings(group)) {
    return <Badge variant="warn">Review overlap warning</Badge>;
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
