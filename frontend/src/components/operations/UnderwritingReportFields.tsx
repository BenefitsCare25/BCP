import type { UnderwritingReportDetails } from "@/api/underwriting";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Checkbox } from "@/components/ui/checkbox";

const fields = [
  ["last_standard_accepted_si", "Last SI accepted at standard rates", "number"],
  ["health_loading", "Health loading (insurer wording)", "text"],
  ["residency_loading", "Residency loading (insurer wording)", "text"],
  ["new_member_letter_date", "New cover letter to member", "date"],
  ["new_insurer_letter_date", "New cover letter to insurer", "date"],
  ["renewal_member_letter_date", "Renewal letter to member", "date"],
  ["renewal_insurer_letter_date", "Renewal letter to insurer", "date"],
  ["annual_premium_net", "Insurer-confirmed annual premium before GST", "number"],
  ["premium_currency", "Premium currency (e.g. SGD)", "text"],
] as const;

export function normalizedReportDetails(value?: UnderwritingReportDetails | null): UnderwritingReportDetails {
  return Object.fromEntries(fields.map(([key]) => [key, value?.[key] ?? null]));
}

export function validReportDetails(value: UnderwritingReportDetails): boolean {
  const amounts = [value.last_standard_accepted_si, value.annual_premium_net];
  if (amounts.some((n) => n != null && (!Number.isFinite(n) || n < 0))) return false;
  if (value.premium_currency && !/^[A-Z]{3}$/.test(value.premium_currency)) return false;
  return value.annual_premium_net == null || Boolean(value.premium_currency);
}

export function UnderwritingReportFields({
  caseId,
  value,
  onChange,
  premiumConfirmed,
  onConfirmPremium,
}: {
  caseId: string;
  value: UnderwritingReportDetails;
  onChange: (next: UnderwritingReportDetails) => void;
  premiumConfirmed: boolean;
  onConfirmPremium: (confirmed: boolean) => void;
}) {
  return (
    <details className="rounded-md border border-border p-3">
      <summary className="cursor-pointer text-sm font-medium text-foreground focus-ring">
        Correspondence and Full EL pricing
      </summary>
      <p className="my-3 text-xs text-muted-foreground">
        Record the insurer’s letter dates and loading terms. For substandard cover, enter the
        confirmed annual premium; loading wording alone is not used to calculate a price.
        Reconfirm the premium when the sum insured or underwriting decision changes.
      </p>
      <div className="grid gap-3 sm:grid-cols-2">
        {fields.map(([key, label, type]) => (
          <div key={key} className="space-y-1">
            <Label htmlFor={`${caseId}-${key}`} className="text-xs">{label}</Label>
            <Input
              id={`${caseId}-${key}`}
              type={type}
              min={type === "number" ? 0 : undefined}
              step={type === "number" ? "0.01" : undefined}
              maxLength={key === "premium_currency" ? 3 : 200}
              value={value[key] ?? ""}
              onChange={(event) => {
                const raw = event.target.value;
                const next = raw === "" ? null : type === "number" ? Number(raw)
                  : key === "premium_currency" ? raw.toUpperCase() : raw;
                onChange({ ...value, [key]: next });
              }}
              className="h-8 text-sm"
            />
          </div>
        ))}
      </div>
      {value.annual_premium_net != null && (
        <label className="mt-3 flex items-start gap-2 text-sm text-foreground">
          <Checkbox checked={premiumConfirmed} onCheckedChange={(checked) => onConfirmPremium(checked === true)} />
          I checked this annual premium against the insurer’s terms for the current sum insured.
        </label>
      )}
      {!validReportDetails(value) && (
        <p className="mt-2 text-xs text-error" role="alert">
          Use nonnegative amounts and a three-letter currency for the annual premium.
        </p>
      )}
    </details>
  );
}
