import type { PolicyNumberAssignment, ProductTerm } from "@/types";

const separators = /[,;\r\n]+|\s+\/\s+/;
const placeholders = new Set(["tba", "tbc", "tbd", "pending", "n/a", "na", "none", "not issued", "-"]);

export function policySourceNumbers(source: string): string[] {
  return [...new Set(source.split(separators).map((s) => s.trim()).filter((s) => s && !placeholders.has(s.toLowerCase())))];
}

export function setupPolicyMappings(source: string, mappings: PolicyNumberAssignment[] | undefined, term: ProductTerm | null): PolicyNumberAssignment[] {
  if (mappings !== undefined) {
    if (!Array.isArray(mappings)) return [];
    return mappings.map((item) => ({
      entity: item?.entity == null ? null : String(item.entity),
      policy_number: String(item?.policy_number ?? ""),
    }));
  }
  if (term?.policy_number_mappings != null) return term.policy_number_mappings;
  const numbers = policySourceNumbers(source || term?.policy_number || "");
  return numbers.length === 1 && numbers[0].length <= 64 ? [{ entity: null, policy_number: numbers[0] }] : [];
}

export function policyMappingIssue(source: string, mappings: PolicyNumberAssignment[]): string | null {
  if (mappings.length > 100) return "Use at most 100 policy-number assignments.";
  const entities = new Set<string>();
  for (const item of mappings) {
    const entity = item.entity?.trim().replace(/\s+/g, " ").toLowerCase() ?? "";
    if (item.entity !== null && !entity) return "Enter a legal entity name or choose All covered entities.";
    if (entities.has(entity)) return "Each entity can have only one policy number for this product.";
    entities.add(entity);
    const number = item.policy_number.trim();
    if (!number || number.length > 64 || separators.test(number) || policySourceNumbers(number).length !== 1) {
      return "Enter one issued policy number per assignment (up to 64 characters).";
    }
  }
  if (!mappings.length && policySourceNumbers(source).length > 1) return "Review the source list and assign the policy number for this product and its covered entities before confirming.";
  return null;
}
