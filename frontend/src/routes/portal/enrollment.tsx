/** "My enrollment" — during an open window the member reviews their plans and
 * chooses to upgrade/downgrade, decline voluntary cover, include dependants,
 * and trade leave. Submissions await broker confirmation. */
import { useMemo } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { useNavigate, useSearch } from "@tanstack/react-router";
import { FileWarning } from "lucide-react";
import {
  usePortalDependants,
  usePortalEnrollment,
  useSaveMyElections,
  useSaveMyEnrollmentDraft,
  useSetMyLeave,
  useSubmitMyEnrollment,
} from "@/api/portal";
import {
  usePortalEnrollmentForm,
  useSignEnrollmentForm,
} from "@/api/portalEnrollmentForms";
import type { DependantRef } from "@/components/enrollment/electionCore";
import { MyFormsMount } from "@/components/portal/enrollment/form/MyFormsMount";
import {
  dependantName,
  dependantRelationship,
} from "@/lib/dependant";
import { MemberEnrollmentPanel } from "@/components/portal/MemberEnrollmentPanel";
import { PortalErrorState } from "@/components/portal/PortalErrorState";
import { Skeleton } from "@/components/ui/skeleton";
import { isNotFoundError } from "@/lib/errors";
import { useDocumentTitle } from "@/lib/useDocumentTitle";
import { useCompany } from "@/components/portal/useCompany";
import { EnrollmentUpdates, EnrollmentStatusNotice } from "@/components/portal/EnrollmentNotices";
import { useEnrollmentNotices, useEnrollmentState } from "@/api/enrollmentEvents";

export function PortalEnrollmentPage() {
  useDocumentTitle("My enrollment");
  const navigate = useNavigate();
  const company = useCompany();
  const search = useSearch({ strict: false }) as { p?: string };
  const enrollment = usePortalEnrollment();
  const dependants = usePortalDependants();
  const saveElections = useSaveMyElections();
  const saveDraft = useSaveMyEnrollmentDraft();
  const setLeave = useSetMyLeave();
  const submit = useSubmitMyEnrollment();
  // The e-form wraps the deck whenever a period is open for this member.
  const formContext = usePortalEnrollmentForm(!!enrollment.data?.window);
  const sign = useSignEnrollmentForm();
  const notices = useEnrollmentNotices();
  const live = useEnrollmentState(enrollment.data?.enrollment?.id);
  const qc = useQueryClient();
  const latestNotice = notices.data?.items.find((item) => item.enrollment_id === enrollment.data?.enrollment?.id);
  const changed = (!!latestNotice && latestNotice.id !== enrollment.data?.enrollment?.latest_event_id)
    || (!!live.data && live.data.latest_event_id !== (enrollment.data?.enrollment?.latest_event_id ?? null));
  const effectiveData = useMemo(() => enrollment.data && live.data && enrollment.data.window
    ? { ...enrollment.data, window: { ...enrollment.data.window, status: live.data.window_status,
        opens_at: live.data.opens_at, closes_at: live.data.closes_at, member_self_service: live.data.member_self_service } }
    : enrollment.data, [enrollment.data, live.data]);

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
  if (
    enrollment.isLoading ||
    dependants.isPending ||
    (!!enrollment.data?.window && formContext.isPending)
  ) {
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
  // Signing is the only way to send while a period is open, so a form that
  // failed to load is an error to retry, not a reason to fall back to an
  // unsigned send.
  if (enrollment.data?.window && formContext.isError) {
    return <PortalErrorState onRetry={() => void formContext.refetch()} />;
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
      <EnrollmentStatusNotice enrollmentId={enrollment.data?.enrollment?.id} />
      {live.isError && <div role="alert" className="rounded-control border border-hairline bg-glass p-4 text-row text-record">
        <p>We couldn't check whether this period still accepts changes. Your entries remain on this page. Retry before saving or signing.</p>
        <button className="leaf-focus min-h-11 text-action-ink" onClick={() => void live.refetch()}>Check enrolment status</button>
      </div>}
      {changed && <div role="status" className="rounded-control border border-hairline bg-glass p-4 text-row text-record">
        <p>Your enrolment changed while this page was open. Refresh to load the latest saved choices before continuing.</p>
        <button className="leaf-focus min-h-11 font-semibold text-action-ink" onClick={() => void qc.invalidateQueries({ queryKey: ["portal"] })}>Refresh enrolment</button>
      </div>}
      <MemberEnrollmentPanel
        readOnly={changed || live.isError}
        data={effectiveData ?? { window: null, enrollment: null, options: null }}
        dependants={dependantRefs}
        // Which step is open lives in the URL, so it survives a refresh and the
        // back button — the same contract the coverage deck has. `replace`
        // because stepping through nine products is filling in a form, not
        // navigating: without it Back walks back through every product visited
        // instead of leaving the page.
        slideKey={search.p ?? null}
        onSlideKeyChange={(p) =>
          navigate({
            to: "/portal/$company/enrollment",
            params: { company },
            search: { p },
            replace: true,
          })
        }
        onSaveElections={(elections) => saveElections.mutateAsync(elections)}
        onSaveLeave={(input) => setLeave.mutateAsync(input)}
        onSaveDraft={async (input) => {
          try { return await saveDraft.mutateAsync({ ...input, expected_event_id: enrollment.data?.enrollment?.latest_event_id ?? null }); }
          catch (error) { void live.refetch(); throw error; }
        }}
        onSubmit={(input) => submit.mutateAsync(input)}
        saving={saveElections.isPending || saveDraft.isPending}
        savingLeave={setLeave.isPending}
        submitting={submit.isPending}
        form={
          formContext.data
            ? {
                context: formContext.data,
                onSign: async (input) => {
                  try { return await sign.mutateAsync({ ...input, expected_event_id: enrollment.data?.enrollment?.latest_event_id ?? null }); }
                  catch (error) { void live.refetch(); throw error; }
                },
                signing: sign.isPending,
              }
            : undefined
        }
      />
      <EnrollmentUpdates />
      <MyFormsMount />
    </div>
  );
}
