import { useState } from "react";
import { toast } from "sonner";
import { usePolicyYears, useUpdatePolicyYear } from "@/api/hooks";
import { useSession } from "@/stores/session";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { InfoHint } from "@/components/ui/tooltip";
import { formatError } from "@/lib/errors";

/** How long a leaver keeps the portal after their last day of service.
 *
 * A DIFFERENT bound from the grace period beside it, and the pair is easy to
 * confuse: grace is a property of the YEAR (how late any claim may be sent in),
 * this is a property of the MEMBER (how long after their own last day they can
 * still reach the portal at all). A submit must satisfy both.
 *
 * Blank is not "unlimited" — it is the system default. There is deliberately no
 * unlimited value: unlimited is the defect this exists to close
 * (`docs/LEAVER_ACCESS_PLAN.md`). */
export function LeaverAccessField() {
  const policyYearId = useSession((s) => s.currentPolicyYearId);
  const { data: years = [] } = usePolicyYears();
  const update = useUpdatePolicyYear();
  const year = years.find((y) => y.id === policyYearId) ?? null;
  const [draft, setDraft] = useState<string | null>(null);

  if (!year) {
    return (
      <p className="text-sm text-muted-foreground">
        Select a benefit year to set how long leavers keep portal access.
      </p>
    );
  }

  const commit = async () => {
    if (draft === null) return;
    const trimmed = draft.trim();
    const next = trimmed === "" ? null : Number(trimmed);
    if (next !== null && (!Number.isInteger(next) || next < 0)) {
      toast.error("Leaver access must be a whole number of days (or blank).");
      return;
    }
    if (next === year.leaver_access_days) {
      setDraft(null);
      return;
    }
    try {
      await update.mutateAsync({
        policyYearId: year.id,
        payload: { leaver_access_days: next },
      });
      toast.success("Leaver portal access updated");
      setDraft(null);
    } catch (e) {
      toast.error(formatError(e));
    }
  };

  return (
    <div className="flex flex-col gap-1.5 sm:max-w-md">
      <div className="flex items-center gap-1">
        <Label htmlFor="leaver-access">Leaver portal access (days)</Label>
        <InfoHint>
          Days after a member&rsquo;s last day of service that they keep portal
          access — enough to send in claims for treatment they had while
          covered. Their panel card, clinic list and enrolment close on the last
          day itself. Blank uses the default (60 days); 0 ends access on the
          last day.
        </InfoHint>
      </div>
      <Input
        id="leaver-access"
        type="number"
        min={0}
        placeholder="60 (default)"
        className="h-9 w-40"
        value={draft ?? (year.leaver_access_days?.toString() ?? "")}
        onChange={(e) => setDraft(e.target.value)}
        onBlur={commit}
      />
    </div>
  );
}
