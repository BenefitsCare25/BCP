import { useMemo, useState } from "react";
import { useNavigate } from "@tanstack/react-router";
import { useQueryClient } from "@tanstack/react-query";
import {
  ArrowRight,
  Building2,
  CalendarClock,
  Loader2,
  ReceiptText,
  Search,
  ShieldQuestion,
  UserPlus,
  Users,
} from "lucide-react";
import { type CompanySummary, useDashboardSummary } from "@/api/hooks";
import { Badge } from "@/components/ui/badge";
import { Input } from "@/components/ui/input";
import { Kpi } from "@/components/ui/kpi";
import { cn } from "@/lib/cn";
import { companyAttention, daysUntil } from "@/lib/attention";
import { useSession } from "@/stores/session";

export function HomePage() {
  const { data, isLoading, isError } = useDashboardSummary();
  const [q, setQ] = useState("");

  const companies = data?.companies ?? [];
  const filtered = useMemo(
    () =>
      companies.filter((c) =>
        c.name.toLowerCase().includes(q.trim().toLowerCase()),
      ),
    [companies, q],
  );

  const attention = useMemo(() => buildAttention(companies), [companies]);

  if (isLoading) {
    return (
      <div className="flex items-center gap-2 p-8 text-sm text-muted-foreground">
        <Loader2 className="size-4 animate-spin" /> Loading your companies…
      </div>
    );
  }
  if (isError || !data) {
    return (
      <div className="p-8 text-sm text-muted-foreground">
        Couldn’t load the dashboard just now.
      </div>
    );
  }

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-xl font-semibold text-foreground">Your companies</h1>
        <p className="text-sm text-muted-foreground">
          {data.firm.company_count} companies · pick one to manage its benefits,
          members and claims.
        </p>
      </div>

      <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-6">
        <Kpi label="Companies" value={data.firm.company_count} icon={Building2} />
        <Kpi label="Members" value={data.firm.member_count} icon={Users} />
        <Kpi
          label="Claims to review"
          value={data.firm.claims_to_review}
          icon={ReceiptText}
          tone={data.firm.claims_to_review > 0 ? "warn" : "default"}
        />
        <Kpi
          label="Dependant approvals"
          value={data.firm.dependants_pending}
          icon={UserPlus}
          tone={data.firm.dependants_pending > 0 ? "warn" : "default"}
        />
        <Kpi
          label="U/W pending"
          value={data.firm.underwriting_pending}
          icon={ShieldQuestion}
          tone={data.firm.underwriting_pending > 0 ? "warn" : "default"}
        />
        <Kpi
          label="Windows open"
          value={data.firm.windows_open}
          icon={CalendarClock}
        />
      </div>

      {attention.length > 0 && (
        <div className="rounded-lg border border-border bg-card p-4">
          <h2 className="mb-2 text-sm font-semibold text-foreground">
            Needs attention
          </h2>
          <ul className="space-y-1.5">
            {attention.map((a) => (
              <li
                key={a.key}
                className="flex items-center gap-2 text-sm text-foreground/80"
              >
                <span
                  className={cn(
                    "inline-block size-1.5 rounded-full",
                    a.tone === "warn" ? "bg-warn" : "bg-error",
                  )}
                />
                <span className="font-medium text-foreground">{a.company}</span>
                <span className="text-muted-foreground">· {a.message}</span>
              </li>
            ))}
          </ul>
        </div>
      )}

      <div className="relative w-full sm:w-72">
        <Search className="pointer-events-none absolute left-2.5 top-1/2 size-4 -translate-y-1/2 text-muted-foreground" />
        <Input
          value={q}
          onChange={(e) => setQ(e.target.value)}
          placeholder="Search companies…"
          className="pl-8"
        />
      </div>

      <CompanyTable companies={filtered} query={q} />
    </div>
  );
}

type FirmAttention = {
  key: string;
  company: string;
  message: string;
  tone: "warn" | "error";
};

// Flatten every company's attention items into one firm-wide list, error-tone
// first (a missing benefit year outranks operational backlog), capped so the
// panel stays a glance not a wall.
function buildAttention(companies: CompanySummary[]): FirmAttention[] {
  const out: FirmAttention[] = [];
  for (const c of companies) {
    for (const a of companyAttention(c)) {
      out.push({
        key: `${c.id}-${a.key}`,
        company: c.name,
        message: a.short,
        tone: a.tone,
      });
    }
  }
  out.sort((a, b) => (a.tone === b.tone ? 0 : a.tone === "error" ? -1 : 1));
  return out.slice(0, 8);
}

