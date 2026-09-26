/** "Edit at source" links into Company & Benefits.
 *
 * Member Coverage shows values that are DERIVED from a product's setup — the
 * plan a cohort maps to, its rates, the schedule, the matching rule. When one
 * reads wrong, the fix is never on the coverage page; these links open the
 * exact place it is edited instead of leaving the broker to find it.
 */

export type SetupSection =
  | "header"
  | "eligibility"
  | "basis_of_cover"
  | "schedule_of_benefits"
  | "claim_limits";

export interface SetupSearch {
  tab?: string;
  product?: string;
  section?: string;
  category?: string;
}

export const SETUP_PATH = "/client-relations/company-benefits" as const;

export function setupSearch(
  productCode: string,
  opts: { section?: SetupSection; categoryId?: string | null } = {},
): SetupSearch {
  return {
    product: productCode,
    ...(opts.section ? { section: opts.section } : {}),
    ...(opts.categoryId ? { category: opts.categoryId } : {}),
  };
}

export const SECTION_LABEL: Record<SetupSection, string> = {
  header: "Policy period & GST",
  eligibility: "Eligibility",
  basis_of_cover: "Plans & rates",
  schedule_of_benefits: "Schedule of benefits",
  claim_limits: "Claim limits",
};
