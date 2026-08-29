/** One employee's LOG cases, on Coverage & Members.
 *
 * This is the requirement's home: the only per-employee broker surface, and the
 * screen that already shows the benefit statement and the utilization an
 * assessor needs while entering a case. It renders the SAME `LogCaseForm` the
 * claims queue mounts, with the member locked.
 *
 * Placement is a reachability decision, not a file-location one — these are
 * mounted from `routes/operations/coverage.tsx`, a page `router.tsx` routes
 * today (see docs/ORPHANED_UI_RECOVERY.md for what happens when that is assumed
 * rather than checked).
 *
 * It is deliberately TWO pieces. The action belongs in the identity strip,
 * beside portal access, because recording a case is an administrative act on
 * the person. The list is only worth space when there is something in it: as
 * one card with a title, a paragraph of explanation and the sentence "No LOG
 * cases for this employee", it occupied 150px above the coverage a broker
 * opened the page to read, and on almost every employee it said nothing.
 */
import { useState } from "react";
import { Link } from "@tanstack/react-router";
import { Plus } from "lucide-react";
import { useEmployeeLogCases } from "@/api/claims";
import { useSession } from "@/stores/session";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { SectionLabel } from "@/components/ui/section-label";
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

/** Records a request that reached the broker by email or phone rather than
 * through the portal. It enters the claims queue like any other case. */
export function NewLogCaseButton({ employeeId }: { employeeId: string }) {
  const [open, setOpen] = useState(false);
  return (
    <>
      <Button size="sm" variant="outline" onClick={() => setOpen(true)}>
        <Plus className="size-4" aria-hidden />
        New LOG case
      </Button>
      <LogCaseForm open={open} onOpenChange={setOpen} employeeId={employeeId} />
    </>
  );
}

/** The cases already on file. Renders nothing when there are none. */
export function LogCaseStrip({ employeeId }: { employeeId: string }) {
  const policyYearId = useSession((s) => s.currentPolicyYearId);
  const { data } = useEmployeeLogCases(policyYearId ?? undefined, employeeId);
  const cases = data?.items ?? [];
  if (cases.length === 0) return null;

  return (
    <section>
      <SectionLabel as="h3" className="mb-1.5">
        LOG cases ({cases.length})
      </SectionLabel>
      <ul className="overflow-hidden rounded-lg border border-border bg-card">
        {cases.map((c) => (
          <li key={c.id} className="border-b border-border last:border-b-0">
            <Link
              to="/claims/review"
              search={{ tab: "log", claim: c.id }}
              className="flex flex-wrap items-center gap-x-3 gap-y-1 px-3 py-2.5 text-sm transition-colors hover:bg-muted focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-ring/40"
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
    </section>
  );
}
