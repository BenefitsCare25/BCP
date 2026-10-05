/** A period members are inside (or about to be): where it stands, what needs
 * the broker, and the way out (extend or close).
 *
 * Structure follows the question a broker arrives with — "is anything waiting
 * on me?" — so the attention queue sits directly under the progress, and a
 * passed deadline is the first thing on the panel, not a date to be noticed. */
import { useState, type ReactNode } from "react";
import { Link } from "@tanstack/react-router";
import {
  AlertTriangle,
  ArrowRight,
  CheckCheck,
  Loader2,
  Lock,
  Pencil,
  UserPlus,
} from "lucide-react";
import { toast } from "sonner";
import {
  type EnrollmentStatus,
  type EnrollmentWindow,
  type WindowProgress,
  useConfirmSubmitted,
  useOpenWindow,
  useWindowProgress,
} from "@/api/enrollment";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { formatError } from "@/lib/errors";
import { cn } from "@/lib/cn";
import { ClosePeriodDialog } from "./ClosePeriodDialog";
import { PeriodForm } from "./PeriodForm";
import { PeriodSummary } from "./PeriodSummary";
import { ReadinessReview } from "./ReadinessReview";
import {
  PHASE_META,
  STATUS_META,
  STATUS_ORDER,
  deadlineSentence,
  phaseOf,
  useNow,
} from "./periodMeta";

export function LivePeriodPanel({
  window: w,
  policyYearId,
  readOnly,
}: {
  window: EnrollmentWindow;
  policyYearId: string;
  readOnly: boolean;
}) {
  const now = useNow();
  const progress = useWindowProgress(w.id);
  const [editing, setEditing] = useState(false);
  const [closing, setClosing] = useState(false);
  const phase = phaseOf(w, now);
  const overdue = phase === "overdue";

  return (
    <section className="rounded-xl border border-border bg-card">
      <header className="flex flex-wrap items-start justify-between gap-3 border-b border-border px-5 py-4">
        <div className="min-w-0">
          <div className="flex items-center gap-2">
            <h2 className="truncate text-lg font-semibold text-foreground">{w.name}</h2>
            <Badge variant={PHASE_META[phase].badge}>{PHASE_META[phase].label}</Badge>
            {!w.member_self_service && <Badge variant="outline">Broker-managed</Badge>}
          </div>
          <p className="mt-0.5 text-sm text-muted-foreground">{deadlineSentence(w, now)}</p>
        </div>
        {!readOnly && !editing && (
          <div className="flex items-center gap-2">
            <Button variant="outline" size="sm" onClick={() => setEditing(true)}>
              <Pencil className="size-3.5" aria-hidden />
              {overdue ? "Extend deadline" : "Edit"}
            </Button>
            <Button size="sm" onClick={() => setClosing(true)}>
              <Lock className="size-3.5" aria-hidden /> Close period
            </Button>
          </div>
        )}
      </header>

      <div className="space-y-6 px-5 py-5">
        {overdue && !editing && <OverdueNotice />}
        {editing ? (
          <PeriodForm
            mode="open"
            window={w}
            policyYearId={policyYearId}
            onDone={() => setEditing(false)}
            onCancel={() => setEditing(false)}
          />
        ) : (
          <>
            <Progress progress={progress.data} loading={progress.isLoading} />
            <ReadinessReview windowId={w.id} />
            {progress.data && (
              <Queue window={w} p={progress.data} readOnly={readOnly} overdue={overdue} />
            )}
            <div className="border-t border-border pt-5">
              <h3 className="mb-3 text-sm font-semibold text-foreground">How this period is set up</h3>
              <PeriodSummary window={w} />
            </div>
          </>
        )}
      </div>

      {closing && (
        <ClosePeriodDialog window={w} onOpenChange={(o) => !o && setClosing(false)} />
      )}
    </section>
  );
}

function OverdueNotice() {
  return (
    <div
      role="status"
      className="flex flex-wrap items-start gap-3 rounded-lg bg-warn-soft/60 px-4 py-3"
    >
      <AlertTriangle className="mt-0.5 size-4 shrink-0 text-warn" aria-hidden />
      <div className="min-w-0 flex-1 text-sm text-foreground">
        <p className="font-medium">The deadline has passed and this period is still open.</p>
        <p className="text-muted-foreground">
          Members can no longer change anything, and pricing and leave rules stay
          locked until it closes. Confirm what&apos;s submitted, then close it — or
          extend the deadline to let members back in.
        </p>
      </div>
    </div>
  );
}

function Progress({
  progress: p,
  loading,
}: {
  progress: WindowProgress | undefined;
  loading: boolean;
}) {
  if (loading || !p) {
    return <div className="h-16 animate-pulse rounded-lg bg-muted" aria-hidden />;
  }
  if (p.total === 0) {
    return (
      <p className="text-sm text-muted-foreground">
        Nobody is in this period yet.
        {p.not_in_period > 0 && " Add the current staff below."}
      </p>
    );
  }
  const done = p.confirmed + p.deemed;
  const segments = STATUS_ORDER.filter((s) => p[s] > 0);
  return (
    <div className="space-y-3">
      <p className="text-sm text-foreground">
        <span className="text-2xl font-semibold tabular-nums">{done.toLocaleString()}</span>
        <span className="text-muted-foreground">
          {" "}
          of {p.total.toLocaleString()} members finalised
        </span>
      </p>
      <div
        className="flex h-2.5 w-full overflow-hidden rounded-full bg-muted"
        role="img"
        aria-label={segments
          .map((s) => `${p[s]} ${STATUS_META[s].label.toLowerCase()}`)
          .join(", ")}
      >
        {segments.map((s) => (
          <div
            key={s}
            className={cn("h-full", STATUS_META[s].fill)}
            style={{ width: `${(p[s] / p.total) * 100}%` }}
          />
        ))}
      </div>
      <ul className="flex flex-wrap gap-x-5 gap-y-1.5">
        {segments.map((s) => (
          <li key={s}>
            <StatusLink status={s} count={p[s]} />
          </li>
        ))}
      </ul>
    </div>
  );
}

