/** Close a period — the one irreversible step, so it states exactly what will
 * happen to each group of members before the broker commits.
 *
 * The figures come from the server's close preview, which runs close's own
 * checks: who would block the close, and whose saved-but-unsent choices can be
 * submitted for them. Saved choices are NOT submitted unless the broker ticks
 * it — a member who never pressed Submit didn't consent, but the broker is
 * told how many that is and can decide, instead of the choices vanishing. */
import { useState } from "react";
import { Link } from "@tanstack/react-router";
import { Loader2 } from "lucide-react";
import { toast } from "sonner";
import {
  type CloseMemberNote,
  type EnrollmentWindow,
  type WindowCloseSummary,
  type WindowClosePreview,
  useCloseWindow,
  useClosePreview,
} from "@/api/enrollment";
import { AlertDialog } from "@/components/ui/alert-dialog";
import { Checkbox } from "@/components/ui/checkbox";
import { ConflictDetailError, formatError } from "@/lib/errors";
import { DEFAULT_BEHAVIOR_TEXT } from "./periodMeta";

function closedMessage(name: string, s: WindowCloseSummary): string {
  const parts = [
    `${s.confirmed.toLocaleString()} confirmed`,
    s.submitted_at_close ? `(${s.submitted_at_close.toLocaleString()} submitted for members)` : "",
    s.deemed_kept ? `${s.deemed_kept.toLocaleString()} kept their plans` : "",
    s.deemed_declined ? `${s.deemed_declined.toLocaleString()} declined` : "",
  ].filter(Boolean);
  return `${name} closed — ${parts.join(", ")}.`;
}

export function ClosePeriodDialog({
  window,
  onOpenChange,
}: {
  window: EnrollmentWindow | null;
  onOpenChange: (open: boolean) => void;
}) {
  const preview = useClosePreview(window?.id);
  const close = useCloseWindow();
  const [submitSaved, setSubmitSaved] = useState(false);
  const p = preview.data;
  const blocked = (p?.invalid_submitted_count ?? 0) > 0;

  function confirm() {
    if (!window) return;
    close.mutate(
      { id: window.id, submitSaved },
      {
        onSuccess: (s) => {
          toast.success(closedMessage(window.name, s));
          setSubmitSaved(false);
          onOpenChange(false);
        },
        onError: (e) => {
          if (e instanceof ConflictDetailError && e.detail.code === "invalid_submissions") {
            // Something changed since the preview loaded — show the new truth.
            void preview.refetch();
            toast.error("Some submissions no longer pass their checks. Review them below.");
            return;
          }
          toast.error(formatError(e));
        },
      },
    );
  }

  return (
    <AlertDialog
      open={window !== null}
      onOpenChange={(open) => {
        if (!open) setSubmitSaved(false);
        onOpenChange(open);
      }}
      tone="info"
      size="lg"
      title={`Close ${window?.name ?? "this period"}?`}
      confirmLabel="Close period"
      confirmVariant="default"
      loading={close.isPending}
      confirmDisabled={!p || blocked || preview.isFetching}
      onConfirm={confirm}
      description={
        p ? (
          <ClosePreviewBody p={p} submitSaved={submitSaved} onSubmitSaved={setSubmitSaved} />
        ) : (
          <span className="flex items-center gap-2">
            <Loader2 className="size-4 animate-spin" aria-hidden /> Working out what
            closing will do…
          </span>
        )
      }
    />
  );
}

