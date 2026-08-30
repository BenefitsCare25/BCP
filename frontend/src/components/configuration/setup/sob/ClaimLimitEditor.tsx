import { useMemo, useState } from "react";
import { AlertTriangle, CheckCircle2, CircleDollarSign, Plus } from "lucide-react";
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
  columnIdForPlan,
  draftLimitSetting,
  itemLimitForPlan,
  isLiveAnnualLimit,
  sourceWordingForPlan,
} from "@/lib/claimLimits";

interface Props {
  sob: SobSchedule;
  plans: PlanAnswer[];
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

function SettingEditor({
  setting,
  wording,
  scopes,
  onChange,
  onScope,
}: {
  setting: ClaimLimitSetting;
  wording: string | null;
  scopes: ClaimLimitScope[];
  onChange: (next: ClaimLimitSetting) => void;
  onScope?: (scopeCode: string, checked: boolean) => void;
}) {
  const patch = (values: Partial<ClaimLimitSetting>) =>
    onChange({ ...setting, ...values, source: "manual", status: "needs_review" });
  const missingRequiredScope = Boolean(
    onScope && scopes.length > 0 && setting.claim_scope_codes.length === 0,
  );
  const missingAnnualAmount =
    setting.basis === "policy_year" && !(setting.amount && setting.amount > 0);

  return (
    <div className="grid gap-4 border-t border-border bg-muted/20 px-3 py-4 lg:grid-cols-[minmax(12rem,0.8fr)_minmax(18rem,1.2fr)]">
      <div className="space-y-3">
        <div className="space-y-1.5">
          <label className="text-xs font-medium text-foreground">Limit basis</label>
          <Select
            value={setting.basis}
            onValueChange={(basis) =>
              patch({
                basis: basis as ClaimLimitBasis,
                amount: basis === "policy_year" ? setting.amount : null,
              })
            }
          >
            <SelectTrigger aria-label="Limit basis">
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
                })
              }
              aria-describedby="claim-limit-enforcement-note"
            />
            <p id="claim-limit-enforcement-note" className="text-2xs text-muted-foreground">
              This is the only basis that creates a remaining balance and approval guard.
            </p>
          </div>
        )}

        {wording && (
          <p className="text-xs leading-5 text-muted-foreground">
            SoB wording: <span className="text-foreground">{wording}</span>
          </p>
        )}
      </div>

      <div className="space-y-3">
        {onScope && scopes.length > 0 && (
          <fieldset className="space-y-2">
            <legend className="text-xs font-medium text-foreground">Applies to claim types</legend>
            <p className="text-2xs text-muted-foreground">
              Select every claim type that should draw from this benefit line.
            </p>
            <div className="grid gap-2 sm:grid-cols-2">
              {scopes.map((scope) => {
                const id = `claim-limit-${scope.code}`;
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
                    <span>{scope.label}</span>
                  </label>
                );
              })}
            </div>
            {missingRequiredScope && (
              <p
                id="claim-limit-scope-note"
                className="text-2xs font-medium text-warn"
              >
                Choose at least one claim type before verifying this setting.
              </p>
            )}
          </fieldset>
        )}

        <div className="flex flex-wrap gap-2">
          <Button
            type="button"
            size="sm"
            onClick={() => onChange({ ...setting, source: "manual", status: "verified" })}
            disabled={missingAnnualAmount || missingRequiredScope}
            aria-describedby={
              missingRequiredScope ? "claim-limit-scope-note" : undefined
            }
          >
            <CheckCircle2 className="size-3.5" aria-hidden />
            Verify setting
          </Button>
          <Button
            type="button"
            size="sm"
            variant="outline"
            onClick={() => onChange({ ...setting, source: "manual", status: "not_limit" })}
          >
            Mark as not a limit
          </Button>
        </div>
        <p className="text-2xs leading-5 text-muted-foreground">
          Verification records the decision in this draft. Select Confirm setup
          or Update setup at the bottom of the page to publish it to employees.
        </p>
      </div>
    </div>
  );
}

