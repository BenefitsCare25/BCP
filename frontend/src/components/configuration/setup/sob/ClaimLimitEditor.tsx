import { useMemo, useState } from "react";
import {
  AlertTriangle,
  CheckCircle2,
  CircleDollarSign,
  Plus,
  RefreshCw,
} from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import { Input } from "@/components/ui/input";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import type {
  ClaimLimitBasis,
  ClaimLimitScope,
  ClaimLimitSetting,
  PlanAnswer,
  SobItemAnswer,
  SobSchedule,
} from "@/types";
import {
  CLAIM_LIMIT_BASES,
  CLAIM_LIMIT_BASIS_LABELS,
  claimLimitSourceForPlan,
  columnIdForPlan,
  draftDetectedLimitSetting,
  draftLimitSetting,
  itemLimitForPlan,
  isLiveAnnualLimit,
  sourceWordingForPlan,
} from "@/lib/claimLimits";
import { copayFields, copayValue } from "@/lib/sob";

interface Props {
  sob: SobSchedule;
  plans: PlanAnswer[];
  productCode: string;
  claimScopes: ClaimLimitScope[];
  setSob: (fn: (schedule: SobSchedule) => SobSchedule) => void;
}

type EditTarget = "overall" | string | null;

const statusLabel = (setting: ClaimLimitSetting) =>
  isLiveAnnualLimit(setting)
    ? "Verified · live"
    : setting.status === "verified"
      ? "Verified · policy wording"
    : setting.status === "not_limit"
      ? "Informational · no balance"
      : "Needs review · not live";

const statusVariant = (setting: ClaimLimitSetting) =>
  setting.status === "verified"
    ? "good"
    : setting.status === "not_limit"
      ? "default"
      : "warn";

const normalizeWording = (value: string | null) =>
  (value ?? "").trim().replace(/\s+/g, " ").toLocaleLowerCase();

const hasSourceChanged = (setting: ClaimLimitSetting, wording: string | null) =>
  normalizeWording(setting.display) !== normalizeWording(wording);

const isAnnualBalance = (setting: ClaimLimitSetting) =>
  setting.basis === "policy_year" && setting.status !== "not_limit";

const isUnavailableWording = (value: string) =>
  ["na", "n/a", "not applicable", "not covered", "-"].includes(
    value.trim().toLocaleLowerCase(),
  );

const claimScopesForBenefit = (
  productCode: string,
  item: SobItemAnswer,
  setting: ClaimLimitSetting,
  scopes: ClaimLimitScope[],
) => {
  const code = productCode.trim().toLocaleUpperCase();
  const name = item.name.trim().toLocaleLowerCase();
  const suggestedCodes: string[] = [];
  const suggest = (scopeCode: string) => {
    if (!suggestedCodes.includes(scopeCode)) suggestedCodes.push(scopeCode);
  };

  if (["GP", "GCGP", "GOGP"].includes(code)) {
    if (
      name.includes("tcm") ||
      name.includes("traditional chinese") ||
      name.includes("chinese physician")
    ) {
      suggest("gp_tcm");
    }
    if (name.includes("physio")) suggest("gp_physiotherapy");
    if (/\bgp\b/.test(name) || name.includes("general practitioner")) {
      suggest("standard");
    }
    if (suggestedCodes.length === 0) suggest("standard");
  } else if (["SP", "GCSP", "GOSP", "GD", "DENTAL"].includes(code)) {
    suggest("standard");
  } else if (["GHS", "GHS2", "IMP"].includes(code)) {
    if (name.includes("pre") && name.includes("post") && name.includes("hospital")) {
      suggest("ghs_pre_post");
    }
    if (name.includes("dialysis") || name.includes("cancer treatment")) {
      suggest("ghs_dialysis_cancer");
    }
    if (
      name.includes("emergency") ||
      name.includes("a&e") ||
      name.includes("accidental outpatient")
    ) {
      suggest("ghs_emergency_outpatient");
    }
    if (
      name.includes("hospitalisation") ||
      name.includes("hospitalization") ||
      name.includes("day surgery")
    ) {
      suggest("ghs_hospitalisation");
    }
  }

  const rank = (scope: ClaimLimitScope) =>
    suggestedCodes.includes(scope.code)
      ? 0
      : setting.claim_scope_codes.includes(scope.code)
        ? 1
        : 2;
  return {
    scopes: scopes
      .map((scope, index) => ({ scope, index }))
      .sort((a, b) => rank(a.scope) - rank(b.scope) || a.index - b.index)
      .map(({ scope }) => scope),
    recommendedScopeCodes: suggestedCodes.filter((scopeCode) =>
      scopes.some((scope) => scope.code === scopeCode),
    ),
  };
};

