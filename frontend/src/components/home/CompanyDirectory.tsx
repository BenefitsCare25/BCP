import { useMemo } from "react";
import { ArrowUpRight, Search } from "lucide-react";
import type { CompanySummary } from "@/api/dashboard";
import { Input } from "@/components/ui/input";
import { policyPeriodLabel, periodState, useCompanyNavigation } from "@/lib/homeDashboard";
import { TablePagination, useTablePage } from "./TablePagination";

export function CompanyDirectory({ companies, query, onQueryChange, businessDate, ending = false }: {
  companies: CompanySummary[]; query: string; onQueryChange: (value: string) => void;
  businessDate?: string; ending?: boolean;
}) {
  const filtered = useMemo(() => companies.filter(company =>
    company.name.toLocaleLowerCase().includes(query.trim().toLocaleLowerCase())), [companies, query]);
  const headingId = ending ? "ending-periods-heading" : "companies-heading";
  return <section id={ending ? "ending-periods" : undefined} className="rounded-xl border border-border bg-card p-4 sm:p-5" aria-labelledby={headingId}>
    <div className="flex flex-wrap items-center justify-between gap-3">
      <h2 id={headingId} className="text-base font-semibold text-foreground">{ending ? "Policy periods ending within 30 days" : "Companies"}</h2>
      <div className="relative w-full sm:w-72">
        <Search className="pointer-events-none absolute left-2.5 top-1/2 size-4 -translate-y-1/2 text-muted-foreground" aria-hidden="true" />
        <Input type="search" aria-label={ending ? "Search ending policy periods" : "Search companies"}
          value={query} onChange={event => onQueryChange(event.target.value)} placeholder="Search companies" className="pl-8" />
      </div>
    </div>
    <p className="mt-2 text-xs text-muted-foreground">{ending
      ? "Period end dates. A configured next period is shown below; confirm its readiness in Company & Benefits."
      : "Employee and dependant counts belong to each displayed period. Upcoming and expired periods are labelled."}</p>
    <DirectoryTable key={query} companies={filtered} businessDate={businessDate} ending={ending}
      emptyText={query.trim() ? "No matching companies." : ending ? "No policy periods end within 30 days." : "No companies are available. Ask your administrator to grant company access or complete company setup."} />
  </section>;
}

function DirectoryTable({ companies, businessDate, ending, emptyText }: {
  companies: CompanySummary[]; businessDate?: string; ending: boolean; emptyText: string;
}) {
  const pagination = useTablePage(companies);
  return <>
    <div className="mt-3 overflow-x-auto">
      <table className="w-full min-w-[19rem] text-sm sm:min-w-[35rem]">
        <thead><tr className="border-y border-border bg-muted/35 text-left text-2xs font-medium uppercase tracking-wider text-muted-foreground">
          <th scope="col" className="px-3 py-2.5">Company</th>
          <th scope="col" className="hidden px-3 py-2.5 sm:table-cell">Policy period</th>
          <th scope="col" className="px-3 py-2.5 text-right">Employees</th>
          <th scope="col" className="px-3 py-2.5 text-right">Dependants</th>
          <th scope="col" className="px-3 py-2.5"><span className="sr-only">Open company</span></th>
        </tr></thead>
        <tbody>{pagination.rows.map(company => <DirectoryRow key={company.id} company={company} businessDate={businessDate} ending={ending} />)}
          {companies.length === 0 && <tr><td colSpan={5} className="px-3 py-8 text-center text-sm text-muted-foreground" role="status">{emptyText}</td></tr>}
        </tbody>
      </table>
    </div>
    <TablePagination {...pagination} />
  </>;
}

function DirectoryRow({ company, businessDate, ending }: { company: CompanySummary; businessDate?: string; ending: boolean }) {
  const enter = useCompanyNavigation();
  const state = periodState(company, businessDate);
  const needsSetup = state === "No benefit year" || state === "Expired";
  const context = ending && company.next_year ? `Next period configured: ${policyPeriodLabel(company.next_year)}`
    : ending ? "No next period configured" : state;
  return <tr className="border-b border-border last:border-0 hover:bg-muted/35">
    <td className="px-3 py-3 font-medium text-foreground">
      {company.name}<span className="mt-1 block text-xs font-normal text-muted-foreground sm:hidden">{policyPeriodLabel(company.current_year)}</span>
      <span className="mt-1 block text-xs font-normal text-muted-foreground">{context}</span>
    </td>
    <td className="hidden whitespace-nowrap px-3 py-3 tabular-nums text-muted-foreground sm:table-cell">{policyPeriodLabel(company.current_year)}</td>
    <td className="px-3 py-3 text-right tabular-nums text-foreground">{company.member_count.toLocaleString()}</td>
    <td className="px-3 py-3 text-right tabular-nums text-foreground">{company.dependant_count.toLocaleString()}</td>
    <td className="px-3 py-3 text-right">
      <button type="button" onClick={() => enter(company, needsSetup ? "setup" : ending ? "setup" : "dashboard")}
        className="inline-flex min-h-8 items-center gap-1 rounded-md text-xs font-medium text-primary hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
        aria-label={`${needsSetup || ending ? "Review setup for" : "Open"} ${company.name}`}>
        {needsSetup || ending ? "Review setup" : "Open"}<ArrowUpRight className="size-3.5 shrink-0" aria-hidden="true" />
      </button>
    </td>
  </tr>;
}
