/** What has been done to this member's cover, and how to undo it.
 *
 * Moved here from the Member Listing row sheet. An override is a COVERAGE
 * decision — it changes the plan, the price tag and the schedule the member
 * reads — so it belongs beside the cover it changed, not on the roster page
 * whose job ends at matching. On the roster page it was also invisible from the
 * one screen a broker opens to answer "why is this person on Plan 3?".
 *
 * Collapsed by default: on a roster with no enrolment period run yet, every
 * member has an empty timeline, and an expanded card that says "No changes
 * recorded yet" above two disabled-looking buttons is the shape this pane was
 * cleared of. The summary line states the count, so opening it is a choice.
 */
import { useState } from "react";
import { ChevronRight, History } from "lucide-react";
import { useCoverageHistory } from "@/api/enrollment";
import { CoverageHistory } from "@/components/enrollment/CoverageHistory";
import { CoverageRevertControls } from "@/components/enrollment/CoverageRevertControls";
import { cn } from "@/lib/cn";

export function CoverageChanges({ employeeId }: { employeeId: string }) {
  const [open, setOpen] = useState(false);
  const { data, isLoading, isError } = useCoverageHistory(employeeId);
  // "none recorded" is an ASSERTION, so it may only be printed once the answer
  // is known. Defaulting the count to 0 told a broker auditing "has anyone
  // touched this member's cover?" that nobody had, while the request was still
  // in flight or had failed — and the contradicting message sat inside a
  // collapsed panel they had no reason to open.
  const summary = isLoading
    ? "loading…"
    : isError
      ? "couldn't load"
      : (data?.entries.length ?? 0) === 0
        ? "none recorded"
        : `${data!.entries.length} ${data!.entries.length === 1 ? "change" : "changes"}`;

  return (
    <section className="rounded-lg border border-border bg-card">
      <button
        type="button"
        onClick={() => setOpen((o) => !o)}
        aria-expanded={open}
        aria-controls={`coverage-changes-${employeeId}`}
        className="flex w-full items-center gap-2 rounded-lg px-4 py-3 text-left hover:bg-muted/40"
      >
        <ChevronRight
          aria-hidden
          className={cn(
            "size-3.5 shrink-0 text-muted-foreground transition-transform duration-150",
            open && "rotate-90",
          )}
        />
        <History className="size-4 shrink-0 text-muted-foreground" aria-hidden />
        <h3 className="text-sm font-semibold text-foreground">
          Coverage changes
        </h3>
        <span className="ml-auto shrink-0 text-xs tabular-nums text-muted-foreground">
          {summary}
        </span>
      </button>
      {open && (
        <div
          id={`coverage-changes-${employeeId}`}
          className="flex flex-col gap-3 border-t border-border px-4 py-3.5"
        >
          {/* One button here, deliberately. This card names no enrolment period,
              so "revert to baseline" had nothing on screen to mean — and on real
              data the baseline predates the latest slip re-upload, so restoring
              it would pin the member to a superseded plan. The per-period revert
              lives on the elections panel, where the period IS the context. */}
          <CoverageRevertControls employeeId={employeeId} />
          <div className="border-t border-border pt-3">
            <CoverageHistory employeeId={employeeId} />
          </div>
        </div>
      )}
    </section>
  );
}
