/**
 * The dual-coverage alert on the Dependants tab, and the review sheet behind it.
 *
 * The banner counts UNRESOLVED CASES only — a life genuinely listed twice, or an
 * employee also carried as somebody's spouse. Opportunities (married colleagues
 * whose child is listed under just one of them) live in their own section and
 * are never counted: they are the normal state of such a family, and on a real
 * roster they outnumber the duplicates enough to bury them.
 *
 * A decision is recorded per life, so the flag clears and the list shows only
 * what is still open. Nothing is blocked — deliberate dual cover is legitimate,
 * and `not_a_match` exists because name+DOB matching is occasionally wrong.
 *
 * **The sheet's whole job is to answer three questions a broker could not
 * previously answer from it**: who is doubled, which two employees they reach
 * this company through, and what pressing a button commits them to. So each
 * case reads as one sentence, the two sides are drawn as two named sides rather
 * than a list of staff ids, every button names a PERSON, and the header states
 * once — in plain words — that recording a decision changes nobody's cover. The
 * earlier version put that fact behind an ⓘ, labelled its buttons with bare
 * staff numbers, and offered no way to tell 4 open cases from 112.
 *
 * The sheet is split from the banner because the Dependants table's own
 * "Covered twice" column opens it focused on a single life; a sheet that owned
 * its own open state could not be opened from a table row.
 */
import { useMemo, useState } from "react";
import { AlertTriangle, Check, Loader2, RotateCcw, Users } from "lucide-react";
import { toast } from "sonner";
import {
  type DualCase,
  type DualOpportunity,
  type DualParty,
  useDualCoverage,
  useRecordDualDecision,
  useReopenDualDecision,
} from "@/api/dualCoverage";
import { formatError } from "@/lib/errors";
import { cn } from "@/lib/cn";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { SectionLabel } from "@/components/ui/section-label";
import { Segmented } from "@/components/ui/segmented";
import {
  Sheet,
  SheetBody,
  SheetContent,
  SheetHeader,
  SheetTitle,
} from "@/components/ui/sheet";

type Filter = "open" | "decided" | "all";

/** The one sentence that was missing: what a decision does, and what it doesn't. */
const WHAT_A_DECISION_DOES =
  "Recording who keeps them clears the flag and leaves a note for whoever looks next. It does not change anyone's cover.";

export function DualCoverageBanner({
  policyYearId,
  onReview,
}: {
  policyYearId: string;
  onReview: () => void;
}) {
  const { data, isLoading } = useDualCoverage(policyYearId);

  // Nothing to say until there is something to say — a banner that renders
  // "0 families" on every roster becomes background and stops being read.
  if (isLoading || !data) return null;
  if (data.total_cases === 0 && data.total_opportunities === 0) return null;

  const unresolved = data.unresolved_cases;

  return (
    <div
      className={cn(
        "flex flex-wrap items-center gap-3 rounded-lg border px-4 py-3",
        unresolved > 0 ? "border-warn/40 bg-warn-soft/30" : "border-border bg-card",
      )}
    >
      {unresolved > 0 ? (
        <AlertTriangle className="size-4 shrink-0 text-warn" aria-hidden />
      ) : (
        <Check className="size-4 shrink-0 text-good" aria-hidden />
      )}
      <div className="min-w-0 flex-1 text-sm">
        {unresolved > 0 ? (
          <>
            <span className="font-medium">
              {unresolved} {unresolved === 1 ? "person is" : "people are"} covered
              under two employees
            </span>
            <span className="text-muted-foreground">
              {" "}
              — each side is a separate premium, and a claim can be paid on both.
            </span>
          </>
        ) : (
          <>
            <span className="font-medium">No open dual coverage</span>
            {data.total_cases > 0 && (
              <span className="text-muted-foreground">
                {" "}
                — all {data.total_cases} decided.
              </span>
            )}
          </>
        )}
      </div>
      <Button size="sm" variant="outline" onClick={onReview}>
        Review
      </Button>
    </div>
  );
}

