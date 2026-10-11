/** One member's selection in a period, edited by the broker on their behalf.
 *
 * Data and edits live in `useMemberElection`, the broker's actions in
 * `useElectionActions`, and the one-primary-action bar in `ElectionActionBar`;
 * this component only lays them out:
 * - not started / saved → **Submit & confirm**, with Save as draft beside it;
 * - submitted → **Confirm** (also after the deadline — confirming is review);
 * - confirmed → Reopen, while the period still takes changes. */
import type { ReactNode } from "react";
import { Loader2 } from "lucide-react";
import type { EnrollmentWindow } from "@/api/enrollment";
import { CoverageHistory } from "@/components/enrollment/CoverageHistory";
import { CoverageRevertControls } from "@/components/enrollment/CoverageRevertControls";
import {
  ElectionProductCard,
  FlexBalanceStrip,
  LeaveTradingCard,
} from "@/components/enrollment/electionShared";
import { STATUS_META, phaseOf, useNow } from "@/components/enrollment/period/periodMeta";
import { AlertDialog } from "@/components/ui/alert-dialog";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/cn";
import { formatError } from "@/lib/errors";
import { ElectionActionBar } from "./ElectionActionBar";
import { EnrollmentActivity } from "./EnrollmentActivity";
import { type ElectionActions, useElectionActions } from "./useElectionActions";
import { type MemberElection, useMemberElection } from "./useMemberElection";

export function MemberElectionPanel({
  enrollmentId,
  window,
  readOnly,
  onClear,
}: {
  enrollmentId: string;
  window: EnrollmentWindow;
  readOnly: boolean;
  /** Drop the selected member (e.g. a link left over from another benefit year). */
  onClear: () => void;
}) {
  const m = useMemberElection(enrollmentId, window);
  const a = useElectionActions(enrollmentId, m);
  const phase = phaseOf(window, useNow());

  if (m.isError) {
    return (
      <SelectionNotice onClear={onClear} onRetry={() => void m.refetch()}>
        Could not load this member's selection. {formatError(m.error)}
      </SelectionNotice>
    );
  }
  if (m.isLoading || !m.enr) {
    return (
      <div className="flex items-center gap-2 px-2 py-10 text-sm text-muted-foreground">
        <Loader2 className="size-4 animate-spin" aria-hidden /> Loading…
      </div>
    );
  }
  // Editing against another period's flags (leave, dependants, deadline) would
  // save under rules this member's enrolment is not governed by.
  if (m.enr.window_id !== window.id) {
    return (
      <SelectionNotice onClear={onClear}>
        This member's selection belongs to a different enrolment period. Pick them
        from this period's list instead.
      </SelectionNotice>
    );
  }
  const { enr, status } = m;
  const finalized = status === "confirmed" || status === "deemed";
  const editable = !readOnly && !finalized && phase === "open";
  const meta = STATUS_META[status] ?? STATUS_META.not_started;

  return (
    <section className="rounded-xl border border-border bg-card">
      <header className="flex flex-wrap items-start justify-between gap-3 border-b border-border px-5 py-4">
        <div className="min-w-0">
          <h2 className="truncate text-base font-semibold text-foreground">
            {enr.employee_name ?? enr.staff_id}
          </h2>
          <p className="font-mono text-xs text-muted-foreground">{enr.staff_id}</p>
        </div>
        <span className={cn("inline-flex items-center gap-1.5 text-sm font-medium", meta.text)}>
          <span className={cn("size-2 rounded-full", meta.fill)} aria-hidden />
          {meta.label}
        </span>
      </header>

      <div className="space-y-4 px-5 py-5">
        <ElectionChoices m={m} a={a} window={window} editable={editable && status !== "submitted" && !a.busy} />
        {!readOnly && (
          <ElectionActionBar
            status={status}
            phase={phase}
            opensAt={window.opens_at}
            editable={editable}
            overdrawn={m.overdrawn}
            selfService={window.member_self_service}
            portalSigned={!!enr.portal_signed}
            actions={a}
          />
        )}
        <div className="space-y-3 border-t border-border pt-4">
          <EnrollmentActivity id={enrollmentId} readOnly={readOnly} />
          {finalized && m.empId && !readOnly && (
            <CoverageRevertControls
              employeeId={m.empId}
              offerBaseline={!!enr.baseline_snapshot?.products}
              windowId={enr.window_id}
            />
          )}
          <CoverageHistory employeeId={m.empId} limit={4} />
        </div>
      </div>

      <UnpricedDialog a={a} />
    </section>
  );
}

function ElectionChoices({
  m,
  a,
  window,
  editable,
}: {
  m: MemberElection;
  a: ElectionActions;
  window: EnrollmentWindow;
  editable: boolean;
}) {
  return (
    <>
      {m.flex && (
        <FlexBalanceStrip
          flex={m.flex}
          allowOverdraft={window.allow_overdraft}
          shortfallHint="These choices cost more than the flex wallet holds. Reduce them, or allow overdraft on the period."
        />
      )}
      <div className="space-y-2">
        {m.tierSets.map((ts) => {
          const ps = m.state[ts.product_code];
          if (!ps) return null;
          return (
            <ElectionProductCard
              key={ts.product_code}
              ts={ts}
              ps={ps}
              disabled={!editable}
              allowDeps={window.allow_dependant_changes}
              dependants={m.dependants}
              flexOnChange={!!m.flex?.onChange}
              planChanges={window.allow_plan_change}
              onChange={(next) => m.setProduct(ts.product_code, next)}
            />
          );
        })}
        {!m.tierSets.length && (
          <p className="text-sm text-muted-foreground">
            This member has nothing to choose in this period.
          </p>
        )}
      </div>
      {window.allow_leave && (
        <LeaveTradingCard
          action={m.leaveAction}
          days={m.leaveDays}
          leave={m.options?.leave ?? null}
          ratePerDay={m.options?.member_leave_rate ?? null}
          disabled={!editable}
          saving={a.pending.leave}
          onActionChange={m.setLeaveAction}
          onDaysChange={m.setLeaveDays}
          onSave={a.saveLeave}
        />
      )}
    </>
  );
}

function SelectionNotice({
  onRetry,
  onClear,
  children,
}: {
  onRetry?: () => void;
  onClear: () => void;
  children: ReactNode;
}) {
  return (
    <section role="alert" className="space-y-3 rounded-xl border border-border bg-card px-5 py-6 text-sm">
      <p className="text-foreground">{children}</p>
      <div className="flex flex-wrap gap-2">
        {onRetry && (
          <Button size="sm" variant="outline" onClick={onRetry}>
            Retry
          </Button>
        )}
        <Button size="sm" variant="ghost" onClick={onClear}>
          Clear selection
        </Button>
      </div>
    </section>
  );
}

function UnpricedDialog({ a }: { a: ElectionActions }) {
  return (
    <AlertDialog
      open={a.unpriced !== null}
      onOpenChange={(o) => !o && a.clearUnpriced()}
      tone="info"
      title="Some choices have no flex price"
      description={`${
        a.unpriced?.length ? `${a.unpriced.join(", ")} would draw $0 from the wallet. ` : ""
      }That is usually a pricing gap — a missing slip premium or price-book row, or an age-banded product without the member's date of birth. Fix the price on Pricing & rules, or continue if $0 is intended.`}
      confirmLabel="Submit & confirm anyway"
      confirmVariant="default"
      loading={a.pending.submit}
      onConfirm={() => a.submitAndConfirm(true)}
    />
  );
}
