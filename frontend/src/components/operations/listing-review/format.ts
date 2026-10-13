/** Shared wording for the Employee Listing review. */

export function count(n: number, singular: string, plural = `${singular}s`): string {
  return `${n.toLocaleString()} ${n === 1 ? singular : plural}`;
}

/** Employees and dependants in a list of roster movements. */
export function byRecordType(ops: { record_type: string }[]): {
  employees: number;
  dependants: number;
} {
  let employees = 0;
  let dependants = 0;
  for (const op of ops) {
    if (op.record_type === "dependant") dependants += 1;
    else employees += 1;
  }
  return { employees, dependants };
}

/** "179 employees, 81 dependants", or null when there are none. */
export function peopleText(people: { employees: number; dependants: number }): string | null {
  const parts = [
    people.employees ? count(people.employees, "employee") : null,
    people.dependants ? count(people.dependants, "dependant") : null,
  ].filter(Boolean);
  return parts.length ? parts.join(", ") : null;
}
