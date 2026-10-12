import { FirmOwnerOnly } from "@/components/auth/FirmOwnerOnly";
/** Orphaned plan overrides — overrides stranded by category churn, of two kinds
 * (`orphan_reason`): the elected product left the employee's cohort after a
 * re-match (inert — the coverage resolver skips it), or the elected tier was
 * deleted by a slip re-parse (still applied — removing it returns the member
 * to their cohort default plan). Surfaced so brokers can reconcile instead of
 * leaving silent ghosts. Rendered on Enrollment → Coverage changes; hidden
 * when there are none. */
import { useState } from "react";
import { Trash2 } from "lucide-react";
import { toast } from "sonner";
import {
  type OrphanOverride,
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
import { ConflictDetailError, formatError } from "@/lib/errors";

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

const isLive = (o: OrphanOverride) => o.orphan_reason === "tier_missing";

function RemoveDescription({ target }: { target: OrphanOverride }) {
  if (!isLive(target)) {
    return (
      <>
        Removes the <strong>{target.product_code}</strong> override for this
        employee. The override is already inactive (the product left their
        cohort), so effective coverage does not change.
      </>
    );
  }
  return (
    <>
      The <strong>{target.product_code}</strong> override is still applied
      {target.declined ? (
        " (the employee declined this product)"
      ) : target.plan_code ? (
        <> (the employee is on <strong>{target.plan_code}</strong>)</>
      ) : null}
      , but the tier they elected was removed by a slip re-parse. Removing the
      override returns them to their cohort default plan, which can change their
      cover, price and underwriting. To keep their choice, re-elect it on the
      Enrollment page instead.
    </>
  );
}

type Confirm = { target: OrphanOverride; openPeriodReason: string | null };

export function OrphanOverridesPanel({
  policyYearId,
}: {
  policyYearId: string;
}) {
  const { data: orphans = [] } = useOrphanOverrides(policyYearId);
  const remove = useDeletePlanOverride();
  const [confirm, setConfirm] = useState<Confirm | null>(null);

  if (orphans.length === 0) return null;
  const liveCount = orphans.filter(isLive).length;

  const onConfirm = async () => {
    if (!confirm) return;
    const { target, openPeriodReason } = confirm;
    try {
      await remove.mutateAsync({
        employeeId: target.employee_id,
        productCode: target.product_code,
        acknowledgeOpenEnrollment: openPeriodReason !== null,
      });
      toast.success("Override removed");
      setConfirm(null);
    } catch (err) {
      if (
        openPeriodReason === null &&
        err instanceof ConflictDetailError &&
        err.detail.code === "open_enrollment_election"
      ) {
        const reason = typeof err.detail.message === "string" ? err.detail.message : "";
        setConfirm({ target, openPeriodReason: reason });
        return;
      }
      toast.error(formatError(err));
    }
  };

  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-sm">
          Orphaned plan overrides ({orphans.length})
        </CardTitle>
        <CardDescription>
          These employees have a plan override left behind by a re-match or a
          slip re-parse. Overrides marked Inactive are for a product no longer in
          their cohort and no longer apply; remove them to keep coverage records
          clean.
          {liveCount > 0 &&
            " Overrides marked Still applied keep the member on their elected plan although the tier they elected was removed. Removing one changes their cover."}
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
                {isLive(o) ? (
                  <Badge variant="warn">Still applied · tier removed</Badge>
                ) : (
                  <Badge>Inactive</Badge>
                )}
                <span className="text-xs text-muted-foreground">
                  {o.declined ? "Declined" : (o.plan_code ?? "—")} · source:{" "}
                  {o.source.replace(/_/g, " ")}
                </span>
              </div>
              <FirmOwnerOnly><Button
                size="sm"
                variant="outline"
                className="text-error hover:text-error"
                disabled={remove.isPending}
                onClick={() => setConfirm({ target: o, openPeriodReason: null })}
              >
                <Trash2 className="size-3.5" /> Remove override
              </Button></FirmOwnerOnly>
            </li>
          ))}
        </ul>
      </CardContent>

      <FirmOwnerOnly><AlertDialog
        open={!!confirm}
        onOpenChange={(o) => !o && setConfirm(null)}
        title={
          confirm?.openPeriodReason != null
            ? "The open enrolment period will replace this"
            : confirm && isLive(confirm.target)
              ? "Remove an override that is still applied?"
              : "Remove this orphaned override?"
        }
        description={
          !confirm ? null : confirm.openPeriodReason != null ? (
            `${confirm.openPeriodReason} Remove the override anyway, or change their selection on the Enrollment page instead.`
          ) : (
            <RemoveDescription target={confirm.target} />
          )
        }
        confirmLabel={
          confirm?.openPeriodReason != null
            ? "Remove anyway"
            : confirm && isLive(confirm.target)
              ? "Remove and revert to default"
              : "Remove override"
        }
        confirmVariant="destructive"
        loading={remove.isPending}
        onConfirm={onConfirm}
      /></FirmOwnerOnly>
    </Card>
  );
}
