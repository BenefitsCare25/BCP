/** A whole-number-of-days setting on the CURRENT benefit year.
 *
 * Two settings share this shape and had been written out twice, character for
 * character apart from their labels: the claim-submission grace period and the
 * leaver portal run-off. They also share the parts that are easy to get subtly
 * wrong and were being maintained in duplicate — `Number()` rather than
 * `parseInt` so "30.5" and "30x" are REJECTED rather than truncated to 30, an
 * edit buffer that survives a validation error so the typed value isn't lost,
 * blank meaning "clear this setting" rather than zero, and commit-on-blur.
 */
import { useState } from "react";
import { toast } from "sonner";
import { usePolicyYears, useUpdatePolicyYear } from "@/api/hooks";
import type { PolicyYear } from "@/types";
import { useSession } from "@/stores/session";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { InfoHint } from "@/components/ui/tooltip";
import { formatError } from "@/lib/errors";
import type { ReactNode } from "react";

/** The two nullable day counts on a benefit year.
 *
 *  Through `Extract`, so the names are pinned to the wire type: rename one on
 *  `PolicyYear` and this union narrows, which makes the caller passing the old
 *  name a type error rather than a payload key the API quietly ignores. */
type DaysField = Extract<
  keyof PolicyYear,
  "claim_grace_period_days" | "leaver_access_days"
>;

export function PolicyYearDaysField({
  id,
  field,
  label,
  hint,
  placeholder,
  noYearPrompt,
  invalidMessage,
  savedMessage,
}: {
  id: string;
  field: DaysField;
  label: string;
  hint: ReactNode;
  placeholder: string;
  /** Shown instead of the control when no benefit year is selected. */
  noYearPrompt: string;
  invalidMessage: string;
  savedMessage: string;
}) {
  const policyYearId = useSession((s) => s.currentPolicyYearId);
  const { data: years = [] } = usePolicyYears();
  const update = useUpdatePolicyYear();
  const year = years.find((y) => y.id === policyYearId) ?? null;
  const [draft, setDraft] = useState<string | null>(null);

  if (!year) {
    return <p className="text-sm text-muted-foreground">{noYearPrompt}</p>;
  }

  const current = year[field] as number | null;

  const commit = async () => {
    if (draft === null) return;
    const trimmed = draft.trim();
    const next = trimmed === "" ? null : Number(trimmed);
    if (next !== null && (!Number.isInteger(next) || next < 0)) {
      toast.error(invalidMessage);
      return;
    }
    if (next === current) {
      setDraft(null);
      return;
    }
    try {
      await update.mutateAsync({
        policyYearId: year.id,
        payload: { [field]: next },
      });
      toast.success(savedMessage);
      setDraft(null);
    } catch (e) {
      toast.error(formatError(e));
    }
  };

  return (
    <div className="flex flex-col gap-1.5 sm:max-w-md">
      <div className="flex items-center gap-1">
        <Label htmlFor={id}>{label}</Label>
        <InfoHint>{hint}</InfoHint>
      </div>
      <Input
        id={id}
        type="number"
        min={0}
        placeholder={placeholder}
        className="h-9 w-40"
        value={draft ?? (current?.toString() ?? "")}
        onChange={(e) => setDraft(e.target.value)}
        onBlur={commit}
      />
    </div>
  );
}
