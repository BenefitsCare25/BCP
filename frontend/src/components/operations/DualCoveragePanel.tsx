/**
 * The dual-coverage alert on the Dependants tab, and the sheet behind it.
 *
 * The banner counts UNRESOLVED CASES only — a life genuinely listed twice, or an
 * employee also carried as somebody's spouse. Opportunities (married colleagues
 * whose child is listed under just one of them) live in their own collapsed
 * section and are never counted: they are the normal state of such a family, and
 * on a real roster they outnumber the duplicates enough to bury them.
 *
 * A decision is recorded per life, so the flag clears and the list shows only
 * what is still open. Nothing is blocked — deliberate dual cover is legitimate,
 * and `not_a_match` exists because name+DOB matching is occasionally wrong.
 */
import { useState } from "react";
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
import {
  Sheet,
  SheetBody,
  SheetContent,
  SheetHeader,
  SheetTitle,
} from "@/components/ui/sheet";
import { InfoHint } from "@/components/ui/tooltip";

const FLAG_LABELS: Record<string, string> = {
  listed_twice: "Listed under two employees",
  employee_as_spouse: "An employee, also covered as a spouse",
};

export function DualCoveragePanel({ policyYearId }: { policyYearId: string }) {
  const [open, setOpen] = useState(false);
  const { data, isLoading } = useDualCoverage(policyYearId);

  // Nothing to say until there is something to say — a banner that renders
  // "0 families" on every roster becomes background and stops being read.
  if (isLoading || !data) return null;
  const nothing = data.total_cases === 0 && data.total_opportunities === 0;
  if (nothing) return null;

  const unresolved = data.unresolved_cases;

  return (
    <>
      <div
        className={cn(
          "flex flex-wrap items-center gap-3 rounded-lg border px-4 py-3",
          unresolved > 0
            ? "border-warn/40 bg-warn-soft/30"
            : "border-border bg-card",
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
                {unresolved} {unresolved === 1 ? "life" : "lives"} may be covered
                twice
              </span>
              <span className="text-muted-foreground">
                {" "}
                — the same person is on the roster under two employees, or holds
                employee cover and spouse cover at once.
              </span>
            </>
          ) : (
            <>
              <span className="font-medium">No unresolved dual coverage</span>
              {data.total_cases > 0 && (
                <span className="text-muted-foreground">
                  {" "}
                  — {data.total_cases} decided.
                </span>
              )}
            </>
          )}
          {data.total_opportunities > 0 && (
            <span className="text-muted-foreground">
              {" "}
              {data.total_opportunities} dual-coverage{" "}
              {data.total_opportunities === 1 ? "option" : "options"} available.
            </span>
          )}
        </div>
        <Button size="sm" variant="outline" onClick={() => setOpen(true)}>
          Review
        </Button>
      </div>

      <Sheet open={open} onOpenChange={setOpen}>
        <SheetContent className="w-full sm:max-w-2xl">
          <SheetHeader>
            <SheetTitle>Dual coverage</SheetTitle>
          </SheetHeader>
          <SheetBody className="space-y-6">
            <section className="space-y-3">
              <SectionLabel>
                Covered twice
                <InfoHint>
                  The same life reached through two employees. Recording who
                  carries them clears the flag; nothing is blocked, because
                  deliberate dual cover is a legitimate arrangement.
                </InfoHint>
              </SectionLabel>
              {data.cases.length === 0 ? (
                <p className="text-sm text-muted-foreground">
                  Nothing is covered twice.
                </p>
              ) : (
                data.cases.map((c) => (
                  <CaseRow key={c.subject_key} c={c} policyYearId={policyYearId} />
                ))
              )}
              {data.total_cases > data.cases.length && (
                <p className="text-2xs text-subtle">
                  Showing {data.cases.length} of {data.total_cases}.
                </p>
              )}
            </section>

            {data.opportunities.length > 0 && (
              <section className="space-y-3 border-t border-border pt-5">
                <SectionLabel>
                  Dual-coverage options
                  <InfoHint>
                    Both parents work here but the child is listed under only one
                    of them. Nothing is wrong — this is the normal state of such
                    a family, and is listed separately so it cannot bury a real
                    duplicate.
                  </InfoHint>
                </SectionLabel>
                {data.opportunities.map((o) => (
                  <OpportunityRow key={`${o.subject_key}-${o.child_name}`} o={o} />
                ))}
                {data.total_opportunities > data.opportunities.length && (
                  <p className="text-2xs text-subtle">
                    Showing {data.opportunities.length} of{" "}
                    {data.total_opportunities}.
                  </p>
                )}
              </section>
            )}
          </SheetBody>
        </SheetContent>
      </Sheet>
    </>
  );
}

