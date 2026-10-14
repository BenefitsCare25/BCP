import { ArrowRight } from "lucide-react";
import type { SetupAnswers } from "@/types";
import { fmtDay } from "@/lib/format";

/** Where a source issue can be fixed: a plan assignment, or the section. */
export interface SourceIssueLink {
  key: string;
  label: string;
  onGo: () => void;
}

export function SourceMappingNotes({
  answers,
  onReview,
  links = [],
}: {
  answers: SetupAnswers | null;
  onReview?: (reviewed: boolean) => void;
  links?: SourceIssueLink[];
}) {
  const schedules = answers?.source_rate_schedules ?? [];
  const issues = answers?.source_issues ?? [];
  if (!issues.length && schedules.length < 2) return null;
  return <details className="rounded-md border border-border p-3" open={issues.length > 0}>
    <summary className="cursor-pointer text-sm font-medium focus-visible:outline focus-visible:outline-2 focus-visible:outline-ring">
      Source rates and mapping review{issues.length ? ` (${issues.length})` : ""}
    </summary>
    {issues.length > 0 && <ul className="mt-3 list-disc space-y-1 pl-5 text-sm text-foreground">
      {issues.map(issue => <li key={issue}>{issue}</li>)}
    </ul>}
    {issues.length > 0 && links.length > 0 && <div className="mt-2 flex flex-wrap items-center gap-x-3 gap-y-1 text-sm">
      <span className="text-muted-foreground">Review in:</span>
      {links.map(link => <button key={link.key} type="button" onClick={link.onGo}
        className="inline-flex items-center gap-1 font-medium text-primary underline-offset-2 hover:underline focus-visible:outline focus-visible:outline-2 focus-visible:outline-ring">
        {link.label} <ArrowRight className="size-3.5" aria-hidden />
      </button>)}
    </div>}
    {schedules.length > 0 && <ul className="mt-3 space-y-2 text-sm">
      {schedules.map((schedule, i) => <li key={i} className="flex flex-wrap items-baseline gap-x-2">
        <span className="font-medium">{schedule.label}</span>
        <span className="text-muted-foreground">{schedule.selected ? "Applied to this setup" : "Retained for reference"}
          {schedule.start_date && schedule.end_date ? ` · ${fmtDay(schedule.start_date)} – ${fmtDay(schedule.end_date)}` : ""}
        </span>
      </li>)}
    </ul>}
    {issues.length > 0 && (onReview ? <label className="mt-3 flex items-start gap-2 text-sm">
      <input type="checkbox" className="mt-1 accent-primary" checked={answers?.source_reviewed === true}
        onChange={event => onReview(event.target.checked)} />
      <span>I have reviewed these source issues and corrected the setup where needed.</span>
    </label> : answers?.source_reviewed && <p className="mt-3 text-sm text-muted-foreground">Source issues reviewed.</p>)}
  </details>;
}