export function DualCoverageSheet({
  policyYearId,
  open,
  onOpenChange,
  focusKey,
  onClearFocus,
}: {
  policyYearId: string;
  open: boolean;
  onOpenChange: (open: boolean) => void;
  /** Show one life only — set when opened from a dependant row. */
  focusKey?: string | null;
  onClearFocus: () => void;
}) {
  const { data } = useDualCoverage(policyYearId);
  const [filter, setFilter] = useState<Filter>("open");

  const decidedCount = useMemo(
    () => (data?.cases ?? []).filter((c) => isSettled(c)).length,
    [data],
  );

  if (!data) return null;

  const focused = focusKey
    ? data.cases.filter((c) => c.subject_key === focusKey)
    : null;
  const shown =
    focused ??
    data.cases.filter((c) =>
      filter === "all" ? true : filter === "decided" ? isSettled(c) : !isSettled(c),
    );

  return (
    <Sheet
      open={open}
      onOpenChange={(next) => {
        if (!next) onClearFocus();
        onOpenChange(next);
      }}
    >
      <SheetContent className="w-full sm:max-w-2xl">
        <SheetHeader>
          <SheetTitle>Covered under two employees</SheetTitle>
        </SheetHeader>
        <SheetBody className="space-y-6">
          <div className="space-y-2 text-sm text-muted-foreground">
            <p>
              These people reach this company twice — listed as a dependant of two
              employees, or holding their own cover while also carried as
              someone's spouse. Each side is a separate premium line, and a claim
              can be paid on both.
            </p>
            <p className="text-foreground/80">{WHAT_A_DECISION_DOES}</p>
          </div>

          {focused ? (
            <div className="flex items-center justify-between gap-3 border-t border-border pt-4">
              <p className="text-sm text-muted-foreground">
                Showing one person.{" "}
                {focused.length === 0 &&
                  "This case is no longer open — it may have been decided already."}
              </p>
              <Button size="sm" variant="ghost" onClick={onClearFocus}>
                Show all {data.total_cases}
              </Button>
            </div>
          ) : (
            <div className="flex flex-wrap items-center justify-between gap-3 border-t border-border pt-4">
              <p className="text-sm text-muted-foreground tabular-nums">
                <span className="font-medium text-foreground">
                  {data.unresolved_cases}
                </span>{" "}
                to review · {decidedCount} decided
              </p>
              <Segmented
                value={filter}
                onChange={setFilter}
                options={[
                  { value: "open", label: "To review" },
                  { value: "decided", label: "Decided" },
                  { value: "all", label: "All" },
                ]}
              />
            </div>
          )}

          <section className="space-y-3">
            {shown.length === 0 ? (
              <p className="text-sm text-muted-foreground">
                {filter === "decided"
                  ? "No decisions recorded yet."
                  : "Nothing left to review."}
              </p>
            ) : (
              shown.map((c) => (
                <CaseCard key={c.subject_key} c={c} policyYearId={policyYearId} />
              ))
            )}
            {!focused && data.total_cases > data.cases.length && (
              <p className="text-2xs text-subtle">
                Showing the first {data.cases.length} of {data.total_cases}. Decide
                these and the rest follow.
              </p>
            )}
          </section>

          {!focused && data.opportunities.length > 0 && (
            <section className="space-y-3 border-t border-border pt-5">
              <SectionLabel>Both parents work here</SectionLabel>
              <p className="text-sm text-muted-foreground">
                Their child is listed under one parent only. Nothing is wrong and
                there is nothing to decide — it is here so the second cover is
                visible if the family asks for it.
              </p>
              {data.opportunities.map((o) => (
                <OpportunityRow key={`${o.subject_key}-${o.child_name}`} o={o} />
              ))}
              {data.total_opportunities > data.opportunities.length && (
                <p className="text-2xs text-subtle">
                  Showing {data.opportunities.length} of {data.total_opportunities}.
                </p>
              )}
            </section>
          )}
        </SheetBody>
      </SheetContent>
    </Sheet>
  );
}

function isSettled(c: DualCase): boolean {
  return !!c.decision && !c.decision.stale;
}

