import { useMemo } from "react";
import { ArrowUpRight, Loader2 } from "lucide-react";
import type { CompanySummary } from "@/api/dashboard";
import { cn } from "@/lib/cn";
import { Tabs, TabsList, TabsTrigger, TabsContent } from "@/components/ui/tabs";
import {
  WORK_QUEUES, type QueueKey, type WorkQueue, queuePriority, queueLabel,
  queuePriorityLabel, policyPeriodLabel, enrollmentStatus, useCompanyNavigation,
} from "@/lib/homeDashboard";
import { TablePagination, useTablePage } from "./TablePagination";

export function WorkPanel({ companies, queue, onQueueChange, isRefreshing }: {
  companies: CompanySummary[]; queue: QueueKey;
  onQueueChange: (queue: QueueKey) => void; isRefreshing: boolean;
}) {
  const selectedQueue = WORK_QUEUES.find(item => item.key === queue) ?? WORK_QUEUES[0];
  const rows = useMemo(() => companies
    .filter(company => selectedQueue.hasWork?.(company) ?? selectedQueue.count(company) > 0)
    .sort((a, b) => queuePriority(b, queue) - queuePriority(a, queue) || a.name.localeCompare(b.name)),
  [companies, queue, selectedQueue]);
  const overdue = companies.reduce((sum, company) => sum + company.claims_overdue, 0);
  return <section className="rounded-xl border border-border bg-card" aria-label="Work queues">
    <Tabs value={queue} onValueChange={value => onQueueChange(value as QueueKey)}>
        <TabsList aria-label="Work queues" className="flex flex-wrap gap-1 rounded-t-xl border-b border-border px-3 py-2 sm:px-5">
          {WORK_QUEUES.map(item => <QueueTab key={item.key} item={item} companies={companies} />)}
        </TabsList>
      {WORK_QUEUES.map(item => <TabsContent key={item.key} value={item.key} className="m-0 p-3 sm:p-5">
        {item.key === queue && <>
          <div className="mb-3 flex flex-wrap items-center justify-between gap-2 text-xs text-muted-foreground">
            <p>{selectedQueue.description}</p>
            {isRefreshing && <Loader2 className="size-3.5 animate-spin" aria-label="Refreshing" />}
          </div>
          {queue === "insurer" && overdue > 0 && <p className="mb-3 text-xs font-medium text-error">{overdue} overdue</p>}
          <QueueTable key={queue} rows={rows} queue={queue} selectedQueue={selectedQueue} />
        </>}
      </TabsContent>)}
    </Tabs>
  </section>;
}

function QueueTab({ item, companies }: { item: WorkQueue; companies: CompanySummary[] }) {
  const matching = companies.filter(company => item.hasWork?.(company) ?? item.count(company) > 0);
  // Matching includes stale snapshots with zero unmatched people; enrollment names its year unit.
  const count = item.key === "matching" || item.key === "enrollment"
    ? matching.length : matching.reduce((sum, company) => sum + item.count(company), 0);
  return <TabsTrigger value={item.key} aria-label={item.label} title={item.description}
        aria-description={`${count.toLocaleString()} ${item.key === "matching" || item.key === "enrollment" ? "benefit years" : "items"}. ${item.description}`}
        className="min-h-10 gap-2 rounded-md border-0 px-2 text-xs data-[state=active]:bg-accent data-[state=active]:text-accent-foreground sm:text-sm">
        {item.label}
        <span className="rounded-full border border-border px-1.5 py-0.5 text-2xs tabular-nums" aria-hidden="true">{count.toLocaleString()}</span>
        <span className="sr-only">{count.toLocaleString()} {item.key === "matching" || item.key === "enrollment" ? "benefit years" : "items"}</span>
      </TabsTrigger>;
}

