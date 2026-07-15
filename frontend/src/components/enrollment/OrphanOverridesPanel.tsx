/** Orphaned plan overrides — overrides stranded by a re-match (the elected
 * product is no longer in the employee's cohort). They're inert (the coverage
 * resolver skips them) but surfaced here so brokers can reconcile instead of
 * leaving silent ghosts. Rendered on the Employees (matching) page; hidden
 * when there are none. */
import { useState } from "react";
import { Trash2 } from "lucide-react";
import { toast } from "sonner";
import {
  type PlanOverride,
  useDeletePlanOverride,
  useOrphanOverrides,
} from "@/api/enrollment";
import { useEmployee } from "@/api/hooks";
import { AlertDialog } from "@/components/ui/alert-dialog";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { formatError } from "@/lib/errors";

/** Best-effort employee label; orphans are rare so a per-row lookup is fine
 * (useEmployee is cached by query key). */
function EmployeeLabel({ employeeId }: { employeeId: string }) {
  const { data } = useEmployee(employeeId);
  if (!data) {
    return <span className="font-mono text-xs">{employeeId.slice(0, 8)}…</span>;
  }
  return (
    <span>
      <span className="font-medium text-foreground">
        {data.employee_name ?? data.staff_id}
      </span>{" "}
      <span className="font-mono text-xs text-muted-foreground">
        {data.staff_id}
      </span>
    </span>
  );
}

export function OrphanOverridesPanel({
  policyYearId,
}: {
  policyYearId: string;
}) {
  const { data: orphans = [] } = useOrphanOverrides(policyYearId);
  const remove = useDeletePlanOverride();
  const [confirmTarget, setConfirmTarget] = useState<PlanOverride | null>(null);

  if (orphans.length === 0) return null;

  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-sm">
          Orphaned plan overrides ({orphans.length})
        </CardTitle>
        <CardDescription>
          These employees have a plan override for a product that's no longer
          in their cohort after re-matching. The overrides are inactive; remove
          them to keep coverage records clean.
        </CardDescription>
      </CardHeader>
      <CardContent>
        <ul className="divide-y divide-border rounded-md border border-border">
          {orphans.map((o) => (
            <li
              key={o.id}
              className="flex flex-wrap items-center justify-between gap-2 px-3 py-2 text-sm"
            >
              <div className="flex min-w-0 flex-wrap items-center gap-2">
                <EmployeeLabel employeeId={o.employee_id} />
                <Badge variant="outline">{o.product_code}</Badge>
                <span className="text-xs text-muted-foreground">
                  {o.declined ? "Declined" : (o.plan_code ?? "—")} · source:{" "}
                  {o.source.replace(/_/g, " ")}
                </span>
              </div>
              <Button
                size="sm"
                variant="outline"
                className="text-error hover:text-error"
                disabled={remove.isPending}
                onClick={() => setConfirmTarget(o)}
              >
                <Trash2 className="size-3.5" /> Remove override
              </Button>
            </li>
          ))}
        </ul>
      </CardContent>

      <AlertDialog
        open={!!confirmTarget}
        onOpenChange={(o) => !o && setConfirmTarget(null)}
        title="Remove this orphaned override?"
        description={
          confirmTarget ? (
            <>
              Removes the <strong>{confirmTarget.product_code}</strong> override
              for this employee. The override is already inactive (the product
              left their cohort), so effective coverage does not change.
            </>
          ) : null
        }
        confirmLabel="Remove override"
        confirmVariant="destructive"
        loading={remove.isPending}
        onConfirm={async () => {
          if (!confirmTarget) return;
          try {
            await remove.mutateAsync({
              employeeId: confirmTarget.employee_id,
              productCode: confirmTarget.product_code,
            });
            toast.success("Override removed");
            setConfirmTarget(null);
          } catch (err) {
            toast.error(formatError(err));
          }
        }}
      />
    </Card>
  );
}
