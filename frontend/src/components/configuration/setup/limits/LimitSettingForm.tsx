import { useState } from "react";
import { AlertTriangle, CheckCircle2, Trash2 } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import { Input } from "@/components/ui/input";
import { cn } from "@/lib/cn";
import type { ClaimLimitBasis, ClaimLimitScope, ClaimLimitSetting } from "@/types";
import { TRACKED_BASES, memberPreview, sourceChanged } from "@/lib/claimLimits";
import { ScopePicker } from "./ScopePicker";

/** The choices, in the order a broker decides: does it count down, or is it
 * a condition the member and assessor read? */
const CHOICES: { basis: ClaimLimitBasis | "not_limit"; title: string; hint: string }[] = [
  { basis: "policy_year", title: "Yearly amount — tracked", hint: "Counts down as claims are approved. Approving past it needs an acknowledgement." },
  { basis: "visits_per_year", title: "Visits per year — tracked", hint: "Each approved claim uses one visit. Approving past it needs an acknowledgement." },
  { basis: "per_visit", title: "Per visit", hint: "A cap on each claim, shown as a condition." },
  { basis: "per_day", title: "Per day", hint: "A daily cap (e.g. room & board), shown as a condition." },
  { basis: "per_disability", title: "Per disability", hint: "A cap per illness or injury, shown as a condition." },
  { basis: "as_charged", title: "As charged", hint: "Covered in full, subject to policy terms." },
  { basis: "percentage", title: "Co-pay / percentage", hint: "The member's share, shown as a condition." },
  { basis: "not_limit", title: "Not a limit", hint: "Just schedule wording — nothing extra is shown or checked." },
];

interface Props {
  idPrefix: string;
  setting: ClaimLimitSetting;
  /** `undefined` for the overall limit, which has no SOB cell. */
  wording: string | null | undefined;
  scopes: ClaimLimitScope[];
  suggested: string[];
  /** Other plan columns stating the same wording, which this decision can
   * be applied to in one go. */
  sameWordingCount: number;
  /** The overall plan limit: annual amount or nothing, no claim types. */
  overall?: boolean;
  onSave: (next: ClaimLimitSetting, applyToSame: boolean) => void;
  onRemove?: () => void;
  onCancel: () => void;
}

