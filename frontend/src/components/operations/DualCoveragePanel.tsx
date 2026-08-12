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
 * than a list of staff ids, every button names a PERSON, and the header says in
 * plain words which controls record and which one actually moves money. The
 * earlier version put that fact behind an ⓘ, labelled its buttons with bare
 * staff numbers, and offered no way to tell 4 open cases from 112.
 *
 * **One action, not two ways of saying it.** "Drop this side's cover" writes a
 * real per-dependant exclusion (`services/dual_coverage_assignment.py`) AND
 * files the decision it states, because choosing who keeps the life and taking
 * the other side off cover are the same choice. A "Kept under <person>" button
 * per side used to sit below, naming the same two people and only writing a
 * note; two rows for one intent, and the one that moved the premium was not the
 * obvious one. What is left below is the pair a cover change cannot express —
 * the dual cover is deliberate, or the match is wrong.
 *
 * The sheet is split from the banner because the Dependants table's own
 * "Covered twice" column opens it focused on a single life; a sheet that owned
 * its own open state could not be opened from a table row.
 */
import { useMemo, useState } from "react";
import { AlertTriangle, Check, Loader2, RotateCcw, Users } from "lucide-react";
import { toast } from "sonner";
import { useUpdateDependant } from "@/api/hooks";
import {
  type DualCase,
  type DualOpportunity,
  type DualParty,
  useDualCoverage,
  useRecordDualDecision,
  useReopenDualDecision,
  useSetDualCover,
} from "@/api/dualCoverage";
import { formatError } from "@/lib/errors";
import { cn } from "@/lib/cn";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Switch } from "@/components/ui/switch";
import { SectionLabel } from "@/components/ui/section-label";
import { Segmented } from "@/components/ui/segmented";
import {
  Sheet,
  SheetBody,
  SheetContent,
  SheetDescription,
  SheetHeader,
  SheetTitle,
} from "@/components/ui/sheet";

