import type { MemberCounts } from "@/types";

export function UnmatchedEmployeeNotice({
  productCode,
  counts,
}: {
  productCode: string;
  counts: MemberCounts | undefined;
}) {
  if (!counts) return null;
  const unmatched = Math.max(0, counts.employees_in_scope - counts.employees_matched);
  if (unmatched === 0) return null;

  const gradeSummary = Object.entries(counts.unmatched_grades)
    .map(([grade, count]) => `${grade} (${count})`)
    .join(", ");

  return (
    <div className="rounded-md border border-warn/40 bg-warn-soft/40 px-3 py-2 text-xs text-foreground">
      <p>
        {unmatched} active employee{unmatched === 1 ? "" : "s"} within this product’s insured entities {unmatched === 1 ? "has" : "have"} no {productCode} category. Review the placement slip and roster grades before confirming coverage.
        {gradeSummary && ` Unmatched grades: ${gradeSummary}.`}
      </p>
      {counts.unmatched_employees.length > 0 && (
        <details className="mt-2">
          <summary className="w-fit cursor-pointer font-medium underline underline-offset-2">
            Show affected employees
          </summary>
          <ul className="mt-2 grid gap-1 sm:grid-cols-2">
            {counts.unmatched_employees.map((employee) => (
              <li key={employee.employee_id} className="break-words">
                <span className="font-medium">{employee.employee_name || employee.staff_id}</span>
                {employee.employee_name && ` · ${employee.staff_id}`}
                {employee.grade && ` · ${employee.grade}`}
              </li>
            ))}
          </ul>
          {unmatched > counts.unmatched_employees.length && (
            <p className="mt-2">Showing the first {counts.unmatched_employees.length} employees.</p>
          )}
        </details>
      )}
    </div>
  );
}
