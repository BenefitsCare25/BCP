import { useMemo, useState } from "react";
import { useNavigate } from "@tanstack/react-router";
import { useQueryClient } from "@tanstack/react-query";
import {
  ArrowUpRight,
  Building2,
  CalendarDays,
  Loader2,
  ReceiptText,
  Search,
  Users,
  type LucideIcon,
} from "lucide-react";
import {
  type CompanySummary,
  useDashboardSummary,
} from "@/api/hooks";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from "@/components/ui/tooltip";
import { cn } from "@/lib/cn";
import { calendarDaysUntil, daysUntil } from "@/lib/attention";
import { useSession } from "@/stores/session";

type QueueKey =
  | "claims_review"
  | "messages"
  | "insurer"
  | "underwriting"
  | "enrollment"
  | "matching"
  | "dependants";

type CompanyDestination = QueueKey | "dashboard";

type WorkQueue = {
  key: QueueKey;
  label: string;
  description: string;
  count: (company: CompanySummary) => number;
  hasWork?: (company: CompanySummary) => boolean;
};

const WORK_QUEUES: WorkQueue[] = [
  {
    key: "claims_review",
    label: "Pending Claims",
    description:
      "Insurer and Flex claims awaiting human review, including claims the AI could not complete.",
    count: (company) => company.claims_to_review,
  },
  {
    key: "messages",
    label: "New Message",
    description:
      "Employee conversations where the employee sent the latest message and is waiting for a reply.",
    count: (company) => company.messages_awaiting_reply,
  },
  {
    key: "insurer",
    label: "Pending Insurer",
    description: "Claims sent to insurers and awaiting their response.",
    count: (company) => company.claims_with_insurer,
  },
  {
    key: "underwriting",
    label: "Pending UW",
    description:
      "Employees above the Non-Evidence Limit whose underwriting decision is pending or postponed.",
    count: (company) => company.underwriting_pending,
  },
  {
    key: "enrollment",
    label: "Enrollment",
    description: "Companies with an enrollment window currently open.",
    count: (company) => Number(company.enrollment_open),
  },
  {
    key: "matching",
    label: "Member matching",
    description:
      "Companies with unmatched employees or matching results made stale by category changes.",
    count: (company) => company.employees_unmatched,
    hasWork: (company) =>
      company.employees_unmatched > 0 || company.matching_stale,
  },
  {
    key: "dependants",
    label: "Dependant approvals",
    description: "Dependants awaiting review and approval.",
    count: (company) => company.dependants_pending,
  },
];

export function HomePage() {
  const { data, isLoading, isError, error, refetch, isFetching } =
    useDashboardSummary();
  const [queue, setQueue] = useState<QueueKey>("claims_review");
  const [companyQuery, setCompanyQuery] = useState("");

  if (isLoading) return <HomeSkeleton />;

  if (isError || !data) {
    return (
      <div className="flex min-h-52 flex-col items-center justify-center gap-3 rounded-xl border border-border bg-card p-6 text-center">
        <p className="text-sm text-error">
          Couldn&apos;t load Home. {error?.message}
        </p>
        <Button variant="outline" size="sm" onClick={() => void refetch()}>
          Retry
        </Button>
      </div>
    );
  }

  const selectedQueue =
    WORK_QUEUES.find((item) => item.key === queue) ?? WORK_QUEUES[0];
  const renewals = renewalCompanies(data.companies, 30);

  return (
    <div className="space-y-5 pb-4">
      <section
        aria-label="Portfolio summary"
        className="overflow-hidden rounded-xl border border-border bg-card"
      >
        <div className="grid grid-cols-2 divide-x divide-y divide-border sm:grid-cols-4 sm:divide-y-0">
          <SummaryMetric
            icon={Building2}
            label="Companies"
            value={data.firm.company_count}
          />
          <SummaryMetric
            icon={Users}
            label="Active members"
            value={data.firm.member_count}
          />
          <SummaryMetric
            icon={ReceiptText}
            label="Pending Claims Review"
            value={data.firm.claims_to_review}
            breakdown={[
              {
                label: "Insurer claims",
                value: data.firm.insured_claims_to_review,
              },
              {
                label: "Flex claims",
                value: data.firm.wallet_claims_to_review,
              },
            ]}
          />
          <SummaryMetric
            icon={CalendarDays}
            label="Upcoming Renewal · 30 days"
            value={renewals.length}
          />
        </div>
      </section>

      <WorkPanel
        companies={data.companies}
        selectedQueue={selectedQueue}
        queue={queue}
        onQueueChange={setQueue}
        isRefreshing={isFetching}
      />

      <CompanyDirectory
        companies={data.companies}
        query={companyQuery}
        onQueryChange={setCompanyQuery}
      />
    </div>
  );
}