/** How this life reaches one employee, as a phrase a broker reads rather than a
 *  field they decode. A null `dependant_id` means the party IS the life. */
export function reachLabel(p: DualParty): string {
  if (p.dependant_id === null) return "Employee here";
  const rel = (p.relationship || "dependant").toLowerCase();
  return `Listed as ${rel} of`;
}

function SideBlock({ p }: { p: DualParty }) {
  return (
    <div className="min-w-0 space-y-1 p-3">
      <p className="text-2xs uppercase tracking-wide text-subtle">{reachLabel(p)}</p>
      <p className="truncate text-sm font-medium text-foreground">
        {p.employee_name ?? "Unknown employee"}
      </p>
      <p className="font-mono text-2xs text-muted-foreground">{p.staff_id || "—"}</p>
      {p.unlinked ? (
        <Badge variant="warn" title="This row is not linked to an active employee">
          Not linked
        </Badge>
      ) : p.covered_products.length > 0 ? (
        <p className="text-2xs text-muted-foreground">
          Covers {p.covered_products.join(", ")}
        </p>
      ) : (
        <p className="text-2xs text-subtle">No cover in force</p>
      )}
    </div>
  );
}

/** The plain-English verdict, replacing a line of flag codes. */
function verdict(c: DualCase): string {
  if (c.overlapping_products.length > 0) {
    return `Both sides cover ${c.overlapping_products.join(", ")} — that is the same benefit paid for twice.`;
  }
  if (c.parties.filter((p) => p.covered).length >= 2) {
    return "Covered on both sides, but no benefit is on both.";
  }
  return "Only one side is covering them at the moment.";
}

