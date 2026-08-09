import { useEffect, useMemo, useState } from "react";
import { Link, useNavigate, useSearch } from "@tanstack/react-router";
import { Loader2, TableProperties, UserSearch } from "lucide-react";
import { useEmployeeUtilization } from "@/api/claims";
import { useBenefitStatement, useCoverageSummary } from "@/api/hooks";
import { useSession } from "@/stores/session";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import { Segmented } from "@/components/ui/segmented";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { BenefitStatement } from "@/components/benefits/BenefitStatement";
import { CoverageChanges } from "@/components/benefits/CoverageChanges";
import {
  LogCaseStrip,
  NewLogCaseButton,
} from "@/components/claims/EmployeeLogCases";
import { EmployeePicker } from "@/components/operations/EmployeePicker";
import { MemberAccountActions } from "@/components/operations/MemberAccountActions";
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
      {/* The two administrative acts on this PERSON — granting portal access
       * and recording a request that arrived outside the portal — ride in the
       * statement's identity strip rather than as cards above it.
       *
       * Portal access is the only surface in the app that can create a member's
       * portal account, mint a set-password link or set their password: the
       * backend endpoints have no other UI, and it shipped unreachable once
       * already when the nav consolidation retired the page that hosted it (see
       * docs/ORPHANED_UI_RECOVERY.md). It stays on the first screenful, with
       * the account's state printed on the button, so nothing about it is
       * hidden — only the controls that change it.
       *
       * "Roster record" is the other half of the pair: this page owns what
       * matching PRODUCED, Member Listing owns what it ran ON, so the roster
       * row is one click away rather than something to go and find by name. */}
      <BenefitStatement
        data={statement}
        utilization={utilization}
        actions={
          <>
            <MemberAccountActions
              employeeId={employeeId}
              staffId={statement.employee.staff_id}
            />
            <NewLogCaseButton employeeId={employeeId} />
            <Button asChild variant="outline" size="sm">
              <Link
                to="/policy-admin/member-listing"
                search={{ tab: "employees", employee: employeeId }}
                title="Open this member's roster row — attributes, matching and manual mapping"
              >
                <TableProperties className="size-4" />
                Roster record
              </Link>
            </Button>
          </>
        }
      />
      {/* Overrides and the history behind them — moved off the roster sheet,
       * because an override changes the cover this page is showing. */}
      <CoverageChanges employeeId={employeeId} />
      <LogCaseStrip employeeId={employeeId} />
    </div>
  );
}

/** Member coverage — one "pick an employee, see their coverage" page.
 * The Broker view shows the full statement (financials, utilization, schedules
 * and the flex wallet); the Employee view shows the read-only portal replica.
 * Both ride the URL (?employee=&view=) so links stay shareable. */
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
  // Off by default: this page is about who is covered NOW, and a roster that
  // silently included everyone who ever left would change the figure every
  // broker reads off the header. On, it is the only way to reach a leaver's
  // Portal access sheet — the sheet's `left`/`settling`/`ended` states are all
  // about people this list excludes (`services/coverage_summary.py`).
  const [includeLeft, setIncludeLeft] = useState(false);

  const { data: summary, isLoading: listLoading } = useCoverageSummary(
    policyYearId ?? undefined,
    includeLeft,
  );
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
      to: "/policy-admin/member-coverage",
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

  return (
    <div className="space-y-4">
      {/* Toolbar — the picker's filter on the left, view toggle on the right.
       * Deliberately NOT a card: a bordered panel around two controls is a
       * container standing in for grouping that alignment already does.
       *
       * There is no export here. The one that lived on this toolbar wrote four
       * columns (staff ID, name, product count, product names) that the
       * Employee listing report on Member Listing already carries alongside the
       * resolved plans, financials and flex — one artifact, two depths, on two
       * pages. Export lives with the roster. */}
      <div>
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
            // The one word that stops a leaver's row reading as a colleague
            // still on cover. On the subtitle rather than the trailing slot,
            // which already carries the product count.
            subtitle: it.left ? `${it.staff_id} · Left` : it.staff_id,
            trailing: (
              <span
                className={cn(
                  "shrink-0 rounded-full px-1.5 py-0.5 text-2xs tabular-nums",
                  it.product_count > 0
                    ? "bg-muted text-muted-foreground"
                    : "text-subtle",
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
            <>
              <div className="flex items-center justify-between px-1 pb-2 text-2xs text-muted-foreground">
                <span>
                  {filtered.length.toLocaleString()}{" "}
                  {filtered.length === 1 ? "employee" : "employees"}
                </span>
                {summary && filtered.length !== summary.total && (
                  <span>of {summary.total.toLocaleString()}</span>
                )}
              </div>
              {/* A plain label wrapping the box, so the words are the hit
                  target too — at 16px the box alone is under the 24px minimum
                  and this sits in a narrow rail. */}
              <label className="mb-2 flex cursor-pointer items-center gap-2 px-1 text-2xs text-muted-foreground">
                <Checkbox
                  checked={includeLeft}
                  onCheckedChange={(v) => setIncludeLeft(v === true)}
                />
                Include leavers
              </label>
            </>
          }
        />

        {/* `min-w-0` is load-bearing: a grid item defaults to `min-width:auto`,
         * so the coverage table's own `min-w-[40rem]` pushed this 1fr column
         * WIDER than its track instead of scrolling inside it — and the card
         * around it clips its corners with `overflow-hidden`, so at laptop
         * widths the Claims column was simply cut off with no way to reach it. */}
        <div className="min-w-0">
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