function SummaryMetric({
  icon: Icon,
  label,
  value,
  breakdown,
}: {
  icon: LucideIcon;
  label: string;
  value: number;
  breakdown?: Array<{ label: string; value: number }>;
}) {
  return (
    <div className="min-w-0 p-4 sm:p-5">
      <div className="flex items-center gap-2 text-xs font-medium text-muted-foreground">
        <Icon className="size-3.5 shrink-0" strokeWidth={1.75} aria-hidden="true" />
        <span className="min-w-0 leading-4 sm:truncate">{label}</span>
      </div>
      <div className="mt-1.5 text-2xl font-semibold tracking-tight tabular-nums text-foreground sm:text-3xl">
        {value.toLocaleString()}
      </div>
      {breakdown && (
        <dl className="mt-2 flex flex-wrap gap-x-4 gap-y-1 text-xs leading-4">
          {breakdown.map((item) => (
            <div key={item.label} className="flex items-baseline gap-1.5">
              <dt className="text-muted-foreground">{item.label}</dt>
              <dd className="font-semibold tabular-nums text-foreground">
                {item.value.toLocaleString()}
              </dd>
            </div>
          ))}
        </dl>
      )}
    </div>
  );
}

function WorkPanel({
  companies,
  selectedQueue,
  queue,
  onQueueChange,
  isRefreshing,
}: {
  companies: CompanySummary[];
  selectedQueue: WorkQueue;
  queue: QueueKey;
  onQueueChange: (queue: QueueKey) => void;
  isRefreshing: boolean;
}) {
  const rows = useMemo(
    () =>
      companies
        .filter(
          (company) =>
            selectedQueue.hasWork?.(company) ?? selectedQueue.count(company) > 0,
        )
        .sort(
          (a, b) =>
            queuePriority(b, queue) - queuePriority(a, queue) ||
            a.name.localeCompare(b.name),
        ),
    [companies, queue, selectedQueue],
  );
  const overdue = companies.reduce(
    (total, company) => total + company.claims_overdue,
    0,
  );
  const isClaimsReview = queue === "claims_review";
  const isEnrollment = queue === "enrollment";
  const isMessages = queue === "messages";

  return (
    <section
      className="overflow-hidden rounded-xl border border-border bg-card"
      aria-label="Work queues"
    >
      <div className="border-b border-border px-3 sm:px-5">
        <TooltipProvider delayDuration={250}>
          <div
            className="flex flex-wrap items-center gap-x-1"
            role="tablist"
            aria-label="Work queues"
          >
            {WORK_QUEUES.map((item) => {
              const active = item.key === queue;
              return (
                <Tooltip key={item.key}>
                  <TooltipTrigger asChild>
                    <button
                      type="button"
                      id={`work-queue-tab-${item.key}`}
                      role="tab"
                      aria-selected={active}
                      aria-controls="work-queue-panel"
                      onClick={() => onQueueChange(item.key)}
                      className={cn(
                        "relative flex min-h-10 items-center px-2 text-xs font-medium text-muted-foreground transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-ring sm:text-sm",
                        active && "text-foreground",
                      )}
                    >
                      {item.label}
                      {active && (
                        <span
                          className="absolute inset-x-2.5 bottom-0 h-0.5 bg-primary"
                          aria-hidden="true"
                        />
                      )}
                    </button>
                  </TooltipTrigger>
                  <TooltipContent side="bottom" className="max-w-72">
                    {item.description}
                  </TooltipContent>
                </Tooltip>
              );
            })}
          </div>
        </TooltipProvider>
      </div>
      <div
        id="work-queue-panel"
        role="tabpanel"
        aria-labelledby={`work-queue-tab-${queue}`}
        className="p-3 sm:p-5"
      >
        {(isRefreshing || (queue === "insurer" && overdue > 0)) && (
          <div className="mb-3 flex justify-end">
            <span className="flex items-center gap-2 text-xs tabular-nums">
              {queue === "insurer" && overdue > 0 && (
                <span className="font-medium text-error">{overdue} overdue</span>
              )}
              {isRefreshing && (
                <Loader2
                  className="size-3.5 animate-spin text-muted-foreground"
                  aria-label="Refreshing"
                />
              )}
            </span>
          </div>
        )}

        {rows.length === 0 ? (
          <div className="rounded-lg border border-dashed border-border px-4 py-9 text-center text-sm text-muted-foreground">
            No open items.
          </div>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full min-w-[19rem] text-sm sm:min-w-[35rem]">
              <thead>
                <tr className="border-y border-border bg-muted/35 text-left text-2xs font-medium uppercase tracking-wider text-muted-foreground">
                  <th className="px-3 py-2.5">Company</th>
                  <th className="hidden px-3 py-2.5 sm:table-cell">Policy period</th>
                  {isClaimsReview ? (
                    <>
                      <th className="px-3 py-2.5 text-right">Insurer claims</th>
                      <th className="px-3 py-2.5 text-right">Flex claims</th>
                    </>
                  ) : isEnrollment ? (
                    <th className="px-3 py-2.5">Closes</th>
                  ) : (
                    <>
                      <th className="px-3 py-2.5 text-right">
                        {isMessages ? "Awaiting reply" : "Outstanding"}
                      </th>
                      <th className="px-3 py-2.5">Status</th>
                    </>
                  )}
                  <th className="px-3 py-2.5">
                    <span className="sr-only">Open queue</span>
                  </th>
                </tr>
              </thead>
              <tbody>
                {rows.map((company) => (
                  <WorkRow
                    key={company.id}
                    company={company}
                    queue={queue}
                    count={selectedQueue.count(company)}
                  />
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </section>
  );
}

function WorkRow({
  company,
  queue,
  count,
}: {
  company: CompanySummary;
  queue: QueueKey;
  count: number;
}) {
  const enter = useCompanyNavigation();
  const priority = queuePriorityLabel(company, queue);
  const isClaimsReview = queue === "claims_review";
  const isEnrollment = queue === "enrollment";

  return (
    <tr className="border-b border-border last:border-0 hover:bg-muted/35">
      <td className="px-3 py-3 font-medium text-foreground">{company.name}</td>
      <td className="hidden whitespace-nowrap px-3 py-3 tabular-nums text-muted-foreground sm:table-cell">
        {policyPeriodLabel(company.current_year)}
      </td>
      {isClaimsReview ? (
        <>
          <td className="px-3 py-3 text-right tabular-nums text-foreground">
            {company.insured_claims_to_review.toLocaleString()}
          </td>
          <td className="px-3 py-3 text-right tabular-nums text-foreground">
            {company.wallet_claims_to_review.toLocaleString()}
          </td>
        </>
      ) : isEnrollment ? (
        <td className="px-3 py-3 text-xs text-muted-foreground">
          {company.enrollment_closes_at
            ? enrollmentClosingLabel(company.enrollment_closes_at)
            : "Open"}
        </td>
      ) : (
        <>
          <td className="px-3 py-3 text-right tabular-nums text-foreground">
            {count.toLocaleString()}
          </td>
          <td
            className={cn(
              "px-3 py-3 text-xs",
              priority.overdue ? "font-medium text-error" : "text-muted-foreground",
            )}
          >
            {priority.label}
          </td>
        </>
      )}
      <td className="px-3 py-3 text-right">
        <button
          type="button"
          onClick={() => enter(company, queue)}
          className="inline-flex min-h-8 items-center gap-1 rounded-md px-1 text-xs font-medium text-primary hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
          aria-label={`Open ${queueLabel(queue)} for ${company.name}`}
        >
          Open <ArrowUpRight className="size-3.5" aria-hidden="true" />
        </button>
      </td>
    </tr>
  );
}

function CompanyDirectory({
  companies,
  query,
  onQueryChange,
}: {
  companies: CompanySummary[];
  query: string;
  onQueryChange: (value: string) => void;
}) {
  const enter = useCompanyNavigation();
  const filtered = useMemo(
    () =>
      companies.filter((company) =>
        company.name.toLowerCase().includes(query.trim().toLowerCase()),
      ),
    [companies, query],
  );
  return (
    <section
      className="rounded-xl border border-border bg-card p-4 sm:p-5"
      aria-labelledby="companies-heading"
    >
      <div className="flex flex-wrap items-center justify-between gap-3">
        <h2 id="companies-heading" className="text-base font-semibold text-foreground">
          Companies
        </h2>
        <div className="relative w-full sm:w-72">
          <Search
            className="pointer-events-none absolute left-2.5 top-1/2 size-4 -translate-y-1/2 text-muted-foreground"
            aria-hidden="true"
          />
          <Input
            type="search"
            aria-label="Search companies"
            value={query}
            onChange={(event) => onQueryChange(event.target.value)}
            placeholder="Search companies"
            className="pl-8"
          />
        </div>
      </div>
      <div className="mt-3 overflow-x-auto">
        <table className="w-full min-w-[19rem] text-sm sm:min-w-[35rem]">
          <thead>
            <tr className="border-y border-border bg-muted/35 text-left text-2xs font-medium uppercase tracking-wider text-muted-foreground">
              <th className="px-3 py-2.5">Company</th>
              <th className="hidden px-3 py-2.5 sm:table-cell">Policy period</th>
              <th className="hidden px-3 py-2.5 text-right sm:table-cell">Employees</th>
              <th className="px-3 py-2.5 text-right">Dependents</th>
              <th className="px-3 py-2.5">
                <span className="sr-only">Open company</span>
              </th>
            </tr>
          </thead>
          <tbody>
            {filtered.map((company) => (
              <tr key={company.id} className="border-b border-border last:border-0 hover:bg-muted/35">
                <td className="px-3 py-3 font-medium text-foreground">{company.name}</td>
                <td className="hidden whitespace-nowrap px-3 py-3 tabular-nums text-muted-foreground sm:table-cell">
                  {policyPeriodLabel(company.current_year)}
                </td>
                <td className="hidden px-3 py-3 text-right tabular-nums text-foreground sm:table-cell">
                  {company.member_count.toLocaleString()}
                </td>
                <td className="px-3 py-3 text-right tabular-nums text-foreground">
                  {company.dependant_count.toLocaleString()}
                </td>
                <td className="px-3 py-3 text-right">
                  <button
                    type="button"
                    onClick={() => enter(company, "dashboard")}
                    className="inline-flex min-h-8 items-center gap-1 text-xs font-medium text-primary hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
                    aria-label={`Open ${company.name}`}
                  >
                    Open <ArrowUpRight className="size-3.5" aria-hidden="true" />
                  </button>
                </td>
              </tr>
            ))}
            {filtered.length === 0 && (
              <tr>
                <td colSpan={5} className="px-3 py-8 text-center text-sm text-muted-foreground">
                  No matching companies.
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>
    </section>
  );
}

function HomeSkeleton() {
  return (
    <div className="space-y-5" aria-label="Loading Home">
      <div className="grid grid-cols-2 overflow-hidden rounded-xl border border-border bg-card sm:grid-cols-4">
        {Array.from({ length: 4 }).map((_, index) => (
          <div
            key={index}
            className="h-28 animate-pulse border-border bg-muted/45 sm:border-r last:border-0"
          />
        ))}
      </div>
      <div className="h-96 animate-pulse rounded-xl border border-border bg-muted/45" />
    </div>
  );
}

function useCompanyNavigation() {
  const navigate = useNavigate();
  const setActiveClient = useSession((state) => state.setActiveClient);
  const setPolicyYear = useSession((state) => state.setPolicyYear);
  const queryClient = useQueryClient();
  return (company: CompanySummary, destination: CompanyDestination) => {
    setActiveClient(company.id);
    setPolicyYear(company.current_year?.id ?? null);
    queryClient.removeQueries();
    if (destination === "dashboard") {
      navigate({ to: "/dashboard" });
    } else if (destination === "enrollment") {
      navigate({ to: "/client-relations/enrollment" });
    } else if (destination === "underwriting") {
      navigate({ to: "/policy-admin/underwriting" });
    } else if (destination === "matching") {
      navigate({ to: "/policy-admin/member-listing" });
    } else if (destination === "dependants") {
      navigate({ to: "/policy-admin/member-listing", search: { tab: "dependants" } });
    } else if (destination === "messages") {
      navigate({ to: "/claims/review", search: { tab: "messages" } });
    } else {
      navigate({ to: "/claims/review", search: { tab: "queue" } });
    }
  };
}

function renewalCompanies(companies: CompanySummary[], horizon: number) {
  return companies
    .filter((company) => company.current_year?.end_date)
    .filter((company) => {
      const days = calendarDaysUntil(company.current_year!.end_date);
      return days >= 0 && days <= horizon;
    })
    .sort((a, b) => a.current_year!.end_date.localeCompare(b.current_year!.end_date));
}

function queuePriority(company: CompanySummary, queue: QueueKey) {
  if (queue === "insurer") {
    return company.claims_overdue * 10_000 + company.claims_with_insurer;
  }
  return WORK_QUEUES.find((item) => item.key === queue)?.count(company) ?? 0;
}

function queuePriorityLabel(company: CompanySummary, queue: QueueKey) {
  if (queue === "insurer" && company.claims_overdue > 0) {
    return { label: `${company.claims_overdue} overdue`, overdue: true };
  }
  if (queue === "matching" && company.matching_stale) {
    return { label: "Matching stale", overdue: false };
  }
  if (queue === "messages") {
    return { label: "Needs reply", overdue: false };
  }
  return { label: "Open", overdue: false };
}

function queueLabel(queue: QueueKey) {
  return WORK_QUEUES.find((item) => item.key === queue)?.label ?? "work";
}

function enrollmentClosingLabel(closesAt: string) {
  const days = daysUntil(closesAt);
  if (days < 0) return "Past closing date";
  if (days === 0) return "Closes today";
  if (days === 1) return "Closes tomorrow";
  return `Closes in ${days} days`;
}

function policyPeriodLabel(policyYear: CompanySummary["current_year"]) {
  if (!policyYear) return "—";
  return `${formatPolicyDate(policyYear.start_date)} – ${formatPolicyDate(policyYear.end_date)}`;
}

function formatPolicyDate(value: string) {
  const [year, month, day] = value.split("-").map(Number);
  if (!year || !month || !day) return value;
  return new Intl.DateTimeFormat("en-SG", {
    day: "2-digit",
    month: "short",
    year: "numeric",
    timeZone: "UTC",
  }).format(new Date(Date.UTC(year, month - 1, day)));
}
