/**
 * "No current benefit year" — the one signal that has to reach every page.
 *
 * Exactly one benefit year carries the `active` status ("Current"). That year
 * is what the member portal reads, what claims are submitted against, and the
 * only year the claim-type vocabulary, panel cards and portal enrollment are
 * derived from. Every benefit year is CREATED as a draft (`POST /policy-years`
 * and `/copy` both), and nothing ever promotes one automatically — so the
 * default state of a fully configured company is "portal dark, claims dead",
 * and the only affordance was a small ghost button in one table on one page.
 * The company dashboard flagged it; none of the pages that actually break did.
 *
 * So the banner rides the shell (every company-scoped page) and resolves the
 * problem in place when there is an unambiguous candidate — the year whose
 * period contains today — rather than only pointing at the page that can.
 */
import { useMemo, useState } from "react";
import { Link } from "@tanstack/react-router";
import { CalendarX2 } from "lucide-react";
import { toast } from "sonner";
import { usePolicyYears, useSetCurrentPolicyYear } from "@/api/hooks";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { formatError } from "@/lib/errors";
import { formatPolicyRange, isWithinPolicyPeriod } from "@/lib/policy-year";
import type { PolicyYear } from "@/types";

/** The years, plus whether one is flagged current. `pending` while unknown, so
 *  callers never flash a "nothing is set" message during the first load. */
function useCurrentYear(): {
  pending: boolean;
  /** A FAILED fetch also leaves `data` undefined, which is indistinguishable
   *  from "this company has no benefit years". Kept separate from `pending`
   *  because the two need different endings: pending resolves on its own, an
   *  error does not — so it can neither assert the false "none configured" nor
   *  sit under a skeleton that never finishes. */
  failed: boolean;
  current: PolicyYear | null;
  years: PolicyYear[];
} {
  const { data, isPending, isError } = usePolicyYears();
  const years = data ?? [];
  return {
    pending: isPending,
    failed: isError,
    current: years.find((y) => y.status === "active") ?? null,
    years,
  };
}

/** The year a one-click fix would promote: the one whose coverage period
 *  contains today. Ambiguity (none, or several) falls back to a link. */
function useCandidate(years: PolicyYear[]): PolicyYear | null {
  return useMemo(() => {
    const inPeriod = years.filter((y) =>
      isWithinPolicyPeriod(y.coverage_start, y.coverage_end),
    );
    return inPeriod.length === 1 ? inPeriod[0] : null;
  }, [years]);
}

function SetCurrentButton({ year }: { year: PolicyYear }) {
  const setCurrent = useSetCurrentPolicyYear();
  const [done, setDone] = useState(false);
  return (
    <Button
      size="sm"
      variant="outline"
      className="h-7"
      disabled={setCurrent.isPending || done}
      onClick={async () => {
        try {
          await setCurrent.mutateAsync(year.id);
          setDone(true);
          toast.success("Set as current benefit year");
        } catch (e) {
          toast.error(formatError(e));
        }
      }}
    >
      {/* Labelled with the SAME date pair `useCandidate` selected on. Printing
          the nominal span while selecting on the derived coverage envelope
          contradicts itself the moment a product term widens the envelope: the
          button appears because today is inside the envelope, then names a
          range that doesn't contain today. */}
      Set {formatPolicyRange(year.coverage_start, year.coverage_end)} as current
    </Button>
  );
}

/** Shell banner — mounted once, shown on every company page. */
export function CurrentYearBanner() {
  const { pending, failed, current, years } = useCurrentYear();
  const candidate = useCandidate(years);
  // The banner is an interruption, so it only appears on a KNOWN answer — a
  // failed fetch stays silent (the query client already surfaces the error).
  if (pending || failed || current || years.length === 0) return null;
  return (
    <div className="flex shrink-0 flex-wrap items-center gap-x-3 gap-y-1.5 border-b border-border bg-error-soft/50 px-6 py-2 text-xs">
      <CalendarX2 className="size-3.5 shrink-0 text-error" />
      <span className="font-medium text-foreground">
        No current benefit year
      </span>
      <span className="text-muted-foreground">
        · members can’t see their benefits or submit claims until one is set
      </span>
      <div className="ml-auto flex shrink-0 items-center gap-2">
        {candidate ? (
          <SetCurrentButton year={candidate} />
        ) : (
          <Link
            to="/configuration"
            className="font-medium text-primary underline-offset-2 hover:underline"
          >
            Choose one in Company &amp; Benefits →
          </Link>
        )}
      </div>
    </div>
  );
}

/** Inline version, for a card whose own content is empty because of this.
 *
 * Renders a placeholder rather than nothing while the answer is unknown: this
 * IS the card's whole content in that state, so returning null leaves an empty
 * padded box (the caller's loading gate covers its own queries, not this one).
 */
export function NoCurrentYearNotice() {
  const { pending, failed, current, years } = useCurrentYear();
  const candidate = useCandidate(years);
  if (pending) return <Skeleton className="h-24 w-full" />;
  if (failed) {
    return (
      <p className="text-sm text-muted-foreground">
        Couldn’t load this company’s benefit years.
      </p>
    );
  }
  if (current) return null;
  return (
    <div className="space-y-2.5 rounded-md border border-border bg-error-soft/40 p-3.5">
      <p className="text-sm font-medium text-foreground">
        {years.length === 0
          ? "This company has no benefit year yet."
          : "No benefit year is set as current."}
      </p>
      <p className="max-w-prose text-xs text-muted-foreground">
        Claim types are read from the current benefit year only — the products
        configured on any other year are not visible here, and members cannot
        submit claims until one is current.
      </p>
      {candidate ? (
        <SetCurrentButton year={candidate} />
      ) : (
        <Button asChild size="sm" variant="outline" className="h-7">
          <Link to="/configuration">Open Company &amp; Benefits</Link>
        </Button>
      )}
    </div>
  );
}
