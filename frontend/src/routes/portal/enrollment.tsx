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
import type { DependantRef } from "@/components/enrollment/electionCore";
import {
  dependantName,
  dependantRelationship,
} from "@/lib/dependant";
import { MemberEnrollmentPanel } from "@/components/portal/MemberEnrollmentPanel";
import { PortalErrorState } from "@/components/portal/PortalErrorState";
import { Skeleton } from "@/components/ui/skeleton";
import { isNotFoundError } from "@/lib/errors";
import { useDocumentTitle } from "@/lib/useDocumentTitle";

export function PortalEnrollmentPage() {
  useDocumentTitle("My enrollment");
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
    // No page heading and no lede. The shell already carries the h1 and the
    // nav already says which section this is, so both only repeated what the
    // member could see; the deadline is the one fact worth the space, and the
    // panel states it.
    <div className="space-y-4">
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