function ScopePicker({
  idPrefix,
  setting,
  scopes,
  recommendedScopeCodes,
  description,
  onScope,
}: {
  idPrefix: string;
  setting: ClaimLimitSetting;
  scopes: ClaimLimitScope[];
  recommendedScopeCodes: string[];
  description: string;
  onScope: (scopeCode: string, checked: boolean) => void;
}) {
  const [showOtherScopes, setShowOtherScopes] = useState(false);
  const initiallyVisible = scopes.filter(
    (scope) =>
      recommendedScopeCodes.includes(scope.code) ||
      setting.claim_scope_codes.includes(scope.code),
  );
  const compact = initiallyVisible.length > 0 && initiallyVisible.length < scopes.length;
  const visibleScopes = showOtherScopes || !compact ? scopes : initiallyVisible;
  const hiddenCount = scopes.length - visibleScopes.length;

  return (
    <fieldset className="space-y-2">
      <legend className="text-xs font-medium text-foreground">Applies to claim types</legend>
      <p className="text-2xs leading-5 text-muted-foreground">{description}</p>
      <div className="grid gap-2 sm:grid-cols-2">
        {visibleScopes.map((scope) => {
          const id = `${idPrefix}-${scope.code}`;
          const checked = setting.claim_scope_codes.includes(scope.code);
          return (
            <label
              key={scope.code}
              htmlFor={id}
              className="flex min-h-9 cursor-pointer items-center gap-2 rounded-md px-2 text-xs text-foreground hover:bg-muted"
            >
              <Checkbox
                id={id}
                checked={checked}
                onCheckedChange={(value) => onScope(scope.code, value === true)}
              />
              <span className="min-w-0 flex-1">{scope.label}</span>
              {recommendedScopeCodes.includes(scope.code) && (
                <span className="text-2xs text-muted-foreground">Suggested</span>
              )}
            </label>
          );
        })}
      </div>
      {hiddenCount > 0 && (
        <Button
          type="button"
          size="sm"
          variant="ghost"
          onClick={() => setShowOtherScopes(true)}
        >
          <Plus className="size-3.5" aria-hidden />
          Show {hiddenCount} other claim type{hiddenCount === 1 ? "" : "s"}
        </Button>
      )}
    </fieldset>
  );
}

