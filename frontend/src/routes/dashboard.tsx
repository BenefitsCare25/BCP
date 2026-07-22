import { useMemo } from "react";
import { Link } from "@tanstack/react-router";
import {
  CalendarClock,
  Loader2,
  ReceiptText,
  UserPlus,
  Users,
} from "lucide-react";
import {
  type CompanySummary,
  useDashboardSummary,
  useMe,
} from "@/api/hooks";
import { COMPANY_NAV } from "@/components/shell/nav";
import { Badge } from "@/components/ui/badge";
import { Kpi } from "@/components/ui/kpi";
import { cn } from "@/lib/cn";
import { useSession } from "@/stores/session";

export function CompanyDashboardPage() {
  const { data, isLoading, isFetching } = useDashboardSummary();
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

  const attention = buildAttention(company);

  return (
    <div className="mx-auto max-w-5xl space-y-6">
      <div>
        <h1 className="text-xl font-semibold text-foreground">{company.name}</h1>
        <p className="text-sm text-muted-foreground">
          {company.current_year
            ? `${company.current_year.year} benefit year · current`
            : "No current benefit year set"}
        </p>
      </div>

      <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
        <Kpi label="Members" value={company.member_count} icon={Users} />
        <Kpi label="Dependants" value={company.dependant_count} icon={UserPlus} />
        <Kpi
          label="Claims to review"
          value={company.claims_to_review}
          icon={ReceiptText}
          tone={company.claims_to_review > 0 ? "warn" : "default"}
        />
        <div className="rounded-lg border border-border bg-card p-4">
          <div className="flex items-center gap-1.5 text-xs text-muted-foreground">
            <CalendarClock className="size-3.5" />
            Enrollment
          </div>
          <div className="mt-2">
            {company.enrollment_open ? (
              <Badge variant="info">Window open</Badge>
            ) : (
              <Badge variant="default">Closed</Badge>
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

      <div>
        <h2 className="mb-2 text-sm font-semibold text-foreground">Jump to</h2>
        <div className="space-y-4">
          {COMPANY_NAV.map((group) => (
            <div key={group.key}>
              <div className="mb-1.5 text-xs font-medium uppercase tracking-wider text-muted-foreground">
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
    </div>
  );
}

type Attention = {
  key: string;
  message: string;
  tone: "warn" | "error";
  to?: string;
};

function buildAttention(company: CompanySummary): Attention[] {
  const out: Attention[] = [];
  if (company.claims_to_review > 0) {
    out.push({
      key: "claims",
      message: `${company.claims_to_review} claim${company.claims_to_review === 1 ? "" : "s"} awaiting review`,
      tone: "warn",
      to: "/operations/claims",
    });
  }
  if (!company.current_year) {
    out.push({
      key: "year",
      message: "No current benefit year — set one in Company & Benefits",
      tone: "error",
      to: "/configuration",
    });
  }
  return out;
}
