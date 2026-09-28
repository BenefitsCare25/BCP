/** Enrollment → Overview. Leads with the ONE period that matters now (the live
 * one, else the newest draft); every other period is history below it.
 *
 * The old page led with a blank "New enrolment period" form even while a
 * period was running, with the running period's state reduced to a badge
 * underneath. Planning a new period is now a deliberate action, not the page. */
import { useState } from "react";
import { CalendarPlus, ChevronRight, Loader2 } from "lucide-react";
import { type EnrollmentWindow, useEnrollmentWindows } from "@/api/enrollment";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { useSession } from "@/stores/session";
import { cn } from "@/lib/cn";
import { DraftPeriodPanel } from "./DraftPeriodPanel";
import { LivePeriodPanel } from "./LivePeriodPanel";
import { PeriodForm } from "./PeriodForm";
import { PHASE_META, deadlineSentence, focusPeriod, phaseOf, useNow } from "./periodMeta";

export function PeriodOverview({ readOnly }: { readOnly: boolean }) {
  const policyYearId = useSession((s) => s.currentPolicyYearId) ?? undefined;
  const { data: windows, isLoading } = useEnrollmentWindows(policyYearId);
  // Re-render each minute: every phase badge here is derived from the clock.
  useNow();
  const [picked, setPicked] = useState<string | null>(null);
  const [planning, setPlanning] = useState(false);

  if (!policyYearId) {
    return (
      <p className="text-sm text-muted-foreground">
        Select a benefit year to manage its enrolment periods.
      </p>
    );
  }
  if (isLoading) {
    return (
      <p className="flex items-center gap-2 text-sm text-muted-foreground">
        <Loader2 className="size-4 animate-spin" aria-hidden /> Loading periods…
      </p>
    );
  }

  const list = windows ?? [];
  const focus = list.find((w) => w.id === picked) ?? focusPeriod(list);
  const others = list.filter((w) => w.id !== focus?.id);

  const planForm = (
    <section className="rounded-xl border border-border bg-card px-5 py-5">
      <h2 className="mb-4 text-lg font-semibold text-foreground">Plan an enrolment period</h2>
      <PeriodForm
        mode="create"
        policyYearId={policyYearId}
        onDone={(w) => {
          setPlanning(false);
          setPicked(w.id);
        }}
        onCancel={list.length ? () => setPlanning(false) : undefined}
      />
    </section>
  );

  return (
    <div className="space-y-6">
      {planning ? (
        planForm
      ) : !focus ? (
        <EmptyState readOnly={readOnly} onPlan={() => setPlanning(true)} />
      ) : focus.status === "draft" ? (
        <DraftPeriodPanel
          key={focus.id}
          window={focus}
          policyYearId={policyYearId}
          readOnly={readOnly}
        />
      ) : focus.status === "open" ? (
        <LivePeriodPanel
          key={focus.id}
          window={focus}
          policyYearId={policyYearId}
          readOnly={readOnly}
        />
      ) : (
        <ClosedPeriodPanel window={focus} />
      )}

      {list.length > 0 && (
        <section>
          <div className="mb-2 flex items-center justify-between gap-3">
            <h2 className="text-sm font-semibold text-foreground">All periods this year</h2>
            {!readOnly && !planning && (
              <Button size="sm" variant="outline" onClick={() => setPlanning(true)}>
                <CalendarPlus className="size-3.5" aria-hidden /> Plan a period
              </Button>
            )}
          </div>
          <ul className="divide-y divide-border rounded-xl border border-border bg-card">
            {[...(focus ? [focus] : []), ...others].map((w) => (
              <PeriodRow
                key={w.id}
                window={w}
                current={w.id === focus?.id && !planning}
                onPick={() => {
                  setPlanning(false);
                  setPicked(w.id);
                }}
              />
            ))}
          </ul>
        </section>
      )}
    </div>
  );
}

function PeriodRow({
  window: w,
  current,
  onPick,
}: {
  window: EnrollmentWindow;
  current: boolean;
  onPick: () => void;
}) {
  const phase = PHASE_META[phaseOf(w)];
  return (
    <li>
      <button
        type="button"
        onClick={onPick}
        aria-current={current ? "true" : undefined}
        className={cn(
          "flex w-full items-center gap-3 px-4 py-3 text-left transition-colors",
          "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-ring/40",
          current ? "bg-muted" : "hover:bg-muted/60",
        )}
      >
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-2">
            <span className="truncate text-sm font-medium text-foreground">{w.name}</span>
            <Badge variant={phase.badge}>{phase.label}</Badge>
          </div>
          <p className="text-xs text-muted-foreground">{deadlineSentence(w)}</p>
        </div>
        <ChevronRight className="size-4 shrink-0 text-muted-foreground" aria-hidden />
      </button>
    </li>
  );
}

function EmptyState({ readOnly, onPlan }: { readOnly: boolean; onPlan: () => void }) {
  return (
    <section className="rounded-xl border border-dashed border-border-strong bg-card px-6 py-10">
      <div className="mx-auto max-w-xl text-center">
        <h2 className="text-lg font-semibold text-foreground">No enrolment period yet</h2>
        <p className="mt-2 text-sm text-muted-foreground">
          An enrolment period is the window in which members review and change their
          benefits. When it opens, everyone starts on the plans they have today; at
          close, the choices become their coverage.
        </p>
        <ol className="mx-auto mt-5 max-w-md space-y-1.5 text-left text-sm text-foreground">
          <li>1. Plan the period: dates, what can change, what happens if members do nothing.</li>
          <li>2. Check prices and leave rules on Pricing &amp; rules.</li>
          <li>3. Open it once the readiness checks pass.</li>
        </ol>
        {!readOnly && (
          <Button className="mt-6" onClick={onPlan}>
            <CalendarPlus className="size-4" aria-hidden /> Plan a period
          </Button>
        )}
      </div>
    </section>
  );
}

function ClosedPeriodPanel({ window: w }: { window: EnrollmentWindow }) {
  return (
    <section className="rounded-xl border border-border bg-card px-5 py-4">
      <div className="flex items-center gap-2">
        <h2 className="truncate text-lg font-semibold text-foreground">{w.name}</h2>
        <Badge variant={PHASE_META.closed.badge}>{PHASE_META.closed.label}</Badge>
      </div>
      <p className="mt-0.5 text-sm text-muted-foreground">{deadlineSentence(w)}</p>
      <p className="mt-3 text-sm text-foreground">
        Every choice from this period is now coverage. To correct one member, use
        Revert on their record in Members or Member Coverage.
      </p>
    </section>
  );
}