function SettingEditor({
  setting,
  wording,
  scopes,
  recommendedScopeCodes = [],
  sourceChanged = false,
  onChange,
  onScope,
  onUseSource,
}: {
  setting: ClaimLimitSetting;
  wording: string | null;
  scopes: ClaimLimitScope[];
  recommendedScopeCodes?: string[];
  sourceChanged?: boolean;
  onChange: (next: ClaimLimitSetting) => void;
  onScope?: (scopeCode: string, checked: boolean) => void;
  onUseSource?: () => void;
}) {
  const patch = (
    values: Partial<ClaimLimitSetting>,
    acknowledgeSource = false,
  ) =>
    onChange({
      ...setting,
      ...values,
      display: acknowledgeSource ? wording?.trim() || null : setting.display,
      source: "manual",
      status: "needs_review",
    });
  const missingRequiredScope = Boolean(
    setting.basis === "policy_year" &&
      onScope &&
      scopes.length > 0 &&
      setting.claim_scope_codes.length === 0,
  );
  const missingAnnualAmount =
    setting.basis === "policy_year" && !(setting.amount && setting.amount > 0);

  return (
    <div className="grid gap-4 border-t border-border bg-muted/20 px-3 py-4 lg:grid-cols-[minmax(12rem,0.8fr)_minmax(18rem,1.2fr)]">
      <div className="space-y-3">
        <div className="space-y-1.5">
          <label className="text-xs font-medium text-foreground">
            How claims use this rule
          </label>
          <Select
            value={setting.basis}
            onValueChange={(basis) =>
              patch({
                basis: basis as ClaimLimitBasis,
                amount: basis === "policy_year" ? setting.amount : null,
              }, true)
            }
          >
            <SelectTrigger aria-label="How claims use this rule">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              {CLAIM_LIMIT_BASES.map((basis) => (
                <SelectItem key={basis} value={basis}>
                  {CLAIM_LIMIT_BASIS_LABELS[basis]}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>

        {setting.basis === "policy_year" && (
          <div className="space-y-1.5">
            <label htmlFor="claim-limit-amount" className="text-xs font-medium text-foreground">
              Annual amount (SGD)
            </label>
            <Input
              id="claim-limit-amount"
              type="number"
              min={0.01}
              step={0.01}
              inputMode="decimal"
              value={setting.amount ?? ""}
              onChange={(event) =>
                patch({
                  amount: event.target.value ? Number(event.target.value) : null,
                }, true)
              }
              aria-describedby="claim-limit-enforcement-note"
            />
            <p id="claim-limit-enforcement-note" className="text-2xs text-muted-foreground">
              This is the only basis that creates a remaining balance and approval guard.
            </p>
          </div>
        )}

        {setting.basis !== "policy_year" && (
          <p className="text-2xs leading-5 text-muted-foreground">
            This stays as policy wording. It does not create a remaining balance
            or block claim approval.
          </p>
        )}

        {wording && (
          <p className="text-xs leading-5 text-muted-foreground">
            SoB wording: <span className="text-foreground">{wording}</span>
          </p>
        )}
        {sourceChanged && (
          <div
            id="claim-limit-source-changed-note"
            className="space-y-2 rounded-md border border-warn/30 bg-warn/10 p-3 text-xs leading-5 text-foreground"
          >
            <p className="font-medium">The SoB wording changed after this setting was saved.</p>
            <p>
              Use the updated value below, or edit the basis or amount to record
              an intentional override. This setting cannot be verified as-is.
            </p>
            {onUseSource && (
              <Button type="button" size="sm" variant="outline" onClick={onUseSource}>
                <RefreshCw className="size-3.5" aria-hidden />
                Use updated SoB value
              </Button>
            )}
          </div>
        )}
      </div>

      <div className="space-y-3">
        {onScope && scopes.length > 0 && (
          <div className="space-y-2">
            <ScopePicker
              idPrefix="claim-limit"
              setting={setting}
              scopes={scopes}
              recommendedScopeCodes={recommendedScopeCodes}
              description="Select every claim type that should draw from this annual balance."
              onScope={onScope}
            />
            {missingRequiredScope && (
              <p
                id="claim-limit-scope-note"
                className="text-2xs font-medium text-warn"
              >
                Choose at least one claim type before verifying this setting.
              </p>
            )}
          </div>
        )}

        <div className="flex flex-wrap gap-2">
          <Button
            type="button"
            size="sm"
            onClick={() =>
              onChange({
                ...setting,
                display: wording?.trim() || setting.display,
                source: "manual",
                status: "verified",
              })
            }
            disabled={missingAnnualAmount || missingRequiredScope || sourceChanged}
            aria-describedby={
              sourceChanged
                ? "claim-limit-source-changed-note"
                : missingRequiredScope
                  ? "claim-limit-scope-note"
                  : undefined
            }
          >
            <CheckCircle2 className="size-3.5" aria-hidden />
            {setting.basis === "policy_year"
              ? "Activate annual balance"
              : "Save policy wording"}
          </Button>
          <Button
            type="button"
            size="sm"
            variant="outline"
            onClick={() =>
              onChange({
                ...setting,
                amount: null,
                display: wording?.trim() || setting.display,
                source: "manual",
                status: "not_limit",
              })
            }
          >
            No balance needed
          </Button>
        </div>
        <p className="text-2xs leading-5 text-muted-foreground">
          This saves the decision in the draft. Publish the setup at the bottom
          of the page when the review is complete.
        </p>
      </div>
    </div>
  );
}

function GuidanceRoutingEditor({
  setting,
  wording,
  scopes,
  recommendedScopeCodes,
  sourceChanged,
  onScope,
  onUseSource,
  onConfirm,
  onNoMapping,
}: {
  setting: ClaimLimitSetting;
  wording: string | null;
  scopes: ClaimLimitScope[];
  recommendedScopeCodes: string[];
  sourceChanged: boolean;
  onScope: (scopeCode: string, checked: boolean) => void;
  onUseSource: () => void;
  onConfirm: () => void;
  onNoMapping: () => void;
}) {
  const missingScope = setting.claim_scope_codes.length === 0;

  return (
    <div className="grid gap-4 border-t border-border bg-muted/20 px-3 py-4 lg:grid-cols-[minmax(12rem,0.8fr)_minmax(18rem,1.2fr)]">
      <div className="space-y-3">
        <div>
          <p className="text-xs font-medium text-foreground">Where this guidance appears</p>
          <p className="mt-1 text-2xs leading-5 text-muted-foreground">
            This mapping shows the wording to assessors for the selected claim
            types. It never creates a balance or blocks approval.
          </p>
        </div>
        {wording && (
          <p className="text-xs leading-5 text-muted-foreground">
            SoB wording: <span className="text-foreground">{wording}</span>
          </p>
        )}
        {sourceChanged && (
          <div
            id="guidance-source-changed-note"
            className="space-y-2 rounded-md border border-warn/30 bg-warn/10 p-3 text-xs leading-5 text-foreground"
          >
            <p className="font-medium">The SoB wording changed after this mapping was saved.</p>
            <p>
              Adopt the updated wording before confirming where this guidance appears.
            </p>
            <Button type="button" size="sm" variant="outline" onClick={onUseSource}>
              <RefreshCw className="size-3.5" aria-hidden />
              Use updated SoB value
            </Button>
          </div>
        )}
      </div>

      <div className="space-y-3">
        <ScopePicker
          idPrefix="claim-guidance"
          setting={setting}
          scopes={scopes}
          recommendedScopeCodes={recommendedScopeCodes}
          description="Start with the suggested claim type. Add another only when this benefit line is genuinely shared."
          onScope={onScope}
        />
        <div className="flex flex-wrap gap-2">
          <Button
            type="button"
            size="sm"
            onClick={onConfirm}
            disabled={missingScope || sourceChanged}
            aria-describedby={sourceChanged ? "guidance-source-changed-note" : undefined}
          >
            <CheckCircle2 className="size-3.5" aria-hidden />
            Confirm claim mapping
          </Button>
          <Button type="button" size="sm" variant="outline" onClick={onNoMapping}>
            No claim type mapping
          </Button>
        </div>
        {missingScope && (
          <p className="text-2xs leading-5 text-muted-foreground">
            Select a claim type, or explicitly confirm that this wording does
            not belong to a member claim type.
          </p>
        )}
      </div>
    </div>
  );
}

export function ClaimLimitEditor({
  sob,
  plans,
  productCode,
  claimScopes,
  setSob,
}: Props) {
  const [planCode, setPlanCode] = useState(plans[0]?.code ?? "");
  const [editing, setEditing] = useState<EditTarget>(null);
  const [addLine, setAddLine] = useState("");
  const activePlan = plans.find((plan) => plan.code === planCode) ?? plans[0];
  const activeCode = activePlan?.code ?? "";
  const columnId = columnIdForPlan(sob, activeCode);
  const overall = sob.plan_claim_limits?.[activeCode] ?? null;

  const configured = useMemo(
    () =>
      sob.items.filter((item) => itemLimitForPlan(sob, item, activeCode) !== null),
    [activeCode, sob],
  );
  const configuredState = configured.map((item) => {
    const setting = itemLimitForPlan(sob, item, activeCode)!;
    const source = claimLimitSourceForPlan(sob, item, activeCode);
    return {
      item,
      setting,
      source,
      sourceChanged: hasSourceChanged(setting, source.wording),
    };
  });
  const configuredByUid = new Map(
    configuredState.map((entry) => [entry.item.uid, entry]),
  );
  const annualConfiguredState = configuredState.filter(({ setting }) =>
    isAnnualBalance(setting),
  );
  const available = sob.items.filter(
    (item) => itemLimitForPlan(sob, item, activeCode) === null,
  );
  const detectedAvailable = available
    .map((item) => {
      const source = claimLimitSourceForPlan(sob, item, activeCode);
      return {
        item,
        source,
        wording: source.wording,
        setting: draftDetectedLimitSetting(source),
      };
    })
    .filter(
      ({ wording, source, setting }) =>
        Boolean(wording) &&
        (source.structuredPolicyYear || setting.basis !== "informational"),
    );
  const detectedAnnualAvailable = detectedAvailable.filter(
    ({ setting }) =>
      setting.basis === "policy_year" &&
      setting.amount !== null &&
      setting.amount > 0,
  );
  const detectedAnnualUids = new Set(
    detectedAnnualAvailable.map(({ item }) => item.uid),
  );
  const manualAvailable = available.filter(
    (item) => !detectedAnnualUids.has(item.uid),
  );
  const policyRows = sob.items
    .map((item) => {
      const configuredEntry = configuredByUid.get(item.uid);
      const rules =
        item.kind === "copay"
          ? copayFields(item)
              .filter(
                (field) =>
                  field.key !== "per_policy_year" ||
                  (!detectedAnnualUids.has(item.uid) &&
                    !(
                      configuredEntry &&
                      isAnnualBalance(configuredEntry.setting)
                    )),
              )
              .map((field) => ({
                label: field.label,
                value: copayValue(item, columnId ?? "", field.key).trim(),
              }))
              .filter(({ value }) => Boolean(value))
          : [];
      if (
        rules.length === 0 &&
        configuredEntry &&
        !isAnnualBalance(configuredEntry.setting)
      ) {
        const value =
          configuredEntry.source.wording ??
          configuredEntry.setting.display ??
          "No balance required";
        rules.push({ label: "Policy condition", value });
      }
      return { item, rules, configuredEntry };
    })
    .filter(
      ({ rules, configuredEntry }) =>
        rules.length > 0 &&
        (rules.some(({ value }) => !isUnavailableWording(value)) ||
          Boolean(
            configuredEntry && !isAnnualBalance(configuredEntry.setting),
          )),
    );
  const verified =
    Number(Boolean(overall && isLiveAnnualLimit(overall))) +
    annualConfiguredState.filter(
      ({ setting, sourceChanged }) =>
        isLiveAnnualLimit(setting) && !sourceChanged,
    ).length;
  const needsReview =
    Number(
      Boolean(
        overall && isAnnualBalance(overall) && overall.status === "needs_review",
      ),
    ) +
    annualConfiguredState.filter(
      ({ setting, sourceChanged }) =>
        setting.status === "needs_review" || sourceChanged,
    ).length;
  const totalNeedsReview = needsReview + detectedAnnualAvailable.length;
  const scopeLabels = new Map(claimScopes.map((scope) => [scope.code, scope.label]));

  const mappedByScope = new Map<
    string,
    { item: SobItemAnswer; setting: ClaimLimitSetting; sourceChanged: boolean }
  >();
  for (const { item, setting, sourceChanged } of annualConfiguredState) {
    for (const scope of setting.claim_scope_codes) {
      mappedByScope.set(scope, { item, setting, sourceChanged });
    }
  }

  const setOverall = (next: ClaimLimitSetting) =>
    setSob((current) => ({
      ...current,
      plan_claim_limits: {
        ...(current.plan_claim_limits ?? {}),
        [activeCode]: next,
      },
    }));

  const setItem = (uid: string, next: ClaimLimitSetting) =>
    setSob((current) => {
      const targetColumn = columnIdForPlan(current, activeCode);
      if (!targetColumn) return current;
      return {
        ...current,
        items: current.items.map((item) =>
          item.uid === uid
            ? {
                ...item,
                claim_limits: { ...(item.claim_limits ?? {}), [targetColumn]: next },
              }
            : item,
        ),
      };
    });

  const assignScope = (uid: string, scopeCode: string, checked: boolean) =>
    setSob((current) => {
      const targetColumn = columnIdForPlan(current, activeCode);
      if (!targetColumn) return current;
      return {
        ...current,
        items: current.items.map((item) => {
          const setting = item.claim_limits?.[targetColumn];
          if (!setting) return item;
          const without = setting.claim_scope_codes.filter((code) => code !== scopeCode);
          const claimScopeCodes =
            item.uid === uid && checked ? [...without, scopeCode] : without;
          if (claimScopeCodes.length === setting.claim_scope_codes.length &&
              claimScopeCodes.every((code, index) => code === setting.claim_scope_codes[index])) {
            return item;
          }
          return {
            ...item,
            claim_limits: {
              ...(item.claim_limits ?? {}),
              [targetColumn]: {
                ...setting,
                claim_scope_codes: claimScopeCodes,
                source: "manual",
                status: "needs_review",
              },
            },
          };
        }),
      };
    });

  const addSelectedLine = () => {
    const item = sob.items.find((row) => row.uid === addLine);
    if (!item || !columnId) return;
    const detected = draftDetectedLimitSetting(
      claimLimitSourceForPlan(sob, item, activeCode),
    );
    setItem(
      item.uid,
      {
        ...detected,
        basis: "policy_year",
        amount: detected.basis === "policy_year" ? detected.amount : null,
        status: "needs_review",
      },
    );
    setEditing(item.uid);
    setAddLine("");
  };

  const reviewDetectedLine = (
    item: SobItemAnswer,
    setting: ClaimLimitSetting,
  ) => {
    setItem(item.uid, setting);
    setEditing(item.uid);
  };

  if (!activePlan || !columnId) return null;

  return (
    <section className="overflow-hidden rounded-md border border-border" aria-labelledby="claim-limits-heading">
      <div className="flex flex-wrap items-start justify-between gap-3 bg-muted/30 px-3 py-3">
        <div className="min-w-0">
          <h3 id="claim-limits-heading" className="flex items-center gap-2 text-sm font-semibold text-foreground">
            <CircleDollarSign className="size-4 text-primary" aria-hidden />
            Annual balances
          </h3>
          <p className="mt-1 max-w-3xl text-xs leading-5 text-muted-foreground">
            Set only annual SGD balances that employees can track and claim
            approval can enforce. Per-visit amounts, visit counts, co-payments
            and as-charged conditions remain assessment guidance below and
            never block approval.
          </p>
        </div>
        <div className="w-full sm:w-64">
          <label className="sr-only" htmlFor="claim-limit-plan">Plan type</label>
          <Select
            value={activeCode}
            onValueChange={(value) => {
              setPlanCode(value);
              setEditing(null);
            }}
          >
            <SelectTrigger id="claim-limit-plan" aria-label="Plan type for claim limits">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              {plans.map((plan) => (
                <SelectItem key={plan.code} value={plan.code}>
                  {plan.label || plan.code}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>
      </div>

      <div className="flex flex-wrap gap-x-4 gap-y-2 border-y border-border px-3 py-2 text-xs">
        {verified > 0 ? (
          <span className="inline-flex items-center gap-1.5 text-good">
            <CheckCircle2 className="size-3.5" aria-hidden /> {verified} annual
            balance{verified === 1 ? "" : "s"} live
          </span>
        ) : (
          <span className="text-muted-foreground">No annual balances live</span>
        )}
        {totalNeedsReview > 0 && (
          <span className="inline-flex items-center gap-1.5 text-warn">
            <AlertTriangle className="size-3.5" aria-hidden /> {totalNeedsReview}
            annual balance{totalNeedsReview === 1 ? "" : "s"} need review
          </span>
        )}
        <span className="text-muted-foreground">
          {policyRows.length} benefit line{policyRows.length === 1 ? "" : "s"}
          with policy wording
        </span>
      </div>

      <div className="divide-y divide-border">
        <div>
          <div className="flex flex-wrap items-center gap-3 px-3 py-3">
            <div className="min-w-0 flex-1">
              <p className="text-sm font-medium text-foreground">Overall annual balance</p>
              <p className="text-xs text-muted-foreground">
                Applies across claims assessed against {activePlan.label || activeCode}.
              </p>
            </div>
            {overall ? (
              <>
                <span className="text-xs text-foreground">
                  {CLAIM_LIMIT_BASIS_LABELS[overall.basis]}
                  {overall.amount != null ? ` · SGD ${overall.amount.toLocaleString()}` : ""}
                </span>
                <Badge variant={statusVariant(overall)}>{statusLabel(overall)}</Badge>
              </>
            ) : (
              <Badge>No overall annual balance</Badge>
            )}
            <Button
              type="button"
              size="sm"
              variant="outline"
              onClick={() => {
                if (!overall) {
                  setOverall({ ...draftLimitSetting(null), basis: "policy_year" });
                }
                setEditing(editing === "overall" ? null : "overall");
              }}
            >
              {overall ? "Edit" : "Set annual balance"}
            </Button>
          </div>
          {editing === "overall" && overall && (
            <SettingEditor setting={overall} wording={overall.display} scopes={[]} onChange={setOverall} />
          )}
        </div>

        {annualConfiguredState.map(({ item, setting, source, sourceChanged }) => {
          const wording = source.wording;
          const benefitScopes = claimScopesForBenefit(
            productCode,
            item,
            setting,
            claimScopes,
          );
          return (
            <div key={item.uid}>
              <div className="grid items-center gap-2 px-3 py-3 md:grid-cols-[minmax(12rem,1fr)_minmax(11rem,0.7fr)_auto_auto]">
                <div className="min-w-0">
                  <p className="truncate text-sm font-medium text-foreground" title={item.name}>{item.name}</p>
                  <p className="truncate text-xs text-muted-foreground" title={wording ?? undefined}>{wording || "No value stated"}</p>
                  <p className="truncate text-2xs text-muted-foreground">
                    {setting.claim_scope_codes.length > 0
                      ? setting.claim_scope_codes
                          .map((code) => scopeLabels.get(code) ?? code)
                          .join(" · ")
                      : "No claim type mapped"}
                  </p>
                </div>
                <p className="text-xs text-foreground">
                  {CLAIM_LIMIT_BASIS_LABELS[setting.basis]}
                  {setting.amount != null ? ` · SGD ${setting.amount.toLocaleString()}` : ""}
                </p>
                <Badge variant={sourceChanged ? "warn" : statusVariant(setting)}>
                  {sourceChanged ? "SoB changed · review" : statusLabel(setting)}
                </Badge>
                <Button
                  type="button"
                  size="sm"
                  variant="outline"
                  onClick={() => setEditing(editing === item.uid ? null : item.uid)}
                >
                  {setting.status === "needs_review"
                    ? "Review annual balance"
                    : "Edit annual balance"}
                </Button>
              </div>
              {editing === item.uid && (
                <SettingEditor
                  setting={setting}
                  wording={wording}
                  scopes={benefitScopes.scopes}
                  recommendedScopeCodes={benefitScopes.recommendedScopeCodes}
                  sourceChanged={sourceChanged}
                  onChange={(next) => setItem(item.uid, next)}
                  onScope={(scope, checked) => assignScope(item.uid, scope, checked)}
                  onUseSource={() =>
                    setItem(
                      item.uid,
                      draftDetectedLimitSetting(
                        source,
                        setting.claim_scope_codes,
                      ),
                    )
                  }
                />
              )}
            </div>
          );
        })}
      </div>

      {detectedAnnualAvailable.length > 0 && (
        <div className="border-t border-border bg-warn/5 px-3 py-3">
          <div className="mb-2">
            <p className="text-xs font-medium text-foreground">
              Annual amounts to review
            </p>
            <p className="text-2xs leading-5 text-muted-foreground">
              These SGD policy-year amounts remain offline until their amount
              and claim routing are confirmed.
            </p>
          </div>
          <div className="divide-y divide-border/70">
            {detectedAnnualAvailable.map(({ item, wording, setting }) => (
              <div
                key={item.uid}
                className="grid items-center gap-2 py-2 first:pt-0 last:pb-0 sm:grid-cols-[minmax(12rem,1fr)_auto_auto]"
              >
                <div className="min-w-0">
                  <p className="truncate text-xs font-medium text-foreground">
                    {item.name || `Line ${item.number}`}
                  </p>
                  <p className="truncate text-2xs text-muted-foreground">
                    {wording} · No claim type mapped
                  </p>
                </div>
                <Badge variant="warn">Needs review · not live</Badge>
                <Button
                  type="button"
                  size="sm"
                  variant="outline"
                  onClick={() => reviewDetectedLine(item, setting)}
                >
                  Review annual balance
                </Button>
              </div>
            ))}
          </div>
        </div>
      )}

      {policyRows.length > 0 && (
        <div className="border-t border-border">
          <div className="bg-muted/20 px-3 py-3">
            <p className="text-xs font-medium text-foreground">Assessment guidance</p>
            <p className="mt-1 text-2xs leading-5 text-muted-foreground">
              These conditions help assessors review a claim but do not create
              a remaining balance or block approval. Edit the wording in the
              benefit table above.
            </p>
          </div>
          <div className="divide-y divide-border">
            {policyRows.map(({ item, rules, configuredEntry }) => {
              const policySetting =
                configuredEntry && !isAnnualBalance(configuredEntry.setting)
                  ? configuredEntry
                  : null;
              const benefitScopes = policySetting
                ? claimScopesForBenefit(
                    productCode,
                    item,
                    policySetting.setting,
                    claimScopes,
                  )
                : null;
              const mappingNeedsReview = Boolean(
                policySetting &&
                  (policySetting.sourceChanged ||
                    policySetting.setting.status === "needs_review"),
              );
              return (
                <div key={item.uid}>
                  <div className="grid gap-2 px-3 py-3 md:grid-cols-[minmax(12rem,0.7fr)_minmax(18rem,1.3fr)_auto_auto] md:items-center">
                    <p className="text-sm font-medium text-foreground">
                      {item.name || `Line ${item.number}`}
                    </p>
                    <div className="flex flex-wrap gap-x-3 gap-y-1 text-xs">
                      {rules.map((rule) => (
                        <span key={`${rule.label}-${rule.value}`} className="text-muted-foreground">
                          {rule.label} · <span className="text-foreground">{rule.value}</span>
                        </span>
                      ))}
                    </div>
                    <Badge variant={mappingNeedsReview ? "warn" : "default"}>
                      {!policySetting
                        ? "Guidance · non-blocking"
                        : mappingNeedsReview
                          ? "Claim mapping needs review"
                          : "Guidance mapping confirmed"}
                    </Badge>
                    {policySetting ? (
                      <Button
                        type="button"
                        size="sm"
                        variant="outline"
                        onClick={() =>
                          setEditing(editing === item.uid ? null : item.uid)
                        }
                      >
                        {mappingNeedsReview
                          ? "Review claim mapping"
                          : "Edit claim mapping"}
                      </Button>
                    ) : (
                      <span aria-hidden />
                    )}
                  </div>
                  {policySetting && benefitScopes && editing === item.uid && (
                    <GuidanceRoutingEditor
                      setting={policySetting.setting}
                      wording={policySetting.source.wording}
                      scopes={benefitScopes.scopes}
                      recommendedScopeCodes={benefitScopes.recommendedScopeCodes}
                      sourceChanged={policySetting.sourceChanged}
                      onScope={(scope, checked) =>
                        assignScope(item.uid, scope, checked)
                      }
                      onUseSource={() =>
                        setItem(
                          item.uid,
                          draftDetectedLimitSetting(
                            policySetting.source,
                            policySetting.setting.claim_scope_codes,
                          ),
                        )
                      }
                      onConfirm={() => {
                        setItem(item.uid, {
                          ...policySetting.setting,
                          amount: null,
                          display:
                            policySetting.source.wording?.trim() ||
                            policySetting.setting.display,
                          source: "manual",
                          status: "not_limit",
                        });
                        setEditing(null);
                      }}
                      onNoMapping={() => {
                        setItem(item.uid, {
                          ...policySetting.setting,
                          amount: null,
                          display:
                            policySetting.source.wording?.trim() ||
                            policySetting.setting.display,
                          claim_scope_codes: [],
                          source: "manual",
                          status: "not_limit",
                        });
                        setEditing(null);
                      }}
                    />
                  )}
                </div>
              );
            })}
          </div>
        </div>
      )}

      {manualAvailable.length > 0 && (
        <div className="flex flex-col gap-2 border-t border-border bg-muted/20 px-3 py-3 sm:flex-row sm:items-end">
          <div className="min-w-0 flex-1 space-y-1.5">
            <label className="text-xs font-medium text-foreground" htmlFor="claim-limit-line">
              Add an annual balance
            </label>
            <Select value={addLine} onValueChange={setAddLine}>
              <SelectTrigger id="claim-limit-line">
                <SelectValue placeholder="Choose a benefit line" />
              </SelectTrigger>
              <SelectContent>
                {manualAvailable.map((item) => (
                  <SelectItem key={item.uid} value={item.uid}>
                    {item.name || `Line ${item.number}`}
                    {sourceWordingForPlan(sob, item, activeCode)
                      ? ` · ${sourceWordingForPlan(sob, item, activeCode)}`
                      : ""}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
          <Button type="button" size="sm" variant="outline" disabled={!addLine} onClick={addSelectedLine}>
            <Plus className="size-3.5" aria-hidden /> Add annual balance
          </Button>
        </div>
      )}

      {mappedByScope.size > 0 && (
        <div className="border-t border-border px-3 py-3">
          <p className="text-xs font-medium text-foreground">Claim routing</p>
          <dl className="mt-2 grid gap-x-6 gap-y-2 text-xs sm:grid-cols-2">
            {claimScopes.filter((scope) => mappedByScope.has(scope.code)).map((scope) => {
              const mapped = mappedByScope.get(scope.code);
              return (
                <div key={scope.code} className="flex items-baseline justify-between gap-3">
                  <dt className="text-muted-foreground">{scope.label}</dt>
                  <dd className="text-right font-medium text-foreground">
                    {mapped
                      ? `${mapped.item.name} · ${
                          mapped.sourceChanged
                            ? "pending review, not live"
                            : isLiveAnnualLimit(mapped.setting)
                            ? "live annual balance"
                            : mapped.setting.status === "needs_review"
                              ? "pending review, not live"
                              : "policy wording only"
                        }`
                      : "Not mapped"}
                  </dd>
                </div>
              );
            })}
          </dl>
        </div>
      )}
    </section>
  );
}