function StatusLink({ status, count }: { status: EnrollmentStatus; count: number }) {
  const meta = STATUS_META[status];
  return (
    <Link
      to="/client-relations/enrollment"
      search={{ tab: "members", status }}
      className="group inline-flex items-center gap-1.5 rounded text-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring/40"
      title={meta.gloss}
    >
      <span className={cn("size-2 rounded-full", meta.fill)} aria-hidden />
      <span className="font-medium tabular-nums text-foreground">{count.toLocaleString()}</span>
      <span className="text-muted-foreground group-hover:text-foreground group-hover:underline">
        {meta.label.toLowerCase()}
      </span>
    </Link>
  );
}

function Queue({
  window: w,
  p,
  readOnly,
  overdue,
}: {
  window: EnrollmentWindow;
  p: WindowProgress;
  readOnly: boolean;
  overdue: boolean;
}) {
  const items: ReactNode[] = [];
  if (p.submitted > 0) {
    items.push(
      <QueueItem
        key="submitted"
        text={`${p.submitted.toLocaleString()} submitted ${p.submitted === 1 ? "selection is" : "selections are"} waiting for your confirmation.`}
        action={!readOnly && <ConfirmAllButton windowId={w.id} />}
        review="submitted"
      />,
    );
  }
  if (p.in_progress > 0) {
    items.push(
      <QueueItem
        key="saved"
        text={`${p.in_progress.toLocaleString()} ${p.in_progress === 1 ? "member has" : "members have"} saved changes but never sent them. Unsent changes are discarded at close unless you submit them.`}
        review="in_progress"
      />,
    );
  }
  if (p.not_in_period > 0) {
    items.push(
      <QueueItem
        key="new"
        text={
          `${p.not_in_period.toLocaleString()} active staff ${p.not_in_period === 1 ? "isn't" : "aren't"} in this period yet (added to the roster after it opened).` +
          (overdue ? " Extend the deadline first, so they can actually choose." : "")
        }
        action={!readOnly && <AddStaffButton window={w} disabled={overdue} />}
      />,
    );
  }
  if (!items.length) {
    return <p className="text-sm text-muted-foreground">Nothing is waiting on you right now.</p>;
  }
  return (
    <div>
      <h3 className="mb-2 text-sm font-semibold text-foreground">Needs you</h3>
      <ul className="divide-y divide-border rounded-lg border border-border">{items}</ul>
    </div>
  );
}

function ConfirmAllButton({ windowId }: { windowId: string }) {
  const confirmAll = useConfirmSubmitted();
  return (
    <Button
      size="sm"
      disabled={confirmAll.isPending}
      onClick={() =>
        confirmAll.mutate(windowId, {
          onSuccess: (r) => {
            if (!r.failed.length) {
              toast.success(`${r.confirmed.toLocaleString()} selections confirmed.`);
              return;
            }
            const names = r.failed.slice(0, 3).map((f) => f.employee_name ?? f.staff_id);
            toast.warning(
              `${r.confirmed.toLocaleString()} confirmed. ${r.failed.length} need review — ${names.join(", ")}${r.failed.length > 3 ? "…" : ""}`,
            );
          },
          onError: (e) => toast.error(formatError(e)),
        })
      }
    >
      {confirmAll.isPending ? (
        <Loader2 className="size-3.5 animate-spin" aria-hidden />
      ) : (
        <CheckCheck className="size-3.5" aria-hidden />
      )}
      Confirm all
    </Button>
  );
}

function AddStaffButton({ window: w, disabled }: { window: EnrollmentWindow; disabled: boolean }) {
  const sync = useOpenWindow();
  return (
    <Button
      size="sm"
      variant="outline"
      disabled={disabled || sync.isPending}
      title={disabled ? "The deadline has passed — extend it first" : undefined}
      onClick={() =>
        sync.mutate(w.id, {
          onSuccess: (r) =>
            toast.success(
              `${r.enrollments_created.toLocaleString()} staff added to ${w.name} with their current plans.`,
            ),
          onError: (e) => toast.error(formatError(e)),
        })
      }
    >
      {sync.isPending ? (
        <Loader2 className="size-3.5 animate-spin" aria-hidden />
      ) : (
        <UserPlus className="size-3.5" aria-hidden />
      )}
      Add them
    </Button>
  );
}

function QueueItem({
  text,
  action,
  review,
}: {
  text: string;
  action?: ReactNode;
  review?: EnrollmentStatus;
}) {
  return (
    <li className="flex flex-wrap items-center gap-3 px-4 py-3">
      <p className="min-w-0 flex-1 text-sm text-foreground">{text}</p>
      <div className="flex items-center gap-2">
        {action}
        {review && (
          <Button asChild size="sm" variant="ghost">
            <Link to="/client-relations/enrollment" search={{ tab: "members", status: review }}>
              Review <ArrowRight className="size-3.5" aria-hidden />
            </Link>
          </Button>
        )}
      </div>
    </li>
  );
}
