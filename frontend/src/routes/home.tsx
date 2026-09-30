import { useState } from "react";
import { Building2, CalendarDays, Loader2, ReceiptText, RefreshCw, Users } from "lucide-react";
import { useDashboardSummary, type DashboardSummary } from "@/api/dashboard";
import { Button } from "@/components/ui/button";
import { CompanyDirectory } from "@/components/home/CompanyDirectory";
import { SummaryMetric } from "@/components/home/SummaryMetric";
import { WorkPanel } from "@/components/home/WorkPanel";
import { renewalCompanies, type QueueKey } from "@/lib/homeDashboard";

export function HomePage() {
  const { data, isLoading, isError, refetch, isFetching, dataUpdatedAt } = useDashboardSummary(undefined, { localErrorHandling: true });
  if (isLoading) return <HomeSkeleton />;
  if (!data) return <div className="flex min-h-52 flex-col items-center justify-center gap-3 rounded-xl border border-border bg-card p-6 text-center" role="alert">
    <p className="text-sm text-error">Couldn't load Home. Please retry.</p>
    <Button variant="outline" size="sm" disabled={isFetching} onClick={() => void refetch()}>Retry</Button>
  </div>;
  return <HomeContent data={data} stale={isError} refreshing={isFetching}
    updatedAt={dataUpdatedAt} refresh={() => void refetch()} />;
}

function HomeContent({ data, stale, refreshing, updatedAt, refresh }: {
  data: DashboardSummary; stale: boolean; refreshing: boolean; updatedAt: number; refresh: () => void;
}) {
  const [queue, setQueue] = useState<QueueKey>("claims_review");
  const [companyQuery, setCompanyQuery] = useState("");
  const [endingQuery, setEndingQuery] = useState("");
  const [showEnding, setShowEnding] = useState(false);
  const renewals = renewalCompanies(data.companies, 30, data.business_date);
  const work = data.work_by_year ?? data.companies;
  return <div className="space-y-5 pb-4">
    <div className="flex flex-wrap items-start justify-between gap-3">
      <div>
        <h1 className="text-xl font-semibold tracking-tight text-foreground">Home</h1>
        <p className="mt-1 text-xs text-muted-foreground">Employees in periods covering today. Outstanding work across all benefit years.</p>
      </div>
      <div className="flex flex-wrap items-center gap-3">
        <p className="text-xs text-muted-foreground">Updated <time dateTime={new Date(updatedAt).toISOString()}>{new Intl.DateTimeFormat("en-SG", { hour: "2-digit", minute: "2-digit", second: "2-digit", timeZone: "Asia/Singapore" }).format(updatedAt)}</time> SGT</p>
        <Button variant="outline" size="sm" disabled={refreshing} onClick={refresh} aria-label="Refresh Home">
          {refreshing ? <Loader2 className="size-3.5 animate-spin" /> : <RefreshCw className="size-3.5" />} Refresh
        </Button>
      </div>
    </div>
    {stale && <div className="flex flex-wrap items-center justify-between gap-3 rounded-lg border border-border bg-warn-soft p-3 text-sm text-warn" role="alert">
      <p>Couldn't refresh Home. Showing the last successful update.</p>
      <Button variant="outline" size="sm" disabled={refreshing} onClick={refresh}>Retry</Button>
    </div>}
    <section aria-label="Portfolio summary" className="overflow-hidden rounded-xl border border-border bg-card">
      <div className="grid grid-cols-2 divide-x divide-y divide-border sm:grid-cols-4 sm:divide-y-0">
        <SummaryMetric icon={Building2} label="Companies" value={data.firm.company_count} />
        <SummaryMetric icon={Users} label="Active employees" value={data.firm.member_count}
          breakdown={[{ label: "Active dependants", value: data.firm.dependant_count }]} />
        <SummaryMetric icon={ReceiptText} label="Pending Claims Review" value={data.firm.claims_to_review}
          breakdown={[{ label: "Insurer claims", value: data.firm.insured_claims_to_review }, { label: "Flex claims", value: data.firm.wallet_claims_to_review }, { label: "AI processing", value: data.firm.verification_pending }]} />
        <SummaryMetric icon={CalendarDays} label="Policy periods ending · 30 days" value={renewals.length}
          onClick={() => setShowEnding(value => !value)} expanded={showEnding} controls="ending-periods" />
      </div>
    </section>
    <div hidden={!showEnding}><CompanyDirectory ending companies={renewals} query={endingQuery} onQueryChange={setEndingQuery} businessDate={data.business_date} /></div>
    <WorkPanel companies={work} queue={queue} onQueueChange={setQueue} isRefreshing={refreshing} />
    <CompanyDirectory companies={data.companies} query={companyQuery} onQueryChange={setCompanyQuery} businessDate={data.business_date} />
  </div>;
}

function HomeSkeleton() {
  return <div className="space-y-5" aria-label="Loading Home" role="status">
    <span className="sr-only">Loading Home</span>
    <div className="h-12 animate-pulse rounded-lg bg-muted/45" />
    <div className="grid grid-cols-2 overflow-hidden rounded-xl border border-border bg-card sm:grid-cols-4">
      {Array.from({ length: 4 }).map((_, index) => <div key={index} className="h-28 animate-pulse bg-muted/45" />)}
    </div>
    <div className="h-96 animate-pulse rounded-xl border border-border bg-muted/45" />
  </div>;
}