// "Enrollment open" gains urgency when it's about to close — show the countdown
// instead of the neutral open badge inside the last week.
function enrollBadge(c: CompanySummary) {
  if (!c.enrollment_closes_at) return null;
  const days = daysUntil(c.enrollment_closes_at);
  if (days > 7) return null;
  return (
    <Badge variant="warn">
      {days <= 0 ? "Closes today" : `Closes in ${days}d`}
    </Badge>
  );
}

// A company is "all clear" only when it has a current year and no outstanding
// operational signal — otherwise a warn badge already tells the real story.
function allClear(c: CompanySummary): boolean {
  return (
    !!c.current_year &&
    c.claims_to_review === 0 &&
    c.employees_unmatched === 0 &&
    c.dependants_pending === 0 &&
    c.underwriting_pending === 0 &&
    !c.enrollment_open &&
    !c.matching_stale
  );
}

// A scannable roster rather than a card wall: one dense row per company reads
// the same at 4 companies or 400, and it stays sortable/filterable by eye.
function CompanyTable({
  companies,
  query,
}: {
  companies: CompanySummary[];
  query: string;
}) {
  if (companies.length === 0) {
    return (
      <div className="rounded-lg border border-dashed border-border p-8 text-center text-sm text-muted-foreground">
        {query
          ? `No companies match “${query}”.`
          : "No companies yet. Add one from Company settings."}
      </div>
    );
  }

  return (
    <div className="overflow-x-auto rounded-lg border border-border">
      <table className="w-full text-sm">
        <thead>
          <tr className="border-b border-border bg-muted/40 text-left text-xs font-medium text-muted-foreground">
            <th className="px-4 py-2.5">Company</th>
            <th className="px-4 py-2.5">Benefit year</th>
            <th className="px-4 py-2.5 text-right">Members</th>
            <th className="px-4 py-2.5 text-right">Dependants</th>
            <th className="px-4 py-2.5">Status</th>
            <th className="w-10 px-4 py-2.5" />
          </tr>
        </thead>
        <tbody>
          {companies.map((c) => (
            <CompanyRow key={c.id} company={c} />
          ))}
        </tbody>
      </table>
    </div>
  );
}

function CompanyRow({ company }: { company: CompanySummary }) {
  const navigate = useNavigate();
  const setActiveClient = useSession((s) => s.setActiveClient);
  const qc = useQueryClient();

  const enter = () => {
    setActiveClient(company.id);
    // Evict the previous tenant's cache (not invalidate — that would refetch
    // previous-tenant-keyed queries in-place with the new header → 404).
    qc.removeQueries();
    navigate({ to: "/dashboard" });
  };

  const initial = company.name.trim().charAt(0).toUpperCase() || "?";

  return (
    <tr
      onClick={enter}
      className="group cursor-pointer border-b border-border last:border-0 transition-colors hover:bg-sidebar-hover"
    >
      <td className="px-4 py-3">
        <div className="flex items-center gap-3">
          <div className="flex size-8 shrink-0 items-center justify-center rounded-md bg-accent text-xs font-semibold text-accent-foreground">
            {initial}
          </div>
          <span className="font-medium text-foreground">{company.name}</span>
        </div>
      </td>
      <td className="px-4 py-3 text-muted-foreground">
        {company.current_year ? (
          <span className="text-foreground">
            {company.current_year.year}
            <span className="text-muted-foreground"> · Current</span>
          </span>
        ) : (
          <span className="text-muted-foreground">No current year</span>
        )}
      </td>
      <td className="px-4 py-3 text-right tabular-nums text-foreground">
        {company.member_count}
      </td>
      <td className="px-4 py-3 text-right tabular-nums text-foreground">
        {company.dependant_count}
      </td>
      <td className="px-4 py-3">
        <div className="flex flex-wrap items-center gap-1.5">
          {company.claims_to_review > 0 && (
            <Badge variant="warn">{company.claims_to_review} claims</Badge>
          )}
          {company.employees_unmatched > 0 && (
            <Badge variant="warn">{company.employees_unmatched} unmatched</Badge>
          )}
          {company.dependants_pending > 0 && (
            <Badge variant="warn">{company.dependants_pending} to approve</Badge>
          )}
          {company.underwriting_pending > 0 && (
            <Badge variant="warn">{company.underwriting_pending} U/W</Badge>
          )}
          {company.matching_stale && (
            <Badge variant="warn">Matching stale</Badge>
          )}
          {company.enrollment_open &&
            (enrollBadge(company) ?? (
              <Badge variant="info">Enrollment open</Badge>
            ))}
          {!company.current_year && <Badge variant="error">No year</Badge>}
          {allClear(company) && <Badge variant="good">All clear</Badge>}
        </div>
      </td>
      <td className="px-4 py-3 text-right">
        <ArrowRight className="ml-auto size-4 text-muted-foreground transition-transform group-hover:translate-x-0.5 group-hover:text-primary" />
      </td>
    </tr>
  );
}
