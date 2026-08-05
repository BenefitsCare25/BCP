/** One employee's LOG cases, on Coverage & Members.
 *
 * This is the requirement's home: the only per-employee broker surface, and the
 * screen that already shows the benefit statement and the utilization an
 * assessor needs while entering a case. It renders the SAME `LogCaseForm` the
 * claims queue mounts, with the member locked.
 *
 * Placement is a reachability decision, not a file-location one — this card is
 * mounted in `routes/operations/coverage.tsx`, a page `router.tsx` routes today
 * (see docs/ORPHANED_UI_RECOVERY.md for what happens when that is assumed
 * rather than checked).
 */
import { useState } from "react";
import { Link } from "@tanstack/react-router";
import { Plus } from "lucide-react";
import { useEmployeeLogCases } from "@/api/claims";
import { useSession } from "@/stores/session";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { LogCaseForm } from "@/components/claims/LogCaseForm";
import { fmtDate } from "@/lib/format";

const STATUS_TONE: Record<string, "good" | "warn" | "error" | "info" | "outline"> = {
  submitted: "info",
  ai_review_pending: "outline",
  ai_verified: "good",
  ai_flagged: "error",
  needs_info: "warn",
  approved: "good",
  rejected: "error",
};

function statusLabel(status: string): string {
  return status.replace(/_/g, " ").replace(/^./, (c) => c.toUpperCase());
}

export function EmployeeLogCases({ employeeId }: { employeeId: string }) {
  const policyYearId = useSession((s) => s.currentPolicyYearId);
  const [formOpen, setFormOpen] = useState(false);
  const { data, isLoading } = useEmployeeLogCases(
    policyYearId ?? undefined,
    employeeId,
  );
  const cases = data?.items ?? [];

  return (
    <Card>
      <CardHeader className="pb-4">
        <div className="flex flex-wrap items-start justify-between gap-x-4 gap-y-3">
          <div className="min-w-0 space-y-1">
            <CardTitle>LOG cases</CardTitle>
            <CardDescription className="max-w-prose">
              Requests recorded here rather than submitted through the portal —
              they enter the claims queue for review like any other case.
            </CardDescription>
          </div>
          <Button
            size="sm"
            className="shrink-0 basis-auto"
            onClick={() => setFormOpen(true)}
          >
            <Plus className="size-4" />
            New LOG case
          </Button>
        </div>
      </CardHeader>
      <CardContent className="pb-6">
        {isLoading ? (
          <p className="text-sm text-muted-foreground">Loading…</p>
        ) : cases.length === 0 ? (
          <p className="text-sm text-muted-foreground">
            No LOG cases for this employee.
          </p>
        ) : (
          <ul className="space-y-2">
            {cases.map((c) => (
              <li key={c.id}>
                <Link
                  to="/operations/claims"
                  search={{ tab: "queue", claim: c.id }}
                  className="flex flex-wrap items-center gap-x-3 gap-y-1 rounded-md border border-border bg-card px-3 py-2.5 text-sm transition-colors hover:bg-muted focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring/40"
                >
                  <span className="font-medium">
                    {c.product_code ?? c.flex_category_name ?? "—"}
                  </span>
                  <span className="text-muted-foreground">
                    {c.provider_name ?? "No provider stated"}
                  </span>
                  <span className="tabular-nums text-muted-foreground">
                    {fmtDate(c.incurred_date)}
                  </span>
                  <span className="ml-auto flex items-center gap-2 whitespace-nowrap">
                    <span className="tabular-nums">
                      {c.currency} {c.amount_claimed.toFixed(2)}
                    </span>
                    <Badge variant={STATUS_TONE[c.status] ?? "outline"}>
                      {statusLabel(c.status)}
                    </Badge>
                  </span>
                </Link>
              </li>
            ))}
          </ul>
        )}
      </CardContent>

      <LogCaseForm
        open={formOpen}
        onOpenChange={setFormOpen}
        employeeId={employeeId}
      />
    </Card>
  );
}
