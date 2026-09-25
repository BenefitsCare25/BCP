import { useState, type ReactNode } from "react";
import { Trash2 } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import { Input } from "@/components/ui/input";
import type { ClaimLimitBasis, ClaimLimitScope, ClaimLimitSetting } from "@/types";
import { TRACKED_BASES, memberPreview, sourceChanged } from "@/lib/claimLimits";
import { DRAWDOWN_OPTIONS, EditorShell, Field, Notice, Preview, Segmented, type SegmentOption } from "./FormParts";
import { ScopePicker } from "./ScopePicker";

type Choice = ClaimLimitBasis | "not_limit";

const CHOICES: SegmentOption<Choice>[] = [
  { value: "policy_year", label: "Annual amount" },
  { value: "visits_per_year", label: "Annual visits" },
  { value: "per_visit", label: "Per visit" },
  { value: "per_day", label: "Per day" },
  { value: "per_disability", label: "Per disability" },
  { value: "lifetime", label: "Lifetime" },
  { value: "percentage", label: "Co-pay %" },
  { value: "as_charged", label: "As charged" },
  { value: "not_limit", label: "Not a limit" },
];

interface Props {
  idPrefix: string;
  title: ReactNode;
  setting: ClaimLimitSetting;
  /** `undefined` for the overall limit, which has no SOB cell. */
  wording: string | null | undefined;
  /** The row's sub-lines as the slip states them, when the amounts live
   * there rather than on the row itself. */
  context?: string[];
  scopes: ClaimLimitScope[];
  suggested: string[];
  /** Other plan columns stating the same wording, which this decision can
   * be applied to in one go. */
  sameWordingCount: number;
  /** The overall plan limit: annual amount or nothing, no claim types. */
  overall?: boolean;
  /** No setting stored yet for this cell. */
  fresh?: boolean;
  /** The row is hidden from employees in the SOB: no drawdown. */
  hidden?: boolean;
  onSave: (next: ClaimLimitSetting, applyToSame: boolean) => void;
  onRemove?: () => void;
  onCancel: () => void;
}