export function ClaimLimitEditor({ sob, plans, claimScopes, setSob }: Props) {
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
  const available = sob.items.filter(
    (item) => itemLimitForPlan(sob, item, activeCode) === null,
  );
  const detectedAvailable = available
    .map((item) => {
      const wording = sourceWordingForPlan(sob, item, activeCode);
      return { item, wording, setting: draftLimitSetting(wording) };
    })
    .filter(
      ({ wording, setting }) =>
        Boolean(wording) && setting.basis !== "informational",
    );
  const detectedUids = new Set(detectedAvailable.map(({ item }) => item.uid));
  const manualAvailable = available.filter((item) => !detectedUids.has(item.uid));
  const settings = [overall, ...configured.map((item) => itemLimitForPlan(sob, item, activeCode))]
    .filter((setting): setting is ClaimLimitSetting => Boolean(setting));
  const verified = settings.filter((setting) => setting.status === "verified").length;
  const needsReview = settings.filter((setting) => setting.status === "needs_review").length;
  const totalNeedsReview = needsReview + detectedAvailable.length;
  const scopeLabels = new Map(claimScopes.map((scope) => [scope.code, scope.label]));

  const mappedByScope = new Map<
    string,
    { item: SobItemAnswer; setting: ClaimLimitSetting }
  >();
  for (const item of configured) {
    const setting = itemLimitForPlan(sob, item, activeCode);
    if (!setting) continue;
    for (const scope of setting.claim_scope_codes) {
      mappedByScope.set(scope, { item, setting });
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
    setItem(item.uid, draftLimitSetting(sourceWordingForPlan(sob, item, activeCode)));
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
            Claim limit settings
          </h3>
          <p className="mt-1 max-w-3xl text-xs leading-5 text-muted-foreground">
            Review each detected amount, map the claim types that draw from it,
            then select Verify setting. Only verified policy-year amounts are
            shown to members or enforced during approval.
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
            <CheckCircle2 className="size-3.5" aria-hidden /> {verified} verified
          </span>
        ) : (
          <span className="text-muted-foreground">No verified limits</span>
        )}
        {totalNeedsReview > 0 && (
          <span className="inline-flex items-center gap-1.5 text-warn">
            <AlertTriangle className="size-3.5" aria-hidden /> {totalNeedsReview} need review
          </span>
        )}
        <span className="text-muted-foreground">
          {configured.length} benefit line{configured.length === 1 ? "" : "s"} configured
        </span>
      </div>

      <div className="divide-y divide-border">
        <div>
          <div className="flex flex-wrap items-center gap-3 px-3 py-3">
            <div className="min-w-0 flex-1">
              <p className="text-sm font-medium text-foreground">Overall plan limit</p>
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
              <Badge>No overall limit</Badge>
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
              {overall ? "Edit" : "Set limit"}
            </Button>
          </div>
          {editing === "overall" && overall && (
            <SettingEditor setting={overall} wording={overall.display} scopes={[]} onChange={setOverall} />
          )}
        </div>

        {configured.map((item) => {
          const setting = itemLimitForPlan(sob, item, activeCode)!;
          const wording = sourceWordingForPlan(sob, item, activeCode);
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
                <Badge variant={statusVariant(setting)}>{statusLabel(setting)}</Badge>
                <Button
                  type="button"
                  size="sm"
                  variant="outline"
                  onClick={() => setEditing(editing === item.uid ? null : item.uid)}
                >
                  {setting.status === "needs_review"
                    ? "Review & verify"
                    : "Edit setting"}
                </Button>
              </div>
              {editing === item.uid && (
                <SettingEditor
                  setting={setting}
                  wording={wording}
                  scopes={claimScopes}
                  onChange={(next) => setItem(item.uid, next)}
                  onScope={(scope, checked) => assignScope(item.uid, scope, checked)}
                />
              )}
            </div>
          );
        })}
      </div>

      {detectedAvailable.length > 0 && (
        <div className="border-t border-border bg-warn/5 px-3 py-3">
          <div className="mb-2">
            <p className="text-xs font-medium text-foreground">
              Detected values to review
            </p>
            <p className="text-2xs leading-5 text-muted-foreground">
              These populated limit or policy-condition fields remain offline
              until you review their amount and claim-type mapping.
            </p>
          </div>
          <div className="divide-y divide-border/70">
            {detectedAvailable.map(({ item, wording, setting }) => (
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
                  Review &amp; verify
                </Button>
              </div>
            ))}
          </div>
        </div>
      )}

      {manualAvailable.length > 0 && (
        <div className="flex flex-col gap-2 border-t border-border bg-muted/20 px-3 py-3 sm:flex-row sm:items-end">
          <div className="min-w-0 flex-1 space-y-1.5">
            <label className="text-xs font-medium text-foreground" htmlFor="claim-limit-line">
              Add a benefit line limit
            </label>
            <Select value={addLine} onValueChange={setAddLine}>
              <SelectTrigger id="claim-limit-line">
                <SelectValue placeholder="Choose a Schedule of Benefits line" />
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
            <Plus className="size-3.5" aria-hidden /> Add setting
          </Button>
        </div>
      )}

      {claimScopes.length > 0 && (
        <div className="border-t border-border px-3 py-3">
          <p className="text-xs font-medium text-foreground">Claim type coverage</p>
          <dl className="mt-2 grid gap-x-6 gap-y-2 text-xs sm:grid-cols-2">
            {claimScopes.map((scope) => {
              const mapped = mappedByScope.get(scope.code);
              const overallState = overall
                ? isLiveAnnualLimit(overall)
                  ? "Overall plan · live annual balance"
                  : overall.status === "needs_review"
                    ? "No live balance · overall needs review"
                    : "Overall plan · policy wording only"
                : "No live balance";
              return (
                <div key={scope.code} className="flex items-baseline justify-between gap-3">
                  <dt className="text-muted-foreground">{scope.label}</dt>
                  <dd className="text-right font-medium text-foreground">
                    {mapped
                      ? `${mapped.item.name} · ${
                          isLiveAnnualLimit(mapped.setting)
                            ? "live annual balance"
                            : mapped.setting.status === "needs_review"
                              ? "pending review, not live"
                              : "policy wording only"
                        }`
                      : overallState}
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
