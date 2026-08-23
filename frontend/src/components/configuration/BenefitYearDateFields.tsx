import { Input } from "@/components/ui/input";
import type { PolicyYear } from "@/types";

export type BenefitYearDateField = "start_date" | "end_date";

interface Props {
  policyYear: PolicyYear;
  draft: Partial<Record<BenefitYearDateField, string>> | undefined;
  errors: Partial<Record<BenefitYearDateField, string>> | undefined;
  readOnly: boolean;
  onChange: (field: BenefitYearDateField, value: string) => void;
  onBlur: (field: BenefitYearDateField, value: string) => void;
}

export function BenefitYearDateFields({
  policyYear,
  draft,
  errors,
  readOnly,
  onChange,
  onBlur,
}: Props) {
  return (
    <div className="grid flex-1 grid-cols-1 gap-2 sm:grid-cols-2 xl:max-w-[320px]">
      {(["start_date", "end_date"] as const).map((field) => {
        const error = errors?.[field];
        const errorId = `benefit-year-${policyYear.id}-${field}-error`;
        const oppositeField = field === "start_date" ? "end_date" : "start_date";
        const oppositeValue = draft?.[oppositeField] ?? policyYear[oppositeField];
        return (
          <label key={field} className="space-y-1 text-xs text-muted-foreground">
            {field === "start_date" ? "Start date" : "End date"}
            <Input
              type="date"
              className={
                error ? "h-8 border-error focus-visible:border-error" : "h-8"
              }
              disabled={readOnly}
              min={field === "end_date" ? oppositeValue : undefined}
              max={field === "start_date" ? oppositeValue : undefined}
              value={draft?.[field] ?? policyYear[field]}
              aria-invalid={Boolean(error)}
              aria-describedby={error ? errorId : undefined}
              data-testid={`benefit-year-${policyYear.id}-${field}`}
              onChange={(event) => onChange(field, event.target.value)}
              onBlur={(event) => onBlur(field, event.target.value)}
            />
            {error && (
              <span id={errorId} role="alert" className="block text-error">
                {error}
              </span>
            )}
          </label>
        );
      })}
    </div>
  );
}
