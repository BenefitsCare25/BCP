/** Voluntary cover: who is enrolled, who is only eligible, and the broker's
 * one-click record of a take-up.
 *
 * "Voluntary" on a slip means eligible — not covered — until the member (or a
 * dependant) is enrolled. The enrolment is stored the same way an enrolment
 * period or a bulk change stores it: a coverage override on the product, so it
 * appears in Coverage changes and reverts from there.
 */
import { Loader2, UserPlus, Users, X } from "lucide-react";
import { toast } from "sonner";
import { useSetPlanOverride } from "@/api/enrollment";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { formatError } from "@/lib/errors";
import type { CoverageLine, DependantSummary } from "@/types";

function depLabel(d: DependantSummary): string {
  if (d.name && d.relationship) return `${d.name} (${d.relationship})`;
  return d.name ?? d.relationship ?? "Dependant";
}

interface Props {
  line: CoverageLine;
  /** Absent on read-only surfaces: the lists render, the actions do not. */
  employeeId?: string;
  canEdit?: boolean;
}

export function EnrolmentControls({ line, employeeId, canEdit = false }: Props) {
  const setOverride = useSetPlanOverride();
  const editable = canEdit && Boolean(employeeId);
  const eligibleOnly = line.enrolment === "eligible";
  const covered = line.covered_dependants;
  const eligible = line.eligible_dependants ?? [];
  const voluntaryDeps = line.dependant_cover === "voluntary";

  const save = (
    body: { planCode?: string | null; coveredDependantIds?: string[] },
    done: string,
  ) => {
    if (!employeeId) return;
    setOverride.mutate(
      { employeeId, productCode: line.product_code, ...body },
      {
        onSuccess: () => toast.success(done),
        onError: (err) => toast.error(formatError(err)),
      },
    );
  };

  // A plan the member elected must be resent (the write replaces it); a
  // cohort-default plan is sent as null so the member keeps following the
  // category rather than being pinned to today's plan code.
  const setDependants = (ids: string[], done: string) =>
    save(
      { planCode: line.plan_overridden ? line.plan_code : null, coveredDependantIds: ids },
      done,
    );
  // Taking up the cover: compulsory dependant cover comes with it; voluntary
  // dependants are enrolled one by one afterwards.
  const enrolMember = () =>
    save(
      {
        planCode: null,
        coveredDependantIds:
          line.dependant_cover === "compulsory" ? eligible.map((d) => d.id) : [],
      },
      `Enrolled in ${line.product_code}`,
    );

  if (!eligibleOnly && covered.length === 0 && eligible.length === 0) return null;

  return (
    <div className="flex flex-col gap-2 text-xs">
      {eligibleOnly && (
        <div className="flex flex-wrap items-center gap-2 rounded-md border border-border bg-card px-2.5 py-2">
          <Badge variant="outline">Eligible · not enrolled</Badge>
          <span className="text-muted-foreground">
            Voluntary cover. Not claimable until enrolled.
          </span>
          {editable && (
            <Button
              size="sm"
              variant="outline"
              className="ml-auto"
              disabled={setOverride.isPending}
              onClick={enrolMember}
            >
              {setOverride.isPending ? (
                <Loader2 className="size-3.5 animate-spin" aria-hidden />
              ) : (
                <UserPlus className="size-3.5" aria-hidden />
              )}
              Mark enrolled
            </Button>
          )}
        </div>
      )}

      {covered.length > 0 && (
        <div className="flex flex-wrap items-center gap-1.5 text-muted-foreground">
          <Users className="size-3.5 shrink-0" aria-hidden />
          <span>Also covers</span>
          {covered.map((d) => (
            <Badge key={d.id} variant="outline" className="gap-1">
              {depLabel(d)}
              {editable && voluntaryDeps && (
                <button
                  type="button"
                  aria-label={`Remove ${d.name ?? "dependant"} from ${line.product_code}`}
                  disabled={setOverride.isPending}
                  onClick={() =>
                    setDependants(
                      covered.filter((c) => c.id !== d.id).map((c) => c.id),
                      `${d.name ?? "Dependant"} removed from ${line.product_code}`,
                    )
                  }
                  className="rounded text-muted-foreground hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring/40"
                >
                  <X className="size-3" aria-hidden />
                </button>
              )}
            </Badge>
          ))}
        </div>
      )}

      {eligible.length > 0 && (
        <div className="flex flex-wrap items-center gap-1.5 text-muted-foreground">
          <Users className="size-3.5 shrink-0" aria-hidden />
          <span>Eligible, not enrolled</span>
          {eligible.map((d) => (
            <span
              key={d.id}
              className="inline-flex items-center gap-1 rounded-md border border-dashed border-border px-1.5 py-0.5 text-foreground/80"
            >
              {depLabel(d)}
              {editable && !eligibleOnly && (
                <button
                  type="button"
                  disabled={setOverride.isPending}
                  onClick={() =>
                    setDependants(
                      [...covered.map((c) => c.id), d.id],
                      `${d.name ?? "Dependant"} enrolled in ${line.product_code}`,
                    )
                  }
                  className="rounded px-1 font-medium text-primary hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring/40"
                >
                  Enrol
                </button>
              )}
            </span>
          ))}
        </div>
      )}
    </div>
  );
}