function CaseCard({ c, policyYearId }: { c: DualCase; policyYearId: string }) {
  const record = useRecordDualDecision(policyYearId);
  const reopen = useReopenDualDecision(policyYearId);
  const [note, setNote] = useState("");
  const [noteOpen, setNoteOpen] = useState(false);
  const settled = isSettled(c);

  const decide = async (
    decision: Parameters<typeof record.mutateAsync>[0]["decision"],
    carriedBy?: string | null,
  ) => {
    try {
      await record.mutateAsync({
        subject_key: c.subject_key,
        decision,
        carried_by_employee_id: carriedBy ?? null,
        note: note.trim() || null,
      });
      toast.success("Decision recorded — no cover was changed");
    } catch (err) {
      toast.error(formatError(err));
    }
  };

  const keepers = c.parties.filter((p) => p.employee_id && !p.unlinked);

  return (
    <div className="space-y-3 rounded-md border border-border bg-card p-3">
      <div className="flex flex-wrap items-baseline gap-x-2 gap-y-1">
        <span className="font-medium text-foreground">{c.name || "Unnamed"}</span>
        {c.dob && (
          <span className="text-2xs text-muted-foreground tabular-nums">
            b. {c.dob}
          </span>
        )}
        {c.nric_masked && (
          <span className="font-mono text-2xs text-subtle">{c.nric_masked}</span>
        )}
        {c.severity === "warn" && (
          <Badge variant="warn">Same benefit twice</Badge>
        )}
        {c.match_tier === "name_dob" && (
          <Badge
            variant="outline"
            title="Matched on name and date of birth — no NRIC on either row, so this pairing is a strong guess rather than a certainty"
          >
            name + DOB match
          </Badge>
        )}
      </div>

      <p className="text-sm text-muted-foreground">{verdict(c)}</p>

      {/* Two named sides, not a list of staff numbers. One container split by a
          rule rather than two bordered boxes — a card inside a card is noise. */}
      <div className="grid grid-cols-1 divide-y divide-border rounded bg-muted/40 sm:grid-cols-2 sm:divide-x sm:divide-y-0">
        {c.parties.map((p, i) => (
          <SideBlock key={`${p.employee_id}-${p.dependant_id}-${i}`} p={p} />
        ))}
      </div>

      {settled ? (
        <div className="flex flex-wrap items-center gap-x-2 gap-y-1 text-sm">
          <Check className="size-3.5 shrink-0 text-good" aria-hidden />
          <span className="text-foreground/80">{decisionLabel(c)}</span>
          {c.decision!.decided_at && (
            <span className="text-2xs text-subtle">
              {c.decision!.decided_at.slice(0, 10)}
              {c.decision!.decided_by ? ` · ${c.decision!.decided_by}` : ""}
            </span>
          )}
          {c.decision!.note && (
            <span className="w-full text-2xs text-muted-foreground">
              “{c.decision!.note}”
            </span>
          )}
          <Button
            size="sm"
            variant="ghost"
            disabled={reopen.isPending}
            onClick={async () => {
              try {
                await reopen.mutateAsync(c.subject_key);
                toast.success("Reopened");
              } catch (err) {
                toast.error(formatError(err));
              }
            }}
          >
            <RotateCcw className="size-3.5" /> Reopen
          </Button>
        </div>
      ) : (
        <div className="space-y-2">
          {c.decision?.stale && (
            <p className="text-2xs text-warn">
              The family changed after this was decided — please confirm again.
            </p>
          )}
          <SectionLabel>Record who keeps them</SectionLabel>
          <div className="flex flex-wrap gap-1.5">
            {keepers.map((p) => (
              <Button
                key={p.employee_id}
                size="sm"
                variant="outline"
                disabled={record.isPending}
                onClick={() => decide("carried_by", p.employee_id)}
              >
                {record.isPending && <Loader2 className="size-3.5 animate-spin" />}
                {p.employee_name ?? p.staff_id}
              </Button>
            ))}
            <Button
              size="sm"
              variant="outline"
              disabled={record.isPending}
              onClick={() => decide("intentional_both")}
            >
              Both, on purpose
            </Button>
            <Button
              size="sm"
              variant="ghost"
              disabled={record.isPending}
              onClick={() => decide("not_a_match")}
            >
              Not the same person
            </Button>
          </div>
          {noteOpen ? (
            <Input
              value={note}
              onChange={(e) => setNote(e.target.value)}
              placeholder="Why — for whoever looks next"
              aria-label="Decision note"
              className="h-8"
              autoFocus
            />
          ) : (
            <button
              type="button"
              className="text-2xs text-muted-foreground underline underline-offset-2 hover:text-foreground"
              onClick={() => setNoteOpen(true)}
            >
              Add a note
            </button>
          )}
        </div>
      )}
    </div>
  );
}

function decisionLabel(c: DualCase): string {
  const d = c.decision!;
  if (d.decision === "carried_by") {
    const who = c.parties.find(
      (p) => p.employee_id === d.carried_by_employee_id,
    );
    const name = who?.employee_name ?? d.carried_by_staff_id ?? "an employee";
    return `Kept under ${name}${d.carried_by_staff_id ? ` (${d.carried_by_staff_id})` : ""}`;
  }
  if (d.decision === "intentional_both") return "Covered by both, on purpose";
  if (d.decision === "not_a_match") return "Not the same person";
  return "Dismissed";
}

function OpportunityRow({ o }: { o: DualOpportunity }) {
  const [listed, other] = o.employees;
  return (
    <div className="space-y-1 rounded-md border border-border bg-card p-3">
      <div className="flex flex-wrap items-baseline gap-2">
        <Users className="size-3.5 shrink-0 text-muted-foreground" aria-hidden />
        <span className="font-medium text-foreground">
          {o.child_name || "Unnamed"}
        </span>
        {o.child_dob && (
          <span className="text-2xs text-muted-foreground tabular-nums">
            b. {o.child_dob}
          </span>
        )}
      </div>
      <p className="text-sm text-muted-foreground">
        Listed under{" "}
        <span className="text-foreground">
          {listed?.employee_name ?? o.listed_under_staff_id}
        </span>{" "}
        ({o.listed_under_staff_id}).{" "}
        <span className="text-foreground">
          {other?.employee_name ?? o.other_staff_id}
        </span>{" "}
        ({o.other_staff_id}) also works here and could cover them.
      </p>
    </div>
  );
}
