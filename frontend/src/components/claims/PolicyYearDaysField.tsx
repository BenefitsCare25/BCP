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
import { useEffect, useState } from "react";
import { Pencil, RefreshCw } from "lucide-react";
import { toast } from "sonner";
import { usePolicyYears, useUpdatePolicyYear } from "@/api/hooks";
import type { PolicyYear } from "@/types";
import { useSession } from "@/stores/session";
import { Input } from "@/components/ui/input";
import { Button } from "@/components/ui/button";
import { SectionLabel } from "@/components/ui/section-label";
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

const MAX_WINDOW_DAYS = 3650;

export function PolicyYearDaysField({
  id,
  field,
  label,
  hint,
  placeholder,
  noYearPrompt,
  invalidMessage,
  savedMessage,
  explicitEdit = false,
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
  /** Keep the persisted value read-only until the user deliberately edits it. */
  explicitEdit?: boolean;
}) {
  const policyYearId = useSession((s) => s.currentPolicyYearId);
  const yearsQuery = usePolicyYears();
  const years = yearsQuery.data ?? [];
  const update = useUpdatePolicyYear();
  const year = years.find((y) => y.id === policyYearId) ?? null;
  const [draft, setDraft] = useState<string | null>(null);
  const [editing, setEditing] = useState(false);

  // A draft belongs to one benefit year. Without this reset, switching years
  // could save the previous year's uncommitted value into the new year on blur.
  useEffect(() => {
    setDraft(null);
    setEditing(false);
  }, [year?.id, field]);

  if (yearsQuery.isLoading) {
    return <p className="text-sm text-muted-foreground">Loading setting…</p>;
  }

  if (yearsQuery.isError) {
    return (
      <div className="flex flex-wrap items-center gap-2 text-sm text-error">
        <span>Couldn&apos;t load this benefit-year setting.</span>
        <Button
          type="button"
          variant="outline"
          size="sm"
          onClick={() => void yearsQuery.refetch()}
        >
          <RefreshCw className="size-4" /> Retry
        </Button>
      </div>
    );
  }

  if (!year) {
    return <p className="text-sm text-muted-foreground">{noYearPrompt}</p>;
  }

  const current = year[field] as number | null;

  const commit = async () => {
    if (draft === null || update.isPending) return;
    const trimmed = draft.trim();
    const next = trimmed === "" ? null : Number(trimmed);
    if (
      next !== null &&
      (!Number.isInteger(next) || next < 0 || next > MAX_WINDOW_DAYS)
    ) {
      toast.error(invalidMessage);
      return;
    }
    if (next === current) {
      setDraft(null);
      setEditing(false);
      return;
    }
    try {
      await update.mutateAsync({
        policyYearId: year.id,
        payload: { [field]: next },
      });
      toast.success(savedMessage);
      setDraft(null);
      setEditing(false);
    } catch (e) {
      toast.error(formatError(e));
    }
  };

  return (
    <div
      className="flex flex-col gap-1.5 sm:max-w-md"
      role="group"
      aria-labelledby={`${id}-label`}
    >
      <div className="flex items-center gap-1">
        <SectionLabel id={`${id}-label`} as="span">
          {label}
        </SectionLabel>
        <InfoHint>{hint}</InfoHint>
      </div>
      {explicitEdit && !editing ? (
        <div className="flex items-center gap-2">
          <span className="flex h-9 w-40 items-center rounded-md border border-border bg-muted/40 px-3 text-sm text-foreground tabular-nums">
            {current ?? placeholder}
          </span>
          <Button
            type="button"
            variant="outline"
            size="sm"
            onClick={() => {
              setDraft(current?.toString() ?? "");
              setEditing(true);
            }}
          >
            <Pencil className="size-3.5" aria-hidden />
            Edit
          </Button>
        </div>
      ) : explicitEdit ? (
        <form
          className="flex items-center gap-2"
          onSubmit={(event) => {
            event.preventDefault();
            void commit();
          }}
        >
          <Input
            id={id}
            type="number"
            min={0}
            max={MAX_WINDOW_DAYS}
            placeholder={placeholder}
            className="h-9 w-40"
            disabled={update.isPending}
            aria-busy={update.isPending}
            aria-labelledby={`${id}-label`}
            value={draft ?? (current?.toString() ?? "")}
            onChange={(event) => setDraft(event.target.value)}
            autoFocus
          />
          <Button type="submit" size="sm" loading={update.isPending}>
            Save
          </Button>
          <Button
            type="button"
            variant="outline"
            size="sm"
            disabled={update.isPending}
            onClick={() => {
              setDraft(null);
              setEditing(false);
            }}
          >
            Cancel
          </Button>
        </form>
      ) : (
        <Input
          id={id}
          type="number"
          min={0}
          max={MAX_WINDOW_DAYS}
          placeholder={placeholder}
          className="h-9 w-40"
          disabled={update.isPending}
          aria-busy={update.isPending}
          aria-labelledby={`${id}-label`}
          value={draft ?? (current?.toString() ?? "")}
          onChange={(event) => setDraft(event.target.value)}
          onBlur={() => void commit()}
        />
      )}
    </div>
  );
}