function ClosePreviewBody({
  p,
  submitSaved,
  onSubmitSaved,
}: {
  p: WindowClosePreview;
  submitSaved: boolean;
  onSubmitSaved: (v: boolean) => void;
}) {
  const fallback = DEFAULT_BEHAVIOR_TEXT[p.default_behavior];
  const blocked = p.invalid_submitted_count > 0;
  return (
    <div className="space-y-3 text-sm">
      <dl className="divide-y divide-border rounded-md border border-border">
        <Row n={p.submitted} label="submitted" outcome="become live coverage" />
        {!!p.returned && <Row n={p.returned} label="returned for correction" outcome="must be resolved before closing" />}
        {p.saved_not_sent > 0 && (
          <Row
            n={p.saved_not_sent}
            label="saved but never sent"
            outcome={
              submitSaved
                ? `are submitted for them${p.saved_blocked_count ? `, except ${p.saved_blocked_count}` : ""}`
                : `lose their unsent changes and ${fallback}`
            }
            warn={!submitSaved}
          />
        )}
        <Row n={p.not_started} label="did nothing" outcome={fallback} />
        {/* Their own row: "decline all" is honoured whatever the default is. */}
        {p.declined > 0 && (
          <Row n={p.declined} label="chose to decline all" outcome="are declined from voluntary cover" />
        )}
        {p.confirmed > 0 && (
          <Row n={p.confirmed} label="already confirmed" outcome="stay as they are" />
        )}
      </dl>

      {blocked && (
        <MemberNotes
          tone="error"
          title={`${p.invalid_submitted_count} enrolment ${p.invalid_submitted_count === 1 ? "needs" : "need"} attention before closing:`}
          notes={p.invalid_submitted}
          extra={p.invalid_submitted_count - p.invalid_submitted.length}
        />
      )}

      {p.saved_not_sent > 0 && !blocked && (
        <SubmitSavedOption p={p} checked={submitSaved} onChange={onSubmitSaved} />
      )}

      <p className="text-muted-foreground">
        Closing can&apos;t be undone. Individual members can still be corrected
        afterwards from their coverage history.
      </p>
    </div>
  );
}

function SubmitSavedOption({
  p,
  checked,
  onChange,
}: {
  p: WindowClosePreview;
  checked: boolean;
  onChange: (v: boolean) => void;
}) {
  const fallback = DEFAULT_BEHAVIOR_TEXT[p.default_behavior];
  return (
    <div className="space-y-2 rounded-md bg-warn-soft/40 p-3">
      <label className="flex items-start gap-2 text-foreground">
        <Checkbox
          checked={checked}
          onCheckedChange={(v) => onChange(v === true)}
          className="mt-0.5"
          disabled={p.saved_submittable === 0}
        />
        <span>
          Submit the saved choices of {p.saved_submittable.toLocaleString()}{" "}
          {p.saved_submittable === 1 ? "member" : "members"} on their behalf
          <span className="block text-xs text-muted-foreground">
            Otherwise their saved plan and leave changes are discarded and they {fallback}.
          </span>
        </span>
      </label>
      {p.saved_blocked_count > 0 && (
        <MemberNotes
          tone="warn"
          title={`${p.saved_blocked_count} can't be submitted — their changes are discarded and they ${fallback}:`}
          notes={p.saved_blocked}
          extra={p.saved_blocked_count - p.saved_blocked.length}
        />
      )}
    </div>
  );
}

function Row({
  n,
  label,
  outcome,
  warn = false,
}: {
  n: number;
  label: string;
  outcome: string;
  warn?: boolean;
}) {
  return (
    <div className="flex items-baseline gap-3 px-3 py-2">
      <dt className="w-14 shrink-0 text-right font-semibold tabular-nums text-foreground">
        {n.toLocaleString()}
      </dt>
      <dd className={warn ? "text-warn" : "text-foreground"}>
        {label} <span className="text-muted-foreground">→</span> {outcome}
      </dd>
    </div>
  );
}

function MemberNotes({
  tone,
  title,
  notes,
  extra,
}: {
  tone: "error" | "warn";
  title: string;
  notes: CloseMemberNote[];
  extra: number;
}) {
  return (
    <div>
      <p className={tone === "error" ? "font-medium text-error" : "font-medium text-warn"}>
        {title}
      </p>
      <ul className="mt-1 max-h-40 space-y-1 overflow-y-auto text-xs">
        {notes.map((n) => (
          <li key={n.enrollment_id} className="text-foreground">
            <Link
              to="/client-relations/enrollment"
              search={{ tab: "members", member: n.enrollment_id }}
              className="font-medium underline underline-offset-2"
            >
              {n.employee_name ?? n.staff_id ?? "Member"}
            </Link>
            {n.staff_id && <span className="text-muted-foreground"> ({n.staff_id})</span>}
            <span className="text-muted-foreground"> — {n.reason}</span>
          </li>
        ))}
        {extra > 0 && <li className="text-muted-foreground">…and {extra} more</li>}
      </ul>
    </div>
  );
}