export function LimitSettingForm({
  idPrefix,
  setting,
  wording,
  scopes,
  suggested,
  sameWordingCount,
  overall = false,
  onSave,
  onRemove,
  onCancel,
}: Props) {
  const [choice, setChoice] = useState<ClaimLimitBasis | "not_limit">(
    setting.status === "not_limit" ? "not_limit" : setting.basis,
  );
  const [amount, setAmount] = useState(setting.amount != null ? String(setting.amount) : "");
  const [scopeCodes, setScopeCodes] = useState<string[]>(
    setting.claim_scope_codes.length > 0 ? setting.claim_scope_codes : suggested,
  );
  const [applyToSame, setApplyToSame] = useState(sameWordingCount > 0);

  const tracked = choice !== "not_limit" && TRACKED_BASES.has(choice);
  const parsed = amount.trim() ? Number(amount) : NaN;
  const amountValid =
    choice === "visits_per_year"
      ? Number.isInteger(parsed) && parsed >= 1
      : choice === "policy_year"
        ? Number.isFinite(parsed) && parsed > 0
        : true;
  const needsScope = tracked && !overall && scopes.length > 0 && scopeCodes.length === 0;
  // A broker decision recorded against wording the slip no longer says.
  const changed =
    wording !== undefined && setting.source === "manual" && sourceChanged(setting, wording);
  const choices = overall ? CHOICES.filter((c) => c.basis === "policy_year" || c.basis === "not_limit") : CHOICES;

  const draft: ClaimLimitSetting = {
    ...setting,
    basis: choice === "not_limit" ? setting.basis : choice,
    amount: tracked && amountValid ? parsed : null,
    // The decision is recorded against the wording the broker is looking at,
    // which is what the backend re-checks at confirmation.
    display: wording?.trim() || setting.display,
    claim_scope_codes: overall ? [] : scopeCodes,
    source: "manual",
    status: choice === "not_limit" ? "not_limit" : "verified",
  };

  return (
    <div className="grid gap-4 rounded-md border border-border bg-muted/20 p-3 lg:grid-cols-[minmax(16rem,1fr)_minmax(16rem,1fr)]">
      <div className="space-y-3">
        {wording && (
          <p className="text-xs leading-5 text-muted-foreground">
            Slip wording: <span className="font-medium text-foreground">{wording}</span>
          </p>
        )}
        {changed && (
          <p className="flex gap-2 rounded-md border border-warn/40 bg-warn/10 p-2 text-xs text-foreground">
            <AlertTriangle className="mt-0.5 size-3.5 shrink-0 text-warn" aria-hidden />
            The slip wording changed since this was confirmed. Check the choice below still fits, then confirm again.
          </p>
        )}
        <fieldset className="space-y-1.5">
          <legend className="mb-1 text-xs font-medium text-foreground">How claims use this</legend>
          {choices.map((c) => (
            <label
              key={c.basis}
              className={cn(
                "flex cursor-pointer gap-2 rounded-md border px-2.5 py-2 text-xs",
                choice === c.basis ? "border-primary bg-card" : "border-transparent hover:bg-muted",
              )}
            >
              <input
                type="radio"
                name={`${idPrefix}-basis`}
                className="mt-0.5 accent-primary"
                checked={choice === c.basis}
                onChange={() => setChoice(c.basis)}
              />
              <span>
                <span className="block font-medium text-foreground">{c.title}</span>
                <span className="block text-2xs leading-4 text-muted-foreground">{c.hint}</span>
              </span>
            </label>
          ))}
        </fieldset>
      </div>

      <div className="space-y-3">
        {tracked && (
          <div className="space-y-1.5">
            <label htmlFor={`${idPrefix}-amount`} className="text-xs font-medium text-foreground">
              {choice === "visits_per_year" ? "Visits per policy year" : "Yearly amount (S$)"}
            </label>
            <Input
              id={`${idPrefix}-amount`}
              type="number"
              min={choice === "visits_per_year" ? 1 : 0.01}
              step={choice === "visits_per_year" ? 1 : 0.01}
              inputMode={choice === "visits_per_year" ? "numeric" : "decimal"}
              value={amount}
              onChange={(e) => setAmount(e.target.value)}
              className="max-w-48"
            />
            {!amountValid && amount.trim() !== "" && (
              <p className="text-2xs text-warn">
                {choice === "visits_per_year" ? "Enter a whole number of visits." : "Enter an amount above zero."}
              </p>
            )}
          </div>
        )}

        {!overall && scopes.length > 0 && choice !== "not_limit" && (
          <ScopePicker
            idPrefix={idPrefix}
            selected={scopeCodes}
            scopes={scopes}
            suggested={suggested}
            legend="Claim types"
            description={
              tracked
                ? "Claims of these types draw from this limit."
                : "Assessors see this condition when reviewing these claim types."
            }
            onToggle={(code, checked) =>
              setScopeCodes((current) =>
                checked ? [...current.filter((c) => c !== code), code] : current.filter((c) => c !== code),
              )
            }
          />
        )}

        {choice !== "not_limit" && (
          <p className="rounded-md bg-card px-2.5 py-2 text-xs leading-5 text-muted-foreground">
            <span className="font-medium text-foreground">Employees see: </span>
            {memberPreview(draft, wording)}
          </p>
        )}

        {sameWordingCount > 0 && (
          <label className="flex items-center gap-2 text-xs text-foreground">
            <Checkbox checked={applyToSame} onCheckedChange={(v) => setApplyToSame(v === true)} />
            Apply to the {sameWordingCount} other plan{sameWordingCount === 1 ? "" : "s"} with the same wording
          </label>
        )}

        <div className="flex flex-wrap items-center gap-2">
          <Button
            type="button"
            size="sm"
            disabled={(tracked && !amountValid) || needsScope}
            onClick={() => onSave(draft, applyToSame)}
          >
            <CheckCircle2 className="size-3.5" aria-hidden /> Confirm
          </Button>
          <Button type="button" size="sm" variant="ghost" onClick={onCancel}>
            Cancel
          </Button>
          {onRemove && (
            <Button type="button" size="sm" variant="ghost" className="ml-auto text-error hover:text-error" onClick={onRemove}>
              <Trash2 className="size-3.5" aria-hidden /> Remove
            </Button>
          )}
        </div>
        {needsScope && (
          <p className="text-2xs text-warn">Choose at least one claim type for a tracked limit.</p>
        )}
        <p className="text-2xs leading-4 text-muted-foreground">
          Saved to the draft. Confirm the setup when the review is done — nothing reaches employees before that.
        </p>
      </div>
    </div>
  );
}
