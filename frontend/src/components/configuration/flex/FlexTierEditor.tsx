import { AlertTriangle, Trash2, Plus, X, Save } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Input } from "@/components/ui/input";
import { Checkbox } from "@/components/ui/checkbox";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import {
  FAMILY_STATUS_LABELS,
  type FamilyStatusCode,
  type FlexBenefitCategory,
  type FlexEmployeeType,
  type FlexLimit,
  type FlexTier,
  type FlexTierHeadcount,
  type VocabValue,
} from "@/types";
import {
  CURRENCY_OPTIONS,
  DEFAULT_CURRENCY,
  flexTierReview,
  flexTierWalletShape,
  formatWallet,
  numOrNull,
  type FlexWalletShape,
} from "@/lib/flex";
import { FieldLabel, InfoHint } from "@/components/ui/tooltip";
import { MatchSetPicker } from "./MatchSetPicker";

const FAMILY_CODES: FamilyStatusCode[] = ["S", "M", "M1C", "M2C", "M3C"];
// Radix Select forbids an empty-string item value, so this sentinel represents
// "fall back to the scheme default currency" and maps to null on change.
const SCHEME_DEFAULT = "__scheme_default__";

// How the tier's wallet shape reads as a badge on the Flexi-benefit-limit line.
const SHAPE_BADGE: Record<
  FlexWalletShape,
  { label: string; variant: "good" | "outline" | "primary" | "error" }
> = {
  family: { label: "By family status", variant: "good" },
  flat: { label: "Flat cap", variant: "outline" },
  mixed: { label: "Flat + family overrides", variant: "primary" },
  none: { label: "No wallet set", variant: "error" },
};

interface Props {
  tier: FlexTier;
  index: number;
  currency: string;
  /** Reconciled headcount for this tier (family-status split + eligible count),
   *  shown inline on the Eligibility line. Absent for a not-yet-saved tier. */
  headcount?: FlexTierHeadcount;
  /** Distinct roster designations + grades that seed the match-set pickers. */
  designations: VocabValue[];
  grades: VocabValue[];
  onChange: (tier: FlexTier) => void;
  onRemove: () => void;
  /** Save the whole scheme draft (all tiers) from within this tab, so edits
   *  aren't lost on navigation. Disabled until there are unsaved changes. */
  onSave: () => void;
  saving: boolean;
  dirty: boolean;
}

