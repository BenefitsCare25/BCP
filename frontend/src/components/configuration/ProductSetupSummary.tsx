import { Badge } from "@/components/ui/badge";
import { fmtDay, fmtMoney } from "@/lib/format";
import type {
  CategoryGroup,
  ClaimLimitSetting,
  PlanAnswer,
  ProductSetup,
  ProductTemplate,
  ProductTerm,
  SetupAnswers,
  SobSchedule,
  TemplateField,
} from "@/types";
import { selectedMemberCover } from "./setup/memberEligibility";
import {
  CLAIM_LIMIT_BASIS_LABELS,
  itemLimitForPlan,
  isLiveAnnualLimit,
} from "@/lib/claimLimits";
import {
  groupEmployeeCategories,
  type EmployeeCategoryGroup,
} from "./employeeCategoryGroups";

interface Props {
  template: ProductTemplate;
  draft: ProductSetup | null;
  group?: CategoryGroup;
  term: ProductTerm | null;
}

function textValue(value: unknown): string {
  if (Array.isArray(value)) return value.map(String).filter(Boolean).join(", ");
  const text = String(value ?? "").trim();
  return text || "Not set";
}

function moneyValue(value: number | null | undefined): string {
  return value == null ? "Not set" : fmtMoney(value);
}

function selectedPlans(answers: SetupAnswers | null) {
  return (answers?.plans ?? []).filter((plan) => plan.selected);
}

export function ProductSetupStatus({
  draft,
  group,
}: Pick<Props, "draft" | "group">) {
  const answers = draft?.answers ?? null;
  const plans = selectedPlans(answers);
  const categoryCount = groupEmployeeCategories(group?.categories ?? []).length;
  const benefitRowCount = answers?.sob?.items.length ?? 0;

  return (
    <div className="flex flex-wrap items-center gap-2">
      <Badge variant={draft?.status === "confirmed" ? "good" : "outline"}>
        {draft?.status === "confirmed" ? "Confirmed" : "Draft"}
      </Badge>
      <span className="text-sm text-muted-foreground">
        {plans.length} plan{plans.length === 1 ? "" : "s"} · {categoryCount}{" "}
        employee categor{categoryCount === 1 ? "y" : "ies"} · {benefitRowCount}{" "}
        benefit row{benefitRowCount === 1 ? "" : "s"}
      </span>
    </div>
  );
}

function categoryStatus(group: EmployeeCategoryGroup) {
  if (group.ruleStatus === "validated") {
    return { label: "Rule validated", variant: "good" as const };
  }
  if (group.ruleStatus === "proposed") {
    return {
      label: "Proposed — awaiting employee listing",
      variant: "outline" as const,
    };
  }
  if (group.ruleStatus === "unmapped") {
    return { label: "Rule not set", variant: "error" as const };
  }
  return { label: "Rule needs attention", variant: "warn" as const };
}

function termRows(term: ProductTerm | null): { label: string; value: string }[] {
  if (!term) return [];
  const gst =
    term.gst_included === null
      ? "Exclude"
      : term.gst_included
        ? `Include${term.gst_rate != null ? ` (${term.gst_rate}%)` : ""}`
        : "Exclude";
  const rows: { label: string; value: string }[] = [
    {
      label: "Coverage period",
      value: `${fmtDay(term.coverage_start)} to ${fmtDay(term.coverage_end)}`,
    },
    { label: "GST", value: gst },
  ];
  if (term.line === "life") {
    rows.push(
      { label: "FCL", value: moneyValue(term.free_cover_limit) },
      {
        label: "NEL age",
        value:
          term.nel_age_limit == null ? "Not set" : String(term.nel_age_limit),
      },
    );
  }
  if (term.line === "medical" || term.line === "general") {
    rows.push({
      label: "Underwriting",
      value: term.underwriting_required ? "Yes" : "No",
    });
  }
  if (term.is_inpatient) {
    rows.push({
      label: "Pre / post days",
      value:
        term.pre_hosp_days == null && term.post_hosp_days == null
          ? "Not set"
          : `${term.pre_hosp_days ?? "-"} / ${term.post_hosp_days ?? "-"}`,
    });
  }
  return rows;
}