type Filter = "open" | "decided" | "all";

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
          <SheetDescription>
            The same person, reached through two employees. Each side is a
            separate premium.
          </SheetDescription>
        </SheetHeader>
        <SheetBody className="space-y-5">
          {focused ? (
            <div className="flex items-center justify-between gap-3 border-t border-border pt-4">
              <p className="text-sm text-muted-foreground">
                {focused.length === 0 ? "Nothing open for this person." : "Showing one person."}
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
                Showing {data.cases.length} of {data.total_cases}.
              </p>
            )}
          </section>

          {!focused && data.opportunities.length > 0 && (
            <section className="space-y-3 border-t border-border pt-5">
              <SectionLabel>Both parents work here</SectionLabel>
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

function SideBlock({
  p,
  policyYearId,
  canUnlink,
}: {
  p: DualParty;
  policyYearId: string;
  /** Whether detaching this row still leaves the life reachable. Counting
   *  LINKED DEPENDANT rows alone hid the control on an employee-also-a-spouse
   *  case — the very shape where a wrong spouse link needs detaching, and where
   *  removing it orphans nobody because the person is an employee here. */
  canUnlink: boolean;
}) {
  const setCover = useSetDualCover(policyYearId);
  const update = useUpdateDependant();
  const linked = !!p.dependant_id && !p.unlinked && !!p.employee_id;

  const toggle = async (covered: boolean) => {
    try {
      await setCover.mutateAsync({ dependantId: p.dependant_id!, covered });
    } catch (err) {
      toast.error(formatError(err));
    }
  };

  return (
    <div className="min-w-0 space-y-2 p-3">
      <div>
        <p className="truncate text-sm font-medium text-foreground">
          {p.employee_name ?? "Unknown employee"}
        </p>
        <p className="font-mono text-2xs text-muted-foreground">
          {p.staff_id || "—"}
        </p>
      </div>
      <p className="text-2xs text-subtle">
        {p.dependant_id === null
          ? "Own employee cover"
          : `Linked as ${(p.relationship || "dependant").toLowerCase()}`}
      </p>

      {p.unlinked ? (
        <Badge variant="warn">Not linked</Badge>
      ) : (
        linked && (
          <>
            <label className="flex items-center gap-2 text-2xs text-foreground">
              <Switch
                checked={p.covered}
                disabled={setCover.isPending}
                onCheckedChange={toggle}
                aria-label={`Covered under ${p.employee_name ?? p.staff_id}`}
              />
              Covered under this employee
            </label>
            <p className="text-2xs text-muted-foreground">
              {p.covered_products.length > 0 ? p.covered_products.join(", ") : "—"}
            </p>
            {canUnlink && (
              <button
                type="button"
                className="text-2xs text-muted-foreground underline underline-offset-2 hover:text-foreground"
                disabled={update.isPending}
                onClick={async () => {
                  try {
                    await update.mutateAsync({
                      dependantId: p.dependant_id!,
                      employee_id: null,
                      relink: true,
                    });
                    toast.success(
                      `Unlinked from ${p.employee_name ?? p.staff_id}`,
                    );
                  } catch (err) {
                    toast.error(formatError(err));
                  }
                }}
              >
                Remove link
              </button>
            )}
          </>
        )
      )}
    </div>
  );
}

/** What the row states, in one line: how many employees it reaches, and how
 *  many of them are actually paying. Linking is not covering — a life can be
 *  listed under two employees while only one plan carries them. */
function verdict(c: DualCase): string {
  const inForce = c.parties.filter((p) => p.covered).length;
  if (c.overlapping_products.length > 0) {
    return `Both cover ${c.overlapping_products.join(", ")} — paid for twice.`;
  }
  if (inForce >= 2) return "Covered on both sides, no benefit on both.";
  if (inForce === 1) return "Covered under one side only.";
  return "Neither side is covering them.";
}

function CaseCard({ c, policyYearId }: { c: DualCase; policyYearId: string }) {
  const record = useRecordDualDecision(policyYearId);
  const reopen = useReopenDualDecision(policyYearId);
  const [note, setNote] = useState("");
  const [noteOpen, setNoteOpen] = useState(false);
  const settled = isSettled(c);
  // Any OTHER party — a second dependant row, or the person's own employee
  // record — is what makes a link safe to remove.
  const otherSides = c.parties.length - 1;

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
          <SideBlock
            key={`${p.employee_id}-${p.dependant_id}-${i}`}
            p={p}
            policyYearId={policyYearId}
            canUnlink={otherSides >= 1}
          />
        ))}
      </div>

      {settled ? (
        <div className="flex flex-wrap items-center gap-x-2 gap-y-1 text-sm">
          <Check className="size-3.5 shrink-0 text-good" aria-hidden />
          <span className="text-foreground/80">{decisionLabel(c)}</span>
          {c.decision!.decided_at && (
            <span className="text-2xs text-subtle">
              {c.decision!.decided_at.slice(0, 10)}
              {/* The NAME, never `decided_by` — that is a uuid, and printing it
                  put an unreadable identifier on the one line meant to say who
                  is answerable for the decision. */}
              {c.decision!.decided_by_name
                ? ` · ${c.decision!.decided_by_name}`
                : ""}
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
          {/* There used to be a "Kept under <person>" button per side here. It
              named the same two people as the sides above and did NOT do what
              those do — one wrote a note, the other moved the premium — so the
              two rows read as duplicates of each other and the one that mattered
              was not obvious. Choosing who keeps the life is now said by
              dropping the other side, once, where the consequence is written.
              What remains are the two outcomes a cover change CANNOT express:
              the dual cover is deliberate, or the match is wrong. */}
          <div className="flex flex-wrap items-center gap-1.5">
            <Button
              size="sm"
              variant="outline"
              disabled={record.isPending}
              onClick={() => decide("dismissed")}
            >
              {record.isPending && <Loader2 className="size-3.5 animate-spin" />}
              Mark reviewed
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
