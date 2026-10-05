import type { Category } from "@/types";

function stableValue(value: unknown): unknown {
  if (Array.isArray(value)) return value.map(stableValue);
  if (value && typeof value === "object") {
    return Object.fromEntries(Object.entries(value)
      .filter(([, item]) => item != null)
      .sort(([left], [right]) => left.localeCompare(right))
      .map(([key, item]) => [key, stableValue(item)]));
  }
  return typeof value === "string" ? value.trim().replace(/\s+/g, " ") : value;
}

/** Fold repeated choices without rewriting the category ID already assigned to
 * the employee. Distinct plans, insured entities and coverage terms stay visible. */
export function employeeMappingChoices(categories: Category[], selected: Set<string>): Category[] {
  const choices = new Map<string, Category>();
  for (const category of categories) {
    if (String(category.plan_assignments?.member_scope ?? "employee").trim().toLowerCase() === "dependant") continue;
    const assignments = { ...category.plan_assignments };
    delete assignments.member_scope;
    if (assignments.plan_code != null) assignments.plan_code = String(assignments.plan_code).trim();
    const key = JSON.stringify(stableValue({
      name: category.display_name.trim().replace(/\s+/g, " ").toLowerCase(),
      assignments,
      employee: category.participation_detail?.employee ?? category.participation_model,
      dependant: category.participation_detail?.dependant,
      direction: category.participation_detail?.direction,
    }));
    const previous = choices.get(key);
    if (!previous || (selected.has(category.id) && !selected.has(previous.id))) {
      choices.set(key, category);
    }
  }
  return [...choices.values()];
}

export function mappingPlanDescription(category: Category): string | null {
  const direction = category.participation_detail?.direction?.toLowerCase();
  if (direction === "upgrade") return "Upgrade option";
  if (direction === "downgrade") return "Downgrade option";
  const participation = category.participation_detail?.employee ?? category.participation_model;
  if (participation === "compulsory") return "Compulsory plan";
  if (participation === "voluntary") return "Voluntary option";
  return null;
}
