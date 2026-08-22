import { useMemo } from "react";
import { Link } from "@tanstack/react-router";
import {
  Activity,
  CalendarClock,
  Loader2,
  ReceiptText,
  ShieldQuestion,
  UserCheck,
  UserPlus,
  Users,
} from "lucide-react";
import { useAuditLog, useDashboardSummary, useMe } from "@/api/hooks";
import type { AuditLogEntry } from "@/types";
import { COMPANY_NAV } from "@/components/shell/nav";
import { Badge } from "@/components/ui/badge";
import { Kpi } from "@/components/ui/kpi";
import { cn } from "@/lib/cn";
import { companyAttention, daysUntil, parseServerDate } from "@/lib/attention";
import { useSession } from "@/stores/session";

export function CompanyDashboardPage() {
  const policyYearId = useSession((s) => s.currentPolicyYearId);
  const { data, isLoading, isFetching } = useDashboardSummary(policyYearId);
  const { data: me } = useMe();
  const activeClientId = useSession((s) => s.activeClientId);
  const selectedId = activeClientId ?? me?.active_client_id ?? null;

  const company = useMemo(
    () => data?.companies.find((c) => c.id === selectedId) ?? null,
    [data, selectedId],
  );

  // Show the loader on the first load AND while a refetch is in flight with no
  // match yet — after a client switch the cache still holds the previous
  // selection's companies (the query key is constant), so `company` is briefly
  // null for an already-selected company. Don't flash "pick a company" then.
  if (isLoading || (isFetching && !company)) {
    return (
      <div className="flex items-center gap-2 p-8 text-sm text-muted-foreground">
        <Loader2 className="size-4 animate-spin" /> Loading…
      </div>
    );
  }
  if (!company) {
    return (
      <div className="p-8 text-sm text-muted-foreground">
        Pick a company from{" "}
        <Link to="/home" className="text-primary underline">
          Home
        </Link>{" "}
        to see its dashboard.
      </div>
    );
  }

  const attention = companyAttention(company);

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-xl font-semibold text-foreground">{company.name}</h1>
        <p className="text-sm text-muted-foreground">
          {company.current_year
            ? `${company.current_year.year} benefit year · selected`
            : "No benefit year selected"}
        </p>
      </div>

      <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-4">
        <Kpi label="Members" value={company.member_count} icon={Users} />
        <Kpi label="Dependants" value={company.dependant_count} icon={UserPlus} />
        <Kpi
          label="Claims to review"
          value={company.claims_to_review}
          icon={ReceiptText}
          tone={company.claims_to_review > 0 ? "warn" : "default"}
        />
        <Kpi
          label="Unmatched members"
          value={company.employees_unmatched}
          icon={UserCheck}
          tone={company.employees_unmatched > 0 ? "warn" : "default"}
        />
        <Kpi
          label="Dependant approvals"
          value={company.dependants_pending}
          icon={UserPlus}
          tone={company.dependants_pending > 0 ? "warn" : "default"}
        />
        <Kpi
          label="U/W pending"
          value={company.underwriting_pending}
          icon={ShieldQuestion}
          tone={company.underwriting_pending > 0 ? "warn" : "default"}
        />
        <div className="col-span-2 rounded-lg border border-border bg-card p-4">
          <div className="flex items-center gap-1.5 text-xs text-muted-foreground">
            <CalendarClock className="size-3.5" />
            Enrollment
          </div>
          <div className="mt-2 flex items-center gap-2">
            {company.enrollment_open ? (
              <Badge variant="info">Enrolment period open</Badge>
            ) : (
              <Badge variant="default">Closed</Badge>
            )}
            {company.enrollment_open && company.enrollment_closes_at && (
              <span className="text-xs text-muted-foreground">
                {closesLabel(company.enrollment_closes_at)}
              </span>
            )}
          </div>
        </div>
      </div>

      {attention.length > 0 && (
        <div className="rounded-lg border border-border bg-card p-4">
          <h2 className="mb-2 text-sm font-semibold text-foreground">
            Needs attention
          </h2>
          <ul className="space-y-1.5">
            {attention.map((a) => (
              <li key={a.key} className="flex items-center gap-2 text-sm">
                <span
                  className={cn(
                    "inline-block size-1.5 rounded-full",
                    a.tone === "warn" ? "bg-warn" : "bg-error",
                  )}
                />
                <span className="text-foreground/80">{a.message}</span>
                {a.to && (
                  <Link
                    to={a.to}
                    search={a.search}
                    className="text-xs font-medium text-primary hover:underline"
                  >
                    Go →
                  </Link>
                )}
              </li>
            ))}
          </ul>
        </div>
      )}

      <div className="grid grid-cols-1 gap-6 lg:grid-cols-[1fr_20rem]">
        <div>
          <h2 className="mb-2 text-sm font-semibold text-foreground">Jump to</h2>
          <div className="space-y-4">
            {COMPANY_NAV.map((group) => (
              <div key={group.key}>
                <div className="mb-1.5 text-2xs font-medium uppercase tracking-wider text-muted-foreground">
                  {group.label}
                </div>
                <div className="grid grid-cols-2 gap-2 sm:grid-cols-3">
                  {group.items.map((item) => {
                    const Icon = item.icon;
                    return (
                      <Link
                        key={item.to}
                        to={item.to}
                        className="flex items-center gap-2 rounded-md border border-border bg-card px-3 py-2.5 text-sm text-foreground/80 transition-colors hover:border-border-strong hover:bg-sidebar-hover hover:text-foreground"
                      >
                        <Icon
                          className="size-4 shrink-0 text-muted-foreground"
                          strokeWidth={1.75}
                        />
                        <span className="truncate">{item.label}</span>
                      </Link>
                    );
                  })}
                </div>
              </div>
            ))}
          </div>
        </div>

        <RecentActivity />
      </div>
    </div>
  );
}

