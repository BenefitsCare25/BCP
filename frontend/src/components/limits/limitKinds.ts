import { fmtMoney } from "@/lib/format";
import type { CoverageLimitAlert, LimitKind } from "@/types";

/** Display order: things that change who is covered first, data gaps last. */
export const LIMIT_ORDER: LimitKind[] = [
  "over_age",
  "over_entry_age",
  "dependant_over_age",
  "underwriting",
  "no_dob",
  "no_salary",
  "capped",
];

/** Group titles, and the summary sentence for N people. */
export const LIMIT_KIND: Record<
  LimitKind,
  { title: string; summary: (n: number) => string }
> = {
  over_age: {
    title: "Above the age limit",
    summary: (n) => `${n} ${n === 1 ? "employee is" : "employees are"} above a product's age limit`,
  },
  over_entry_age: {
    title: "Joined above the last entry age",
    summary: (n) => `${n} ${n === 1 ? "employee" : "employees"} joined above the last entry age`,
  },
  dependant_over_age: {
    title: "Dependant above the age limit",
    summary: (n) => `${n} ${n === 1 ? "dependant is" : "dependants are"} above a spouse or child age limit`,
  },
  underwriting: {
    title: "Needs underwriting",
    summary: (n) => `${n} ${n === 1 ? "member needs" : "members need"} underwriting for cover above the limit`,
  },
  no_dob: {
    title: "No date of birth",
    summary: (n) => `${n} ${n === 1 ? "employee has" : "employees have"} no date of birth to check age limits`,
  },
  no_salary: {
    title: "No salary",
    summary: (n) => `${n} ${n === 1 ? "employee has" : "employees have"} no salary for a salary-based cover`,
  },
  capped: {
    title: "Capped at the maximum sum insured",
    summary: (n) => `${n} ${n === 1 ? "employee's cover is" : "employees' cover is"} capped at the maximum`,
  },
};

/** A few words for a coverage-row badge; the full message is its tooltip. */
export function limitBadge(alert: CoverageLimitAlert): string {
  switch (alert.kind) {
    case "over_age":
      return `Age ${alert.age} · above limit ${alert.limit}`;
    case "over_entry_age":
      return `Joined at ${alert.age} · entry limit ${alert.limit}`;
    case "dependant_over_age":
      return `${alert.relationship === "child" ? "Child" : "Spouse"} ${alert.age} · above limit ${alert.limit}`;
    case "underwriting":
      return alert.amount != null ? `Underwriting ${fmtMoney(alert.amount)}` : "Needs underwriting";
    case "capped":
      return alert.limit != null ? `Capped at ${fmtMoney(alert.limit)}` : "Capped";
    case "no_salary":
      return "No salary";
    case "no_dob":
      return "No date of birth";
  }
}
