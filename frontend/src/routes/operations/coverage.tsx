import { useEffect, useMemo, useState } from "react";
import { useNavigate, useSearch } from "@tanstack/react-router";
import { Download, Loader2, UserSearch } from "lucide-react";
import { toast } from "sonner";
import { api } from "@/api/client";
import { useEmployeeUtilization } from "@/api/claims";
import { useBenefitStatement, useCoverageSummary } from "@/api/hooks";
import { formatError } from "@/lib/errors";
import { triggerDownload } from "@/lib/download";
import { useSession } from "@/stores/session";
import { Button } from "@/components/ui/button";
import { Segmented } from "@/components/ui/segmented";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { BenefitStatement } from "@/components/benefits/BenefitStatement";
import { UtilizationView } from "@/components/benefits/UtilizationView";
import { EmployeePicker } from "@/components/operations/EmployeePicker";
import { PortalFrame } from "@/components/operations/PortalFrame";
import { cn } from "@/lib/cn";

const ANY = "__any__";

type CoverageView = "broker" | "employee";

function countLabel(n: number): string {
  if (n === 0) return "None (unmatched)";
  return `${n} ${n === 1 ? "product" : "products"}`;
}

/** Broker-facing statement pane — full financials + claims utilization. */
function BrokerStatementPane({ employeeId }: { employeeId: string }) {
  const {
    data: statement,
    isLoading,
    isError,
    error,
  } = useBenefitStatement(employeeId);
  const { data: utilization } = useEmployeeUtilization(employeeId);

  if (isLoading) {
    return (
      <div className="flex items-center gap-2 px-2 py-10 text-sm text-muted-foreground">
        <Loader2 className="size-4 animate-spin" /> Loading statement…
      </div>
    );
  }
  if (isError) {
    return (
      <p className="text-sm text-error">
        Could not load statement: {String((error as Error)?.message ?? error)}
      </p>
    );
  }
  if (!statement) return null;
  return (
    <div className="space-y-4">
      <BenefitStatement data={statement} utilization={utilization} />
      {utilization && (
        <div>
          <div className="text-xs uppercase tracking-wider text-muted-foreground mb-2">
            Claims utilization
          </div>
          <UtilizationView data={utilization} />
        </div>
      )}
    </div>
  );
}

/** Employee coverage — one "pick an employee, see their coverage" page.
 * The Broker view shows the full statement (financials + utilization + Excel
 * export); the Employee view shows the read-only portal replica. Both ride
 * the URL (?employee=&view=) so links stay shareable. */