function PartyLine({ p }: { p: DualParty }) {
  return (
    <div className="flex flex-wrap items-baseline gap-x-2 gap-y-0.5 text-sm">
      <span className="font-mono text-2xs text-muted-foreground">
        {p.staff_id || "—"}
      </span>
      <span className="text-foreground">{p.employee_name ?? "Unknown"}</span>
      <span className="text-muted-foreground">
        {p.dependant_id === null
          ? "· their own cover"
          : `· as ${p.relationship || "dependant"}`}
      </span>
      {p.unlinked && (
        <Badge variant="warn" title="Not linked to an active employee">
          unlinked
        </Badge>
      )}
      {p.covered_products.length > 0 && (
        <span className="text-2xs text-subtle">
          {p.covered_products.join(", ")}
        </span>
      )}
    </div>
  );
}

function CaseRow({ c, policyYearId }: { c: DualCase; policyYearId: string }) {
  const record = useRecordDualDecision(policyYearId);
  const reopen = useReopenDualDecision(policyYearId);
  const [note, setNote] = useState("");
  const settled = c.decision && !c.decision.stale;

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
      toast.success("Decision recorded");
    } catch (err) {
      toast.error(formatError(err));
    }
  };

  return (
    <div className="rounded-md border border-border bg-card p-3 space-y-2">
      <div className="flex flex-wrap items-center gap-2">
        <span className="font-medium text-foreground">{c.name || "Unnamed"}</span>
        {c.dob && <span className="text-2xs text-muted-foreground">b. {c.dob}</span>}
        {c.nric_masked && (
          <span className="font-mono text-2xs text-subtle">{c.nric_masked}</span>
        )}
        {/* An empty overlap means two different things — the sides cover
            different products, or fewer than two sides cover anything at all
            (one has declined). Asserting "Different products" for the second
            reads as a verified finding when it is an absence of data. */}
        {c.severity === "warn" ? (
          <Badge variant="warn">Same product</Badge>
        ) : c.parties.filter((p) => p.covered).length >= 2 ? (
          <Badge variant="info">Different products</Badge>
        ) : (
          <Badge variant="outline">Not both covered</Badge>
        )}
        {c.match_tier === "name_dob" && (
          <Badge variant="outline" title="Matched on name and date of birth, not NRIC">
            name + DOB
          </Badge>
        )}
      </div>

      <p className="text-2xs text-subtle">
        {c.flags.map((f) => FLAG_LABELS[f] ?? f).join(" · ")}
        {c.overlapping_products.length > 0 &&
          ` · both cover ${c.overlapping_products.join(", ")}`}
      </p>

      <div className="space-y-1 rounded bg-muted/50 p-2">
        {c.parties.map((p, i) => (
          <PartyLine key={`${p.employee_id}-${p.dependant_id}-${i}`} p={p} />
        ))}
      </div>

      {settled ? (
        <div className="flex flex-wrap items-center gap-2 text-sm">
          <Check className="size-3.5 text-good" aria-hidden />
          <span className="text-foreground/80">
            {c.decision!.decision === "carried_by"
              ? `Carried by ${c.decision!.carried_by_staff_id}`
              : c.decision!.decision === "intentional_both"
                ? "Deliberate dual cover"
                : c.decision!.decision === "not_a_match"
                  ? "Not the same person"
                  : "Dismissed"}
          </span>
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
              The family changed since this was decided — please confirm again.
            </p>
          )}
          <Input
            value={note}
            onChange={(e) => setNote(e.target.value)}
            placeholder="Note (optional)"
            aria-label="Decision note"
            className="h-8"
          />
          <div className="flex flex-wrap gap-1.5">
            {c.parties
              .filter((p) => p.employee_id && !p.unlinked)
              .map((p) => (
                <Button
                  key={p.employee_id}
                  size="sm"
                  variant="outline"
                  disabled={record.isPending}
                  onClick={() => decide("carried_by", p.employee_id)}
                >
                  {record.isPending && <Loader2 className="size-3.5 animate-spin" />}
                  Carried by {p.staff_id}
                </Button>
              ))}
            <Button
              size="sm"
              variant="outline"
              disabled={record.isPending}
              onClick={() => decide("intentional_both")}
            >
              Both — intentional
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
        </div>
      )}
    </div>
  );
}

function OpportunityRow({ o }: { o: DualOpportunity }) {
  return (
    <div className="rounded-md border border-border bg-card p-3 space-y-1.5">
      <div className="flex flex-wrap items-center gap-2">
        <Users className="size-3.5 text-muted-foreground" />
        <span className="font-medium text-foreground">
          {o.child_name || "Unnamed"}
        </span>
        {o.child_dob && (
          <span className="text-2xs text-muted-foreground">b. {o.child_dob}</span>
        )}
      </div>
      <p className="text-sm text-muted-foreground">
        Listed under {o.listed_under_staff_id}. {o.other_staff_id} is also an
        employee here and could cover them.
      </p>
    </div>
  );
}