function closesLabel(iso: string): string {
  const days = daysUntil(iso);
  if (days <= 0) return "closes today";
  if (days === 1) return "closes tomorrow";
  if (days <= 14) return `closes in ${days} days`;
  return `closes ${parseServerDate(iso).toLocaleDateString(undefined, {
    day: "numeric",
    month: "short",
  })}`;
}

// Company-scoped audit feed (the /audit-log endpoint filters to the active
// client). A lightweight "what changed lately" panel beside the jump links.
function RecentActivity() {
  const { data, isLoading } = useAuditLog();
  const items = (data?.items ?? []).slice(0, 10);

  return (
    <div className="rounded-lg border border-border bg-card p-4">
      <div className="mb-2 flex items-center gap-1.5 text-sm font-semibold text-foreground">
        <Activity className="size-4 text-muted-foreground" strokeWidth={1.75} />
        Recent activity
      </div>
      {isLoading ? (
        <div className="flex items-center gap-2 py-4 text-xs text-muted-foreground">
          <Loader2 className="size-3.5 animate-spin" /> Loading…
        </div>
      ) : items.length === 0 ? (
        <div className="py-4 text-xs text-muted-foreground">
          No recent activity.
        </div>
      ) : (
        <ul className="space-y-2.5">
          {items.map((e) => (
            <li key={e.id} className="text-xs">
              <div className="text-foreground/80">{describe(e)}</div>
              <div className="text-subtle">{relTime(e.created_at)}</div>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

// Turn "run_matching" / "override_match" into "Run matching", "Override match".
function humanize(s: string): string {
  const t = s.replace(/_/g, " ").trim();
  return t.charAt(0).toUpperCase() + t.slice(1);
}

function describe(e: AuditLogEntry): string {
  const entity = e.entity_type.replace(/_/g, " ");
  return `${humanize(e.action)} · ${entity}`;
}

function relTime(iso: string): string {
  const then = parseServerDate(iso);
  const diff = Date.now() - then.getTime();
  const mins = Math.round(diff / 60_000);
  if (mins < 1) return "just now";
  if (mins < 60) return `${mins}m ago`;
  const hrs = Math.round(mins / 60);
  if (hrs < 24) return `${hrs}h ago`;
  const days = Math.round(hrs / 24);
  if (days < 7) return `${days}d ago`;
  return then.toLocaleDateString(undefined, {
    day: "numeric",
    month: "short",
  });
}
