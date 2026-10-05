import { SystemAdminOnly } from "@/components/auth/SystemAdminOnly";
/** A drafted period: its rules, whether it can open, and the Open action.
 *
 * Readiness is shown up front (it used to appear only after Open failed), and
 * Validation alerts are advisory and never disable the Open action. */
import { useState } from "react";
import { Loader2, Pencil, Play, Trash2 } from "lucide-react";
import { toast } from "sonner";
import {
  type EnrollmentWindow,
  useDeleteWindow,
  useOpenWindow,
} from "@/api/enrollment";
import { AlertDialog } from "@/components/ui/alert-dialog";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { formatError } from "@/lib/errors";
import { parseServerDate } from "@/lib/format";
import { PeriodForm } from "./PeriodForm";
import { PeriodSummary } from "./PeriodSummary";
import { ReadinessReview } from "./ReadinessReview";
import { PHASE_META, deadlineSentence } from "./periodMeta";

export function DraftPeriodPanel({
  window: w,
  policyYearId,
  readOnly,
}: {
  window: EnrollmentWindow;
  policyYearId: string;
  readOnly: boolean;
}) {
  const [editing, setEditing] = useState(false);
  return (
    <section className="rounded-xl border border-border bg-card">
      <header className="flex flex-wrap items-start justify-between gap-3 border-b border-border px-5 py-4">
        <div className="min-w-0">
          <div className="flex items-center gap-2">
            <h2 className="truncate text-lg font-semibold text-foreground">{w.name}</h2>
            <Badge variant={PHASE_META.draft.badge}>{PHASE_META.draft.label}</Badge>
          </div>
          <p className="mt-0.5 text-sm text-muted-foreground">{deadlineSentence(w)}</p>
        </div>
        {!readOnly && !editing && (
          <div className="flex items-center gap-2">
            <Button variant="outline" size="sm" onClick={() => setEditing(true)}>
              <Pencil className="size-3.5" aria-hidden /> Edit
            </Button>
            <DeleteDraft window={w} />
          </div>
        )}
      </header>
      <div className="space-y-6 px-5 py-5">
        {editing ? (
          <PeriodForm
            mode="draft"
            window={w}
            policyYearId={policyYearId}
            onDone={() => setEditing(false)}
            onCancel={() => setEditing(false)}
          />
        ) : (
          <>
            <PeriodSummary window={w} />
            <OpenSection window={w} readOnly={readOnly} />
          </>
        )}
      </div>
    </section>
  );
}

function OpenSection({ window: w, readOnly }: { window: EnrollmentWindow; readOnly: boolean }) {
  const open = useOpenWindow();
  const startsLater = parseServerDate(w.opens_at).getTime() > Date.now();

  function doOpen() {
    open.mutate(w.id, {
      onSuccess: (r) => {
        toast.success(
          `${w.name} is open — ${r.enrollments_created.toLocaleString()} members added with their current plans.`,
        );
      },
      onError: (e) => {
        toast.error(formatError(e));
      },
    });
  }

  return (
    <div className="space-y-4 border-t border-border pt-5">
      <ReadinessReview windowId={w.id} />
      {!readOnly && (
        <div className="flex flex-wrap items-center gap-3">
          <Button
            disabled={open.isPending}
            onClick={doOpen}
          >
            {open.isPending ? (
              <Loader2 className="size-4 animate-spin" aria-hidden />
            ) : (
              <Play className="size-4" aria-hidden />
            )}
            Open period
          </Button>
          <span className="text-xs text-muted-foreground">
            Opening adds every active member with their current plans
            {startsLater ? "; members can start at the start time" : ""}. It can&apos;t
            be undone.
          </span>
        </div>
      )}
    </div>
  );
}

function DeleteDraft({ window: w }: { window: EnrollmentWindow }) {
  const remove = useDeleteWindow();
  const [confirming, setConfirming] = useState(false);
  return (
    <>
      <SystemAdminOnly><Button
        variant="ghost"
        size="sm"
        aria-label={`Delete ${w.name}`}
        onClick={() => setConfirming(true)}
      >
        <Trash2 className="size-3.5" aria-hidden />
      </Button></SystemAdminOnly>
      <SystemAdminOnly><AlertDialog
        open={confirming}
        onOpenChange={setConfirming}
        title={`Delete ${w.name}?`}
        description="The draft is removed. Nothing has happened to any member yet."
        loading={remove.isPending}
        onConfirm={() =>
          remove.mutate(w.id, {
            onSuccess: () => {
              setConfirming(false);
              toast.success("Draft period deleted.");
            },
            onError: (e) => toast.error(formatError(e)),
          })
        }
      /></SystemAdminOnly>
    </>
  );
}
