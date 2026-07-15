import { useMemo } from "react";
import { CalendarClock } from "lucide-react";
import { Button } from "@/components/ui/button";
import { usePolicyYears, useProductSetups } from "@/api/hooks";
import { useSession } from "@/stores/session";
import { formatPolicyRange, parsePeriodOfInsurance } from "@/lib/policy-year";
import type { ProductSetup } from "@/types";

/** First parseable "Period of Insurance" across the slip-origin setup drafts. */
function slipPeriodFor(
  setups: ProductSetup[],
): { start: string; end: string } | null {
  for (const s of setups) {
    if (s.origin !== "placement_slip") continue;
    const raw = s.answers?.header?.period_of_insurance ?? s.answers?.header?.period;
    const parsed = parsePeriodOfInsurance(raw);
    if (parsed) return parsed;
  }
  return null;
}

/**
 * Policy-year-level guard: a placement slip covers the whole policy year, so
 * when its period of insurance doesn't match the active year we surface it once
 * at the top — and offer a one-click switch when a matching year already exists,
 * rather than nudging per-product coverage overrides. Never auto-switches.
 */
export function SlipPeriodBanner({ policyYearId }: { policyYearId: string }) {
  const { data: years = [] } = usePolicyYears();
  const { data: setups = [] } = useProductSetups(policyYearId);
  const setPolicyYear = useSession((s) => s.setPolicyYear);

  const active = years.find((y) => y.id === policyYearId);
  const slip = useMemo(() => slipPeriodFor(setups), [setups]);

  // Compare against the year's nominal contract dates, not the computed coverage
  // envelope — per-product overrides shift coverage_* and would mask the real
  // year-level mismatch.
  const mismatch =
    slip && active && (slip.start !== active.start_date || slip.end !== active.end_date);

  const matchingYear = useMemo(
    () =>
      slip
        ? years.find(
            (y) =>
              y.id !== policyYearId &&
              y.start_date === slip.start &&
              y.end_date === slip.end,
          )
        : undefined,
    [years, slip, policyYearId],
  );

  if (!mismatch || !slip || !active) return null;

  return (
    <div className="flex flex-wrap items-center gap-x-3 gap-y-2 rounded-lg border border-warn/40 bg-warn-soft/40 px-4 py-3 text-sm text-foreground">
      <CalendarClock className="size-4 text-warn shrink-0" />
      <span className="flex-1 min-w-[16rem]">
        This placement slip covers{" "}
        <strong>{formatPolicyRange(slip.start, slip.end)}</strong>, but you're
        viewing the{" "}
        <strong>{formatPolicyRange(active.start_date, active.end_date)}</strong>{" "}
        policy year.{" "}
        {matchingYear
          ? "A policy year matching the slip already exists."
          : "Check you selected the right policy year, or create one for this period on the Policy year page."}
      </span>
      {matchingYear && (
        <Button
          size="sm"
          variant="outline"
          onClick={() => setPolicyYear(matchingYear.id)}
        >
          Switch to {formatPolicyRange(matchingYear.start_date, matchingYear.end_date)}
        </Button>
      )}
    </div>
  );
}
