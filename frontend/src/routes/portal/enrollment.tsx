/** "My enrollment" — during an open window the member reviews their plans and
 * chooses to upgrade/downgrade, decline voluntary cover, include dependants,
 * and trade leave. Submissions await broker confirmation. */
import { useMemo } from "react";
import { useNavigate, useSearch } from "@tanstack/react-router";
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
  const navigate = useNavigate();
  const search = useSearch({ strict: false }) as { p?: string };
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

  // **The dependants query gates the page, not just the family lists.**
  // `buildElectionsPayload` persists EVERY dependant id on a product whose
  // family cover is compulsory, so an unresolved list means it persists none —
  // and since Send now saves before it submits, one press on a page that had
  // rendered with `dependantRefs: []` would write "nobody is covered" for those
  // products and project it into the member's cover at broker confirm. It is
  // reachable on first paint through a restored `?p=review` link, where the
  // deck opens on the step whose primary action is Send.
  if (enrollment.isLoading || dependants.isPending) {
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
  // A FAILED dependants fetch is the same hazard as an unfinished one, and it
  // does not resolve itself (`usePortalDependants` sets `retry: false`), so it
  // gets the retryable error state rather than a form that would silently elect
  // on the member's behalf.
  if (dependants.isError) {
    return <PortalErrorState onRetry={() => void dependants.refetch()} />;
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
        // Which step is open lives in the URL, so it survives a refresh and the
        // back button — the same contract the coverage deck has. `replace`
        // because stepping through nine products is filling in a form, not
        // navigating: without it Back walks back through every product visited
        // instead of leaving the page.
        slideKey={search.p ?? null}
        onSlideKeyChange={(p) =>
          navigate({ to: "/portal/enrollment", search: { p }, replace: true })
        }
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