export function LimitSettingForm({
  idPrefix,
  title,
  setting,
  wording,
  context = [],
  scopes,
  suggested,
  sameWordingCount,
  overall = false,
  fresh = false,
  hidden = false,
  onSave,
  onRemove,
  onCancel,
}: Props) {
  const [choice, setChoice] = useState<Choice | null>(() => {
    if (setting.status === "not_limit") return "not_limit";
    if (CHOICES.some((c) => c.value === setting.basis)) return setting.basis;
    // A new cell nothing was detected on starts undecided: pre-selecting "Not
    // a limit" invited a one-click save that hid the benefit's amount.
    return fresh ? null : "not_limit";
  });
  const [amount, setAmount] = useState(setting.amount != null ? String(setting.amount) : "");
  // A detected guess's claim type is only as good as the rules that made it;
  // start from today's suggestion, never a stale one.
  const [scopeCodes, setScopeCodes] = useState<string[]>(
    setting.source !== "detected" && setting.claim_scope_codes.length > 0 ? setting.claim_scope_codes : suggested,
  );
  const [applyToSame, setApplyToSame] = useState(sameWordingCount > 0);
  const [displayOnly, setDisplayOnly] = useState(Boolean(setting.display_only) || hidden);

  const decided = choice !== null && choice !== "not_limit";
  const yearly = decided && TRACKED_BASES.has(choice);
  const tracked = yearly && !displayOnly;
  const visits = choice === "visits_per_year";
  const parsed = amount.trim() ? Number(amount) : NaN;
  const amountValid = !yearly || (visits ? Number.isInteger(parsed) && parsed >= 1 : Number.isFinite(parsed) && parsed > 0);
  const needsScope = tracked && !overall && scopes.length > 0 && scopeCodes.length === 0;
  // A broker decision recorded against wording the slip no longer says.
  const changed = wording !== undefined && setting.source === "manual" && sourceChanged(setting, wording);
  const choices = overall ? CHOICES.filter((c) => c.value === "policy_year" || c.value === "not_limit") : CHOICES;

  const { display_only: _previous, ...rest } = setting;
  const draft: ClaimLimitSetting = {
    ...rest,
    ...(yearly && displayOnly ? { display_only: true } : {}),
    basis: decided ? choice : setting.basis,
    amount: yearly && amountValid ? parsed : null,
    // The decision is recorded against the wording the broker is looking at,
    // which is what the backend re-checks at confirmation.
    display: wording?.trim() || setting.display,
    claim_scope_codes: overall ? [] : scopeCodes,
    source: "manual",
    status: choice === "not_limit" ? "not_limit" : "verified",
  };

  const footer = (
    <>
      <Button type="button" size="sm" disabled={choice === null || !amountValid || needsScope} onClick={() => onSave(draft, applyToSame)}>
        Apply
      </Button>
      <Button type="button" size="sm" variant="ghost" onClick={onCancel}>
        Cancel
      </Button>
      {needsScope && <span className="text-xs text-warn">Select a claim type to allow drawdown</span>}
      {onRemove && (
        <Button type="button" size="sm" variant="ghost" className="ml-auto text-error hover:text-error" onClick={onRemove}>
          <Trash2 className="size-3.5" aria-hidden /> Remove
        </Button>
      )}
    </>
  );

  return (
    <EditorShell title={title} onClose={onCancel} footer={footer}>
      {hidden && yearly && <Notice>Hidden in SOB. Show it there to allow drawdown.</Notice>}
      {changed && <Notice>Slip wording changed since this was last applied.</Notice>}
      {(wording || context.length > 0) && (
        <Field label="Slip">
          <div className="text-sm text-foreground sm:pt-1">
            {wording && <p>{wording}</p>}
            {context.map((line) => (
              <p key={line}>{line}</p>
            ))}
          </div>
        </Field>
      )}
      <Field label="Limit type" labelId={`${idPrefix}-basis`}>
        <Segmented name={`${idPrefix}-basis`} labelledBy={`${idPrefix}-basis`} value={choice} options={choices} onChange={setChoice} />
      </Field>
      {yearly && (
        <>
          <Field label={visits ? "Visits per year" : "Limit (S$)"} htmlFor={`${idPrefix}-amount`}>
            <Input
              id={`${idPrefix}-amount`}
              type="number"
              min={visits ? 1 : 0.01}
              step={visits ? 1 : 0.01}
              inputMode={visits ? "numeric" : "decimal"}
              value={amount}
              aria-invalid={!amountValid && amount.trim() !== ""}
              onChange={(e) => setAmount(e.target.value)}
              className="max-w-48"
            />
          </Field>
          <Field label="Employee portal" labelId={`${idPrefix}-portal`}>
            <Segmented
              name={`${idPrefix}-portal`}
              labelledBy={`${idPrefix}-portal`}
              value={displayOnly ? "display" : "drawdown"}
              options={DRAWDOWN_OPTIONS(!hidden)}
              onChange={(v) => setDisplayOnly(v === "display")}
            />
          </Field>
        </>
      )}
      {!overall && scopes.length > 0 && decided && (
        <Field label="Claim types" labelId={`${idPrefix}-scopes`}>
          <ScopePicker
            idPrefix={idPrefix}
            labelledBy={`${idPrefix}-scopes`}
            selected={scopeCodes}
            scopes={scopes}
            suggested={suggested}
            onToggle={(code, checked) =>
              setScopeCodes((current) =>
                checked ? [...current.filter((c) => c !== code), code] : current.filter((c) => c !== code),
              )
            }
          />
        </Field>
      )}
      {decided && (
        <Field label="Portal preview">
          <Preview>{memberPreview(draft, wording)}</Preview>
        </Field>
      )}
      {sameWordingCount > 0 && (
        <label className="flex items-center gap-2 text-xs text-foreground sm:pl-[10rem]">
          <Checkbox checked={applyToSame} onCheckedChange={(v) => setApplyToSame(v === true)} />
          {overall
            ? `Apply to all ${sameWordingCount + 1} plans`
            : `Apply to ${sameWordingCount} other plan${sameWordingCount === 1 ? "" : "s"} with the same wording`}
        </label>
      )}
    </EditorShell>
  );
}