function QueueTable({ rows, queue, selectedQueue }: { rows: CompanySummary[]; queue: QueueKey; selectedQueue: WorkQueue }) {
  const pagination = useTablePage(rows);
  if (rows.length === 0) return <div className="rounded-lg border border-dashed border-border px-4 py-9 text-center text-sm text-muted-foreground" role="status">No open items.</div>;
  return <>
    <div className="overflow-x-auto">
      <table className="w-full min-w-[19rem] text-sm sm:min-w-[35rem]">
        <thead><tr className="border-y border-border bg-muted/35 text-left text-2xs font-medium uppercase tracking-wider text-muted-foreground">
          <th scope="col" className="px-3 py-2.5">Company</th>
          <th scope="col" className="hidden px-3 py-2.5 sm:table-cell">Policy period</th>
          {queue === "claims_review" ? <>
            <th scope="col" className="px-3 py-2.5 text-right">Insurer claims</th>
            <th scope="col" className="px-3 py-2.5 text-right">Flex claims</th>
          </> : <>
            <th scope="col" className="px-3 py-2.5 text-right">{queue === "messages" ? "Awaiting reply" : queue === "enrollment" ? "Windows" : queue === "underwriting" ? "Reviews" : "Outstanding"}</th>
            <th scope="col" className="px-3 py-2.5">Status</th>
          </>}
          <th scope="col" className="px-3 py-2.5"><span className="sr-only">Open queue</span></th>
        </tr></thead>
        <tbody>{pagination.rows.map(company => <WorkRow key={`${company.id}:${company.current_year?.id}`} company={company} queue={queue} count={selectedQueue.count(company)} />)}</tbody>
      </table>
    </div>
    <TablePagination {...pagination} />
  </>;
}

function WorkRow({ company, queue, count }: { company: CompanySummary; queue: QueueKey; count: number }) {
  const enter = useCompanyNavigation();
  const priority = queuePriorityLabel(company, queue);
  return <tr className="border-b border-border last:border-0 hover:bg-muted/35">
    <td className="px-3 py-3 font-medium text-foreground">
      {company.name}<span className="mt-1 block text-xs font-normal text-muted-foreground sm:hidden">{policyPeriodLabel(company.current_year)}</span>
    </td>
    <td className="hidden whitespace-nowrap px-3 py-3 tabular-nums text-muted-foreground sm:table-cell">{policyPeriodLabel(company.current_year)}</td>
    {queue === "claims_review" ? <>
      {(["insured", "flex"] as const).map(kind => <td key={kind} className="px-3 py-3 text-right tabular-nums">
        <button type="button" onClick={() => enter(company, queue, kind)}
          className="min-h-8 min-w-8 rounded text-primary hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
          aria-label={`Open ${kind === "insured" ? "insurer" : "Flex"} claims awaiting review for ${company.name}`}>
          {(kind === "insured" ? company.insured_claims_to_review : company.wallet_claims_to_review).toLocaleString()}
        </button>
      </td>)}
    </> : <>
      <td className="px-3 py-3 text-right tabular-nums">{count.toLocaleString()}</td>
      <td className={cn("px-3 py-3 text-xs", priority.overdue || (queue === "enrollment" && company.enrollment_overdue) ? "font-medium text-error" : "text-muted-foreground")}>
        {queue === "enrollment" ? enrollmentStatus(company) : queue === "insurer" && company.claims_overdue > 0 ?
          <button type="button" onClick={() => enter(company, "overdue")}
            className="min-h-8 rounded hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
            aria-label={`Open overdue insurer claims for ${company.name}`}>{priority.label}</button> : priority.label}
      </td>
    </>}
    <td className="px-3 py-3 text-right">
      <button type="button" onClick={() => enter(company, queue)}
        className="inline-flex min-h-8 items-center gap-1 rounded-md px-1 text-xs font-medium text-primary hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
        aria-label={`Open ${queueLabel(queue)} for ${company.name}`}
        aria-description={policyPeriodLabel(company.current_year)}>
        Open <ArrowUpRight className="size-3.5" aria-hidden="true" />
      </button>
    </td>
  </tr>;
}