function FieldList({
  title,
  fields,
  values,
  detailRows,
}: {
  title: string;
  fields: TemplateField[];
  values: Record<string, unknown>;
  detailRows?: { label: string; value: string }[];
}) {
  if (!fields.length) return null;
  return (
    <section className="space-y-2 border-t border-border pt-4">
      <h4 className="text-sm font-semibold text-foreground">{title}</h4>
      {detailRows && <DetailStrip rows={detailRows} />}
      <dl className="grid grid-cols-1 gap-3 md:grid-cols-2">
        {fields.map((field) => (
          <div key={field.id} className="min-w-0">
            <dt className="text-2xs uppercase tracking-wider text-muted-foreground">
              {field.label}
            </dt>
            <dd className="mt-1 whitespace-pre-wrap break-words text-sm text-foreground">
              {textValue(values[field.id])}
            </dd>
          </div>
        ))}
      </dl>
    </section>
  );
}

function DetailStrip({
  rows,
}: {
  rows: { label: string; value: string }[];
}) {
  if (!rows.length) return null;
  return (
    <dl className="grid grid-cols-1 gap-3 rounded-lg border border-border bg-muted/20 p-4 sm:grid-cols-2 lg:grid-cols-4">
      {rows.map((row) => (
        <div key={row.label} className="min-w-0">
          <dt className="text-2xs uppercase tracking-wider text-muted-foreground">
            {row.label}
          </dt>
          <dd className="mt-1 break-words text-sm font-medium text-foreground">
            {row.value}
          </dd>
        </div>
      ))}
    </dl>
  );
}

function LimitValue({ setting }: { setting: ClaimLimitSetting }) {
  return (
    <span className="text-xs text-foreground">
      {setting.status === "not_limit"
        ? "Not a limit"
        : CLAIM_LIMIT_BASIS_LABELS[setting.basis]}
      {setting.amount != null && setting.status !== "not_limit"
        ? ` · SGD ${setting.amount.toLocaleString()}`
        : ""}
    </span>
  );
}

function LimitStatusBadge({ setting }: { setting: ClaimLimitSetting }) {
  return (
    <Badge
      variant={
        setting.status === "verified"
          ? "good"
          : setting.status === "not_limit"
            ? "default"
            : "warn"
      }
    >
      {isLiveAnnualLimit(setting)
        ? "Verified · live"
        : setting.status === "verified"
          ? "Verified · policy wording"
        : setting.status === "not_limit"
          ? "Informational · no balance"
          : "Needs review · not live"}
    </Badge>
  );
}

function ClaimLimitSummary({
  sob,
  plans,
  scopes,
}: {
  sob: SobSchedule | null | undefined;
  plans: PlanAnswer[];
  scopes: ProductTemplate["claim_scopes"];
}) {
  if (!sob) return null;
  const scopeLabels = new Map((scopes ?? []).map((scope) => [scope.code, scope.label]));
  const configuredPlans = plans
    .filter((plan) => plan.selected)
    .map((plan) => ({
      plan,
      overall: sob.plan_claim_limits?.[plan.code] ?? null,
      items: sob.items
        .map((item) => ({ item, setting: itemLimitForPlan(sob, item, plan.code) }))
        .filter(
          (row): row is { item: (typeof sob.items)[number]; setting: ClaimLimitSetting } =>
            row.setting !== null,
        ),
    }));
  const hasSettings = configuredPlans.some(
    ({ overall, items }) => overall !== null || items.length > 0,
  );

  return (
    <section className="space-y-2 border-t border-border pt-4">
      <h4 className="text-sm font-semibold text-foreground">Claim limit settings</h4>
      <p className="text-xs text-muted-foreground">
        Only verified policy-year amounts are live in member balances and the
        approval guard.
      </p>
      {!hasSettings ? (
        <p className="text-sm text-muted-foreground">
          No explicit claim limits have been reviewed for this product.
        </p>
      ) : (
        <div className="divide-y divide-border rounded-lg border border-border">
          {configuredPlans.map(({ plan, overall, items }) => (
            <div key={plan.code} className="space-y-2 px-3 py-3">
              <div className="flex flex-wrap items-center justify-between gap-2">
                <p className="text-sm font-medium text-foreground">
                  {plan.label || plan.code}
                </p>
                {overall && (
                  <span className="inline-flex items-center gap-2">
                    <span className="text-xs text-muted-foreground">Overall</span>
                    <LimitValue setting={overall} />
                    <LimitStatusBadge setting={overall} />
                  </span>
                )}
              </div>
              {items.length > 0 ? (
                <dl className="divide-y divide-border/70">
                  {items.map(({ item, setting }) => (
                    <div
                      key={item.uid}
                      className="grid gap-1 py-2 first:pt-0 last:pb-0 sm:grid-cols-[minmax(12rem,1fr)_auto]"
                    >
                      <div>
                        <dt className="text-xs font-medium text-foreground">{item.name}</dt>
                        <dd className="text-2xs text-muted-foreground">
                          {setting.claim_scope_codes.length
                            ? setting.claim_scope_codes
                                .map((code) => scopeLabels.get(code) ?? code)
                                .join(" · ")
                            : "No claim type mapped"}
                        </dd>
                      </div>
                      <div className="flex items-center gap-2 sm:justify-end">
                        <LimitValue setting={setting} />
                        <LimitStatusBadge setting={setting} />
                      </div>
                    </div>
                  ))}
                </dl>
              ) : (
                <p className="text-xs text-muted-foreground">No benefit line limits.</p>
              )}
            </div>
          ))}
        </div>
      )}
    </section>
  );
}