export function EmployeeCoveragePage() {
  const policyYearId = useSession((s) => s.currentPolicyYearId);
  const navigate = useNavigate();
  const search = useSearch({ strict: false }) as {
    employee?: string;
    view?: string;
  };
  const view: CoverageView = search.view === "employee" ? "employee" : "broker";
  const selectedId = search.employee ?? null;

  const [query, setQuery] = useState("");
  const [countFilter, setCountFilter] = useState<string>(ANY);
  const [exporting, setExporting] = useState(false);

  const { data: summary, isLoading: listLoading } =
    useCoverageSummary(policyYearId ?? undefined);
  const items = useMemo(() => summary?.items ?? [], [summary]);

  // Product-count options come from the counts actually present in the roster —
  // a count with no employees never appears in the dropdown.
  const counts = useMemo(() => {
    const present = new Set<number>();
    for (const it of items) present.add(it.product_count);
    return [...present].sort((a, b) => a - b);
  }, [items]);

  // Drop the active count filter if a roster reload no longer has that count.
  useEffect(() => {
    if (countFilter !== ANY && !counts.includes(Number(countFilter))) {
      setCountFilter(ANY);
    }
  }, [counts, countFilter]);

  const setSearchParams = (next: { employee?: string; view?: CoverageView }) =>
    void navigate({
      to: "/operations/coverage",
      search: {
        employee: next.employee ?? selectedId ?? undefined,
        view: next.view ?? view,
      },
      // Selecting people/toggling views shouldn't pile up history entries.
      replace: true,
    });

  if (!policyYearId) {
    return (
      <p className="text-sm text-muted-foreground">
        Select a policy year to view employee coverage.
      </p>
    );
  }

  const needle = query.trim().toLowerCase();
  const filtered = items.filter((it) => {
    if (
      needle &&
      !(
        it.employee_name?.toLowerCase().includes(needle) ||
        it.staff_id.toLowerCase().includes(needle)
      )
    ) {
      return false;
    }
    if (countFilter !== ANY && it.product_count !== Number(countFilter)) {
      return false;
    }
    return true;
  });

  async function handleExport() {
    if (!policyYearId) return;
    setExporting(true);
    try {
      const params = new URLSearchParams({ policy_year_id: policyYearId });
      if (needle) params.set("q", query.trim());
      if (countFilter !== ANY) params.set("product_count", countFilter);
      const blob = await api.download(
        `/employees/coverage-summary/export?${params}`,
      );
      triggerDownload(blob, "benefit-coverage.xlsx");
    } catch (e) {
      toast.error(formatError(e));
    } finally {
      setExporting(false);
    }
  }

  return (
    <div className="space-y-4">
      {/* Toolbar — filters on the left, view toggle on the right */}
      <div className="rounded-lg border border-border bg-card p-3">
        <div className="flex flex-col gap-3 sm:flex-row sm:items-center">
          <div className="flex flex-1 items-center gap-2">
            <span className="whitespace-nowrap text-xs text-muted-foreground">
              Plans covered
            </span>
            <Select value={countFilter} onValueChange={setCountFilter}>
              <SelectTrigger className="w-[180px]">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value={ANY}>Any</SelectItem>
                {counts.map((n) => (
                  <SelectItem key={n} value={String(n)}>
                    {countLabel(n)}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
            <Button
              type="button"
              variant="outline"
              onClick={handleExport}
              disabled={exporting || filtered.length === 0}
            >
              {exporting ? (
                <Loader2 className="size-4 animate-spin" />
              ) : (
                <Download className="size-4" />
              )}
              Export Excel
            </Button>
          </div>
          <Segmented<CoverageView>
            value={view}
            onChange={(v) => setSearchParams({ view: v })}
            options={[
              { value: "broker", label: "Broker view" },
              { value: "employee", label: "Employee view" },
            ]}
          />
        </div>
      </div>

      <div className="grid gap-4 lg:grid-cols-[280px_1fr]">
        <EmployeePicker
          items={filtered.map((it) => ({
            id: it.id,
            name: it.employee_name ?? it.staff_id,
            subtitle: it.staff_id,
            trailing: (
              <span
                className={cn(
                  "shrink-0 rounded-full px-1.5 py-0.5 text-[10px] tabular-nums",
                  it.product_count > 0
                    ? "bg-muted text-muted-foreground"
                    : "text-muted-foreground/60",
                )}
                title={`${it.product_count} ${it.product_count === 1 ? "product" : "products"} covered`}
              >
                {it.product_count}
              </span>
            ),
          }))}
          selectedId={selectedId}
          onSelect={(id) => setSearchParams({ employee: id })}
          isLoading={listLoading}
          query={query}
          onQueryChange={setQuery}
          header={
            <div className="flex items-center justify-between px-1 pb-2 text-[11px] text-muted-foreground">
              <span>
                {filtered.length.toLocaleString()}{" "}
                {filtered.length === 1 ? "employee" : "employees"}
              </span>
              {summary && filtered.length !== summary.total && (
                <span>of {summary.total.toLocaleString()}</span>
              )}
            </div>
          }
        />

        <div>
          {!selectedId ? (
            <div className="rounded-lg border border-dashed border-border p-10 text-center">
              <UserSearch className="mx-auto size-6 text-muted-foreground" />
              <p className="mt-2 text-sm text-muted-foreground">
                {view === "broker"
                  ? "Select an employee to view their benefit statement."
                  : "Select an employee to preview the portal exactly as they see it when they sign in."}
              </p>
            </div>
          ) : view === "broker" ? (
            <BrokerStatementPane employeeId={selectedId} />
          ) : (
            <PortalFrame employeeId={selectedId} />
          )}
        </div>
      </div>
    </div>
  );
}
