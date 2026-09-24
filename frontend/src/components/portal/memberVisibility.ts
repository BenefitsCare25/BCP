/** Which products and coverage lines a member is shown.
 *
 * Statements are already filtered server-side (`member_statement.member_visible_code`);
 * enrolment options are not, because a fixed GTL still has to be priced and
 * submitted. These helpers are the one frontend copy of those rules. */
import type { CoverageLine } from "@/types";

function normalizedCode(code: string | null | undefined): string {
  return (code ?? "").trim().toUpperCase();
}

/** GTL is a death benefit: shown only where the member makes a choice about it. */
export function isHiddenUnlessChosen(code: string | null | undefined): boolean {
  return normalizedCode(code) === "GTL";
}

/** Dependant-only sheets (e.g. GHS-DEPENDANTS) describe family cover, not the employee's. */
export function isEmployeeLine(line: CoverageLine): boolean {
  return !normalizedCode(line.product_code).includes("DEPENDANTS");
}
