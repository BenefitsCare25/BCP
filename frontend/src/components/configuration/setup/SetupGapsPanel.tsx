import { useId, useState } from "react";
import { ArrowRight, ChevronDown, CircleCheck, TriangleAlert } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/cn";
import { GAP_SECTION_ORDER, type GapSection, type SetupGap } from "./setupGaps";

/** One row of the checklist: a missing value and every place it is missing.
 *  The same blank rate on four categories reads as one line with four chips
 *  instead of four near-identical lines. */
interface GapRow {
  key: string;
  label: string;
  hint?: string;
  blocking: boolean;
  gaps: SetupGap[];
}

function rowsFor(gaps: SetupGap[]): GapRow[] {
  const rows = new Map<string, GapRow>();
  for (const gap of gaps) {
    const key = `${gap.label}::${gap.hint ?? ""}`;
    const row = rows.get(key) ?? {
      key,
      label: gap.label,
      hint: gap.hint,
      blocking: false,
      gaps: [],
    };
    row.blocking ||= Boolean(gap.blocking);
    row.gaps.push(gap);
    rows.set(key, row);
  }
  return [...rows.values()];
}

export function SetupGapsPanel({
  gaps,
  sectionLabels,
  sections,
  onGo,
}: {
  gaps: SetupGap[];
  sectionLabels: Record<string, string>;
  /** Sections this product's form renders; gaps elsewhere still list, unlinked. */
  sections: string[];
  onGo: (gap: SetupGap) => void;
}) {
  const [open, setOpen] = useState(true);
  const listId = useId();

  if (gaps.length === 0) {
    return (
      <p className="flex items-center gap-2 rounded-lg border border-border bg-card px-3 py-2 text-sm text-muted-foreground">
        <CircleCheck aria-hidden className="size-4 shrink-0 text-good" />
        Nothing missing. Every required setup value is filled in.
      </p>
    );
  }

  const blocking = gaps.filter((gap) => gap.blocking).length;
  const bySection = GAP_SECTION_ORDER.map((section) => ({
    section,
    gaps: gaps.filter((gap) => gap.section === section),
  })).filter((group) => group.gaps.length > 0);

  return (
    // A labelled region, not a <section>: the employee category cards below
    // are the setup's sections, and this list repeats their names.
    <div
      role="region"
      aria-label="Missing setup data"
      className="rounded-lg border border-border bg-card"
    >
      <div className="flex flex-wrap items-center gap-x-3 gap-y-2 px-3 py-2.5">
        <TriangleAlert aria-hidden className="size-4 shrink-0 text-warn" />
        <h3 className="text-sm font-semibold text-foreground">
          {gaps.length} {gaps.length === 1 ? "value" : "values"} missing
        </h3>
        {blocking > 0 && (
          <Badge variant="error">
            {blocking} must be filled to confirm
          </Badge>
        )}
        <Button
          size="sm"
          variant="ghost"
          className="ml-auto text-muted-foreground"
          aria-expanded={open}
          aria-controls={listId}
          onClick={() => setOpen((value) => !value)}
        >
          {open ? "Hide list" : "Show list"}
          <ChevronDown
            aria-hidden
            className={cn("size-3.5 transition-transform duration-150", open && "rotate-180")}
          />
        </Button>
      </div>
      {open && (
        <div id={listId} className="grid gap-3 px-3 pb-3">
          {bySection.map(({ section, gaps: sectionGaps }) => (
            <SectionList
              key={section}
              section={section}
              label={sectionLabels[section] ?? section}
              rows={rowsFor(sectionGaps)}
              linkable={sections.includes(section)}
              onGo={onGo}
            />
          ))}
        </div>
      )}
    </div>
  );
}

function SectionList({
  section,
  label,
  rows,
  linkable,
  onGo,
}: {
  section: GapSection;
  label: string;
  rows: GapRow[];
  linkable: boolean;
  onGo: (gap: SetupGap) => void;
}) {
  return (
    <div>
      <h4 className="mb-1 text-2xs font-medium uppercase tracking-wider text-muted-foreground">
        {label}
      </h4>
      <ul className="grid gap-0.5" data-section={section}>
        {rows.map((row) => {
          const scoped = row.gaps.filter((gap) => gap.where);
          return (
            <li
              key={row.key}
              className="grid grid-cols-[minmax(0,1fr)_auto] items-start gap-x-3 gap-y-1 rounded-md px-2 py-1.5 hover:bg-muted/40"
            >
              <div className="min-w-0">
                <div className="flex flex-wrap items-center gap-x-2 gap-y-1">
                  <span className="text-sm font-medium text-foreground">{row.label}</span>
                  {row.blocking && <Badge variant="error">Required to confirm</Badge>}
                </div>
                {row.hint && (
                  <p className="mt-0.5 text-xs text-muted-foreground">{row.hint}</p>
                )}
                {scoped.length > 0 && (
                  <div className="mt-1 flex flex-wrap gap-1.5">
                    {scoped.map((gap) => (
                      <button
                        key={gap.key}
                        type="button"
                        disabled={!linkable}
                        onClick={() => onGo(gap)}
                        aria-label={`${row.label}: open ${gap.where}`}
                        title={linkable ? `Open ${gap.where}` : undefined}
                        className="max-w-72 truncate rounded-md border border-border bg-card px-2 py-0.5 text-xs text-foreground transition-colors hover:bg-muted focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring/40 disabled:cursor-default disabled:hover:bg-card"
                      >
                        {gap.where}
                      </button>
                    ))}
                  </div>
                )}
              </div>
              {linkable && scoped.length === 0 && (
                <Button
                  size="sm"
                  variant="ghost"
                  className="h-7 text-muted-foreground"
                  aria-label={`Go to ${row.label}`}
                  onClick={() => onGo(row.gaps[0])}
                >
                  Go to <ArrowRight aria-hidden className="size-3.5" />
                </Button>
              )}
            </li>
          );
        })}
      </ul>
    </div>
  );
}
