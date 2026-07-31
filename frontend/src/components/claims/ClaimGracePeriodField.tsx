import { useState } from "react";
import { toast } from "sonner";
import { usePolicyYears, useUpdatePolicyYear } from "@/api/hooks";
import { useSession } from "@/stores/session";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { InfoHint } from "@/components/ui/tooltip";
import { formatError } from "@/lib/errors";

/** Claim-submission grace period, bound to the current benefit year — the year
 * claims submit against. Edit buffer is committed on blur; blank clears the
 * deadline. (Number(), not parseInt, so "30.5"/"30x" are rejected rather than
 * truncated; the draft survives a validation error so the value isn't lost.) */
export function ClaimGracePeriodField() {
  const policyYearId = useSession((s) => s.currentPolicyYearId);
  const { data: years = [] } = usePolicyYears();
  const update = useUpdatePolicyYear();
  const year = years.find((y) => y.id === policyYearId) ?? null;
  const [draft, setDraft] = useState<string | null>(null);

  if (!year) {
    return (
      <p className="text-sm text-muted-foreground">
        Select a benefit year to set a claim submission deadline.
      </p>
    );
  }

  const commit = async () => {
    if (draft === null) return;
    const trimmed = draft.trim();
    const next = trimmed === "" ? null : Number(trimmed);
    if (next !== null && (!Number.isInteger(next) || next < 0)) {
      toast.error("Grace period must be a whole number of days (or blank).");
      return;
    }
    if (next === year.claim_grace_period_days) {
      setDraft(null);
      return;
    }
    try {
      await update.mutateAsync({
        policyYearId: year.id,
        payload: { claim_grace_period_days: next },
      });
      toast.success("Claim grace period updated");
      setDraft(null);
    } catch (e) {
      toast.error(formatError(e));
    }
  };

  return (
    <div className="flex flex-col gap-1.5 sm:max-w-md">
      <div className="flex items-center gap-1">
        <Label htmlFor="claim-grace">Claim submission grace period (days)</Label>
        <InfoHint>
          Days after the current benefit year's coverage period ends during
          which members may still submit claims. Leave blank for no submission
          deadline.
        </InfoHint>
      </div>
      <Input
        id="claim-grace"
        type="number"
        min={0}
        placeholder="No deadline"
        className="h-9 w-40"
        value={draft ?? (year.claim_grace_period_days?.toString() ?? "")}
        onChange={(e) => setDraft(e.target.value)}
        onBlur={commit}
      />
    </div>
  );
}