export function FlexTierEditor({
  tier,
  index,
  currency,
  headcount,
  designations,
  grades,
  onChange,
  onRemove,
  onSave,
  saving,
  dirty,
}: Props) {
  const patch = (partial: Partial<FlexTier>) => onChange({ ...tier, ...partial });
  const emp = tier.employee_type ?? {};
  const setEmp = (partial: Partial<FlexEmployeeType>) =>
    patch({ employee_type: { ...emp, ...partial } });
  // A token is resolved once a selected designation covers it as a whole word
  // (so mapping "Director" → "Snr Director" clears the "Director" hint too).
  const isCovered = (tok: string, desigs: string[]) => {
    const t = tok.trim().toLowerCase();
    return desigs.some((d) => {
      const words = d.trim().toLowerCase().split(/\s+/);
      return d.trim().toLowerCase() === t || words.includes(t);
    });
  };
  const unresolved = (emp.unresolved ?? []).filter(
    (t) => !isCovered(t, emp.match_designations ?? []),
  );
  // On any designation change, prune only the now-covered tokens — never blanket
  // wipe (which would hide tokens that are still unmapped).
  const onDesignationsChange = (next: string[]) =>
    setEmp({
      match_designations: next,
      unresolved: (emp.unresolved ?? []).filter((t) => !isCovered(t, next)),
    });
  const dismissUnresolved = (tok: string) =>
    setEmp({ unresolved: (emp.unresolved ?? []).filter((t) => t !== tok) });
  const addDesignationFromUnresolved = (tok: string) =>
    setEmp({
      match_designations: [...(emp.match_designations ?? []), tok],
      unresolved: (emp.unresolved ?? []).filter((t) => t !== tok),
    });
  // Defensive: a malformed/legacy tier may lack these lists.
  const limits = tier.limits ?? [];
  const cats = tier.benefit_categories ?? [];

  const review = flexTierReview(tier, headcount);
  const shapeBadge = SHAPE_BADGE[flexTierWalletShape(tier)];

  const setLimit = (i: number, partial: Partial<FlexLimit>) => {
    patch({ limits: limits.map((l, j) => (j === i ? { ...l, ...partial } : l)) });
  };
  const addLimit = () => {
    const used = new Set(limits.map((l) => l.family_status));
    const next = FAMILY_CODES.find((c) => !used.has(c)) ?? "S";
    patch({ limits: [...limits, { family_status: next, amount: 0 }] });
  };
  const removeLimit = (i: number) =>
    patch({ limits: limits.filter((_, j) => j !== i) });

  const setCat = (i: number, partial: Partial<FlexBenefitCategory>) => {
    patch({
      benefit_categories: cats.map((c, j) => (j === i ? { ...c, ...partial } : c)),
    });
  };
  const addCat = () =>
    patch({
      benefit_categories: [
        ...cats,
        { name: "", claimable: true, sub_limit: null, note: "" },
      ],
    });
  const removeCat = (i: number) =>
    patch({ benefit_categories: cats.filter((_, j) => j !== i) });

  return (
    <div className="rounded-xl border border-border bg-card p-4 space-y-4">
      {/* Review banner — wallet/eligibility problems the broker should fix. The
          unresolved-doc-terms warning has its own actionable block below. */}
      {review.reasons.length > 0 && (
        <div className="flex items-start gap-2 rounded-lg border border-warn/40 bg-warn-soft/30 p-2.5 text-xs">
          <AlertTriangle className="mt-0.5 size-4 shrink-0 text-warn" />
          <div>
            <div className="font-medium text-foreground">Needs review</div>
            <ul className="mt-0.5 list-disc space-y-0.5 pl-4 text-muted-foreground">
              {review.reasons.map((r) => (
                <li key={r}>{r}</li>
              ))}
            </ul>
          </div>
        </div>
      )}

      {/* Employee type / eligibility — roster-anchored match sets (union: an
          employee matches when their designation OR grade is selected here). */}
      <div className="space-y-3">
        <div className="flex items-center justify-between gap-2 flex-wrap">
          <div className="flex items-center gap-1">
            <span className="text-sm font-medium text-foreground">Eligibility</span>
            <InfoHint>
              Employees are tagged to this tier when their job title{" "}
              <strong>or</strong> job grade matches. Pick the values that appear on
              the roster.
            </InfoHint>
          </div>
          <div className="flex items-center gap-2">
            {headcount ? (
              // Reconciled tier: the read-only family-status headcount.
              <div className="flex flex-wrap items-center gap-1.5">
                {FAMILY_CODES.map((code) => {
                  const n = headcount.by_family_status[code] ?? 0;
                  if (n === 0) return null;
                  return (
                    <span
                      key={code}
                      className="inline-flex items-center gap-1 rounded-md border border-border bg-muted/40 px-1.5 py-0.5 text-xs"
                      title={`${FAMILY_STATUS_LABELS[code]} · wallet ${formatWallet(
                        headcount.wallet_by_family_status[code],
                        headcount.currency,
                      )}`}
                    >
                      <span className="text-muted-foreground">{code}</span>
                      <span className="font-medium text-foreground">{n}</span>
                    </span>
                  );
                })}
              </div>
            ) : (
              // New/unreconciled tier: name it (becomes read-only once matched).
              <Input
                value={tier.name ?? ""}
                onChange={(e) => patch({ name: e.target.value })}
                placeholder={`Tier ${index + 1} name (e.g. JG8-17)`}
                className="h-8 w-56 font-medium"
              />
            )}
            <Button
              variant="outline"
              size="sm"
              onClick={onSave}
              disabled={!dirty || saving}
              title="Save the whole scheme draft (all tiers)"
            >
              <Save className="size-4" /> Save draft
            </Button>
            <Button
              variant="ghost"
              size="icon"
              onClick={onRemove}
              aria-label="Remove tier"
            >
              <Trash2 className="size-4 text-error" />
            </Button>
          </div>
        </div>

        {unresolved.length > 0 && (
          <div className="rounded-lg border border-warn/40 bg-warn-soft/30 p-2.5 text-xs">
            <div className="flex items-center gap-1 font-medium text-foreground">
              Not found on the roster
              <InfoHint>
                The document mentioned these, but they match no roster designation.
                Add the matching roster value(s) with +, or dismiss with × once
                mapped.
              </InfoHint>
            </div>
            <div className="mt-1.5 flex flex-wrap gap-1.5">
              {unresolved.map((tok) => (
                <span
                  key={tok}
                  className="inline-flex items-center gap-1 rounded-md border border-warn/50 bg-card px-1.5 py-0.5"
                >
                  <span className="text-foreground">{tok}</span>
                  <button
                    type="button"
                    onClick={() => addDesignationFromUnresolved(tok)}
                    className="text-muted-foreground hover:text-foreground"
                    title="Add this text as-is to the employee types"
                  >
                    <Plus className="size-3" />
                  </button>
                  <button
                    type="button"
                    onClick={() => dismissUnresolved(tok)}
                    className="text-muted-foreground hover:text-error"
                    title="Dismiss (already mapped)"
                  >
                    <X className="size-3" />
                  </button>
                </span>
              ))}
            </div>
          </div>
        )}

        <div className="grid gap-3 sm:grid-cols-2">
          <MatchSetPicker
            label="Job title / designation"
            selected={emp.match_designations ?? []}
            options={designations}
            onChange={onDesignationsChange}
            placeholder="Add a job title from the roster…"
          />
          <MatchSetPicker
            label="Job grade"
            selected={emp.match_grades ?? []}
            options={grades}
            onChange={(next) => setEmp({ match_grades: next })}
            placeholder="Add a job grade from the roster…"
          />
        </div>
      </div>

      {/* Flexi benefit limit (param 1+2: family status → limit, OR a flat cap) */}
      <div className="space-y-2">
        <div className="flex flex-wrap items-center justify-between gap-x-4 gap-y-2">
          <div className="flex items-center gap-2">
            <FieldLabel hint="A flat annual cap applies to every family band; add rows to override specific family statuses. Both can coexist — the flat cap is the fallback.">
              Flexi benefit limit ({(tier.currency || currency || "—").toUpperCase()})
            </FieldLabel>
            <Badge variant={shapeBadge.variant}>{shapeBadge.label}</Badge>
          </div>
          <div className="flex items-center gap-2">
            <FieldLabel hint="Leave on “Scheme default” to inherit the scheme currency.">
              Currency
            </FieldLabel>
            <Select
              value={tier.currency || SCHEME_DEFAULT}
              onValueChange={(v) =>
                patch({ currency: v === SCHEME_DEFAULT ? null : v })
              }
            >
              <SelectTrigger className="h-8 w-44">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value={SCHEME_DEFAULT}>
                  Scheme default ({(currency || DEFAULT_CURRENCY).toUpperCase()})
                </SelectItem>
                {CURRENCY_OPTIONS.map((c) => (
                  <SelectItem key={c} value={c}>
                    {c}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
        </div>
        <div className="flex items-center gap-2">
          <span className="text-sm text-muted-foreground">Flat annual cap</span>
          <Input
            type="number"
            className="w-36"
            value={tier.system_cap ?? ""}
            onChange={(e) => patch({ system_cap: numOrNull(e.target.value) })}
            placeholder="e.g. 10000"
          />
          <span className="text-sm text-muted-foreground">or by family status:</span>
          <Button variant="ghost" size="sm" onClick={addLimit} className="ml-auto">
            <Plus className="size-3.5" /> Add row
          </Button>
        </div>
        {limits.length === 0 ? (
          typeof tier.system_cap === "number" ? null : (
            <p className="text-xs text-muted-foreground">
              Set a flat cap above, or add per-family rows.
            </p>
          )
        ) : (
          <div className="space-y-1.5">
            {limits.map((limit, i) => (
              <div key={i} className="flex items-center gap-2">
                <Select
                  value={limit.family_status}
                  onValueChange={(v) =>
                    setLimit(i, { family_status: v as FamilyStatusCode })
                  }
                >
                  <SelectTrigger className="w-56">
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    {FAMILY_CODES.map((c) => (
                      <SelectItem key={c} value={c}>
                        {FAMILY_STATUS_LABELS[c]}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
                <Input
                  type="number"
                  value={limit.amount}
                  onChange={(e) => setLimit(i, { amount: Number(e.target.value) || 0 })}
                  className="w-36"
                  placeholder="amount"
                />
                <Button
                  variant="ghost"
                  size="icon"
                  onClick={() => removeLimit(i)}
                  aria-label="Remove limit"
                >
                  <Trash2 className="size-4 text-muted-foreground" />
                </Button>
              </div>
            ))}
          </div>
        )}
      </div>

      {/* Benefit statements (param 4: claimable items + sub-limits) */}
      <div className="space-y-2">
        <div className="flex items-center justify-between">
          <FieldLabel hint="Claimable items and their optional sub-limits that members can claim against this wallet.">
            Benefit statements
          </FieldLabel>
          <Button variant="ghost" size="sm" onClick={addCat}>
            <Plus className="size-3.5" /> Add item
          </Button>
        </div>
        {cats.length === 0 ? (
          <p className="text-sm text-muted-foreground">No categories yet.</p>
        ) : (
          <div className="space-y-1.5">
            {cats.map((cat, i) => (
              <div
                key={i}
                className="grid grid-cols-[1fr_auto_7rem_1.5fr_auto] items-center gap-2"
              >
                <Input
                  value={cat.name}
                  onChange={(e) => setCat(i, { name: e.target.value })}
                  placeholder="Category (e.g. Dental)"
                />
                <label className="flex cursor-pointer items-center gap-1.5 text-sm text-foreground whitespace-nowrap">
                  <Checkbox
                    checked={cat.claimable}
                    onCheckedChange={(v) => setCat(i, { claimable: Boolean(v) })}
                  />
                  Claimable
                </label>
                <Input
                  type="number"
                  value={cat.sub_limit ?? ""}
                  onChange={(e) => setCat(i, { sub_limit: numOrNull(e.target.value) })}
                  placeholder="sub-limit"
                />
                <Input
                  value={cat.note ?? ""}
                  onChange={(e) => setCat(i, { note: e.target.value })}
                  placeholder="Note (e.g. 100% up to USD 175 / procedure)"
                />
                <Button
                  variant="ghost"
                  size="icon"
                  onClick={() => removeCat(i)}
                  aria-label="Remove category"
                >
                  <Trash2 className="size-4 text-muted-foreground" />
                </Button>
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}
