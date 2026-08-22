import { insuredNames } from "@/lib/insured";
import type { Category, EligibilityRuleStatus, PlanAssignment } from "@/types";

export interface EmployeeCategoryGroup {
  key: string;
  name: string;
  categories: Category[];
  representative: Category;
  ruleStatus: EligibilityRuleStatus;
}

const normalize = (value: string) =>
  value.trim().toLocaleLowerCase().replace(/\s+/g, " ");

function identity(category: Category): string {
  const wording = normalize(category.raw_description || category.display_name);
  const entities = insuredNames(
    (category.plan_assignments as PlanAssignment | null)?.insured,
  )
    .map(normalize)
    .sort()
    .join("|");
  return `${wording}::${entities}`;
}

const STATUS_PRIORITY: Record<EligibilityRuleStatus, number> = {
  validated: 0,
  proposed: 1,
  needs_review: 2,
  unmapped: 3,
};

function groupStatus(categories: Category[]): EligibilityRuleStatus {
  return categories.reduce<EligibilityRuleStatus>((current, category) => {
    const next = category.rule_status ?? "unmapped";
    return STATUS_PRIORITY[next] > STATUS_PRIORITY[current] ? next : current;
  }, "validated");
}

export function groupEmployeeCategories(
  categories: Category[],
): EmployeeCategoryGroup[] {
  const groups = new Map<string, Category[]>();
  for (const category of categories) {
    const key = identity(category);
    groups.set(key, [...(groups.get(key) ?? []), category]);
  }
  return [...groups.entries()]
    .map(([key, members]) => ({
      key,
      name: members[0].raw_description || members[0].display_name,
      categories: [...members].sort((a, b) => a.priority - b.priority),
      representative: [...members].sort((a, b) => {
        const status =
          STATUS_PRIORITY[a.rule_status ?? "unmapped"] -
          STATUS_PRIORITY[b.rule_status ?? "unmapped"];
        return status || a.priority - b.priority;
      })[0],
      ruleStatus: groupStatus(members),
    }))
    .sort((a, b) => a.representative.priority - b.representative.priority);
}

export function employeeCategoryIssueCount(categories: Category[]): number {
  return groupEmployeeCategories(categories).filter(
    (group) => group.ruleStatus !== "validated",
  ).length;
}
