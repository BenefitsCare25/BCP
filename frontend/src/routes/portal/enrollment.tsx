/** "My enrollment" — during an open window the member reviews their plans and
 * chooses to upgrade/downgrade, decline voluntary cover, include dependants,
 * and trade leave. Submissions await broker confirmation. */
import { useMemo } from "react";
import { FileWarning } from "lucide-react";
import {
  usePortalDependants,
  usePortalEnrollment,
  useSaveMyElections,
  useSetMyLeave,
  useSubmitMyEnrollment,
} from "@/api/portal";
import type { DependantRef } from "@/components/enrollment/electionShared";
import {
  dependantName,
  dependantRelationship,
} from "@/components/portal/DependantsTable";
import { MemberEnrollmentPanel } from "@/components/portal/MemberEnrollmentPanel";
import { PortalErrorState } from "@/components/portal/PortalErrorState";
import { Skeleton } from "@/components/ui/skeleton";
import { isNotFoundError } from "@/lib/errors";

export function PortalEnrollmentPage() {
  const enrollment = usePortalEnrollment();
  const dependants = usePortalDependants();
  const saveElections = useSaveMyElections();
  const setLeave = useSetMyLeave();
  const submit = useSubmitMyEnrollment();

  // Only active (approved) dependants are electable for coverage — pending
  // self-added dependants join once the broker approves them.
  const dependantRefs = useMemo<DependantRef[]>(
    () =>
      (dependants.data ?? [])
        .filter((d) => d.status === "active")
        .map((d) => ({
          id: d.id,
          name: dependantName(d),
          relationship: dependantRelationship(d),
        })),
    [dependants.data],
  );

  if (enrollment.isLoading) {
    return (
      <div className="space-y-4">
        <Skeleton className="h-20 w-full" />
        <Skeleton className="h-48 w-full" />
      </div>
    );
  }

  // A fetch failure must not read as "no enrollment period is open" — only a
  // real 404 (no active coverage) gets the confident empty state.
  if (enrollment.isError && !isNotFoundError(enrollment.error)) {
    return <PortalErrorState onRetry={() => void enrollment.refetch()} />;
  }
  if (enrollment.isError) {
    return (
      <div className="rounded-lg border border-border bg-card p-8 text-center">
        <FileWarning className="mx-auto size-6 text-muted-foreground" />
        <p className="mt-2 text-sm font-medium text-foreground">
          No active coverage found
        </p>
        <p className="mt-1 text-xs text-muted-foreground">
          Your company doesn't have an active policy year yet, or your record
          isn't on the current roster. Contact your HR or broker.
        </p>
      </div>
    );
  }

  return (
    <div className="space-y-4">
      <div>
        <h1 className="text-lg font-semibold text-foreground">My enrollment</h1>
        <p className="text-sm text-muted-foreground">
          Review your plans and make changes while the enrollment window is
          open.
        </p>
      </div>
      <MemberEnrollmentPanel
        data={enrollment.data ?? { window: null, enrollment: null, options: null }}
        dependants={dependantRefs}
        onSaveElections={(elections) => saveElections.mutateAsync(elections)}
        onSaveLeave={(input) => setLeave.mutateAsync(input)}
        onSubmit={(ack) => submit.mutateAsync(ack)}
        saving={saveElections.isPending}
        savingLeave={setLeave.isPending}
        submitting={submit.isPending}
      />
    </div>
  );
}