export function ProductSetupSummary({ template, draft, group, term }: Props) {
  const answers = draft?.answers ?? null;
  const categoryGroups = groupEmployeeCategories(group?.categories ?? []);
  const memberCover = selectedMemberCover(
    answers?.eligibility?.member_cover_eligibility,
  );
  const eligibilityFields = template.eligibility_fields.filter((field) => {
    if (field.id === "age_limit_no_underwriting" && term?.line !== "life") {
      return false;
    }
    if (field.id === "spouse_age_limit") return memberCover.has("Spouse");
    if (field.id === "child_age_limit") return memberCover.has("Child");
    return true;
  });

  return (
    <div className="space-y-5">
      {!draft && (
        <div className="rounded-lg border border-dashed border-border bg-muted/20 p-4 text-sm text-muted-foreground">
          This product has not been configured yet. Open edit mode to enter the
          setup details and confirm it.
        </div>
      )}

      <FieldList
        title="Header & Policy"
        fields={template.header_fields}
        values={answers?.header ?? {}}
        detailRows={termRows(term)}
      />
      <FieldList
        title="Eligibility"
        fields={eligibilityFields}
        values={answers?.eligibility ?? {}}
      />

      <section className="space-y-2 border-t border-border pt-4">
        <h4 className="text-sm font-semibold text-foreground">Cover</h4>
        <p className="whitespace-pre-wrap break-words text-sm text-foreground">
          {textValue(answers?.cover_description)}
        </p>
      </section>

      <ClaimLimitSummary
        sob={answers?.sob}
        plans={answers?.plans ?? []}
        scopes={template.claim_scopes}
      />

      <section className="space-y-2 border-t border-border pt-4">
        <h4 className="text-sm font-semibold text-foreground">
          Employee Category & Plan Type
        </h4>
        {categoryGroups.length ? (
          <div className="divide-y divide-border rounded-lg border border-border">
            {categoryGroups.slice(0, 5).map((categoryGroup) => {
              const status = categoryStatus(categoryGroup);
              return (
                <div
                  key={categoryGroup.key}
                  className="flex flex-wrap items-center justify-between gap-2 px-3 py-2.5"
                >
                  <span className="min-w-0 break-words text-sm text-foreground">
                    {categoryGroup.name}
                  </span>
                  <Badge variant={status.variant}>{status.label}</Badge>
                </div>
              );
            })}
            {categoryGroups.length > 5 && (
              <div className="px-3 py-2 text-xs text-muted-foreground">
                {categoryGroups.length - 5} more employee categories
              </div>
            )}
          </div>
        ) : (
          <p className="text-sm text-muted-foreground">No categories configured.</p>
        )}
      </section>
    </div>
  );
}
