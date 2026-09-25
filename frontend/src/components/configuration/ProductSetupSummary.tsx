import { Badge } from "@/components/ui/badge";
import { useCategoryOverlaps, useMemberCounts } from "@/api/hooks";
import { UnmatchedEmployeeNotice } from "./UnmatchedEmployeeNotice";
import { fmtDay, fmtMoney } from "@/lib/format";
import { insuredNames } from "@/lib/insured";
import type {
  CategoryGroup,
  ClaimLimitSetting,
  ProductSetup,
  ProductTemplate,
  ProductTerm,
  SetupAnswers,
  SobSchedule,
  TemplateField,
} from "@/types";
import { selectedMemberCover } from "./setup/memberEligibility";
import { claimLimitSourceForColumn, describeLimit } from "@/lib/claimLimits";
import { LimitChip } from "./setup/limits/LimitChip";
import {
  groupEmployeeCategories,
  onlySavedOverlapWarnings,
  type EmployeeCategoryGroup,
} from "./employeeCategoryGroups";

interface Props {
  policyYearId: string;
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

function categoryStatus(
  group: EmployeeCategoryGroup,
  overlapCount: number | null,
): { label: string; variant: "warn" | "info" | "good" | "outline" | "error"; detail?: string } {
  const headcountWarning = group.categories
    .flatMap((category) => {
      const warnings = category.rule_validation?.warnings;
      return Array.isArray(warnings) ? warnings.map(String) : [];
    })
    .find((message) => /^Matched \d+ employees; placement slip states \d+$/i.test(message));
  if (overlapCount && overlapCount > 0) {
    return {
      label: `${overlapCount} overlapping employee${overlapCount === 1 ? "" : "s"}`,
      variant: "warn" as const,
      detail: "Open the category mapping to review the conflicting rules",
    };
  }
  if (onlySavedOverlapWarnings(group)) {
    return {
      label: "Review overlap warning",
      variant: "warn",
      detail: "Review both category rules before confirming",
    };
  }
  if (group.ruleStatus === "validated") {
    return group.categories.every((category) => category.status === "confirmed")
      ? {
          label: "Mapping confirmed",
          variant: "good" as const,
          detail: headcountWarning,
        }
      : {
          label: "Rule checks passed",
          variant: "info" as const,
          detail: headcountWarning
            ? `${headcountWarning} · Broker confirmation pending`
            : "Broker confirmation pending",
        };
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
  const issue = group.categories.flatMap((category) => {
    const validation = category.rule_validation;
    const errors = Array.isArray(validation?.errors) ? validation.errors.map(String) : [];
    const unresolved = Array.isArray(validation?.unresolved_clauses)
      ? validation.unresolved_clauses.map(String)
      : [];
    const warnings = Array.isArray(validation?.warnings)
      ? validation.warnings.map(String)
      : [];
    return [
      ...errors,
      ...unresolved,
      ...warnings.filter((message) =>
        !/^Configured value .+ has no active employees in /i.test(message) &&
        !/^Matched \d+ employees; placement slip states \d+$/i.test(message),
      ),
    ];
  })[0];
  return {
    label: "Rule needs attention",
    variant: "warn" as const,
    detail: issue || "Open the category mapping to review the rule",
  };
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

/** Read-only mirror of the Claim limits editor grid: benefit rows × plan
 * columns, one chip per cell, same states and wording as the editor. */
function ClaimLimitSummary({
  sob,
  scopes,
}: {
  sob: SobSchedule | null | undefined;
  scopes: ProductTemplate["claim_scopes"];
}) {
  if (!sob || sob.columns.length === 0) return null;
  const columns = sob.columns;
  const scopeLabels = new Map((scopes ?? []).map((scope) => [scope.code, scope.label]));
  const overallFor = (columnId: string): ClaimLimitSetting | null => {
    const code = columns.find((c) => c.id === columnId)?.plan_codes[0];
    return code ? sob.plan_claim_limits?.[code] ?? null : null;
  };
  const rows = sob.items.filter((item) => columns.some((c) => item.claim_limits?.[c.id]));
  const hasOverall = columns.some((c) => overallFor(c.id));

  const tally = { live: 0, review: 0 };
  const count = (tone: string) => {
    if (tone === "live") tally.live += 1;
    if (tone === "review") tally.review += 1;
  };
  for (const col of columns) {
    const overall = overallFor(col.id);
    if (overall) count(describeLimit(overall, undefined).tone);
    for (const item of rows) {
      const setting = item.claim_limits?.[col.id];
      if (setting) count(describeLimit(setting, claimLimitSourceForColumn(item, col.id).wording).tone);
    }
  }

  return (
    <section className="space-y-2 border-t border-border pt-4">
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <h4 className="text-sm font-semibold text-foreground">Claim limits</h4>
        {(rows.length > 0 || hasOverall) && (
          <p className="flex flex-wrap gap-3 text-xs">
            <span className="text-good">{tally.live} tracked</span>
            {tally.review > 0 && <span className="text-warn">{tally.review} to review</span>}
          </p>
        )}
      </div>
      <p className="text-xs text-muted-foreground">
        <span className="font-medium text-foreground">Tracked</span> limits count down on the
        employee&apos;s &ldquo;What&apos;s left&rdquo; and guard claim approval. Everything else
        shows as a condition. To change them, click Edit and open Claim limits.
      </p>
      {rows.length === 0 && !hasOverall ? (
        <p className="text-sm text-muted-foreground">No claim limits set for this product yet.</p>
      ) : (
        <div className="overflow-x-auto rounded-md border border-border">
          <table className="w-full border-collapse text-sm">
            <thead className="bg-muted">
              <tr className="border-b border-border">
                <th className="min-w-40 px-3 py-1.5 text-left text-2xs uppercase tracking-wider text-muted-foreground sm:min-w-56">
                  Benefit
                </th>
                {columns.map((col) => (
                  <th key={col.id} className="min-w-44 px-2 py-1.5 text-left text-2xs uppercase tracking-wider text-muted-foreground">
                    <span className="block truncate" title={col.label}>{col.label}</span>
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {hasOverall && (
                <tr className="border-b border-border">
                  <td className="px-3 py-2 text-sm font-medium text-foreground">Overall yearly limit</td>
                  {columns.map((col) => {
                    const setting = overallFor(col.id);
                    const { text, tone } = setting
                      ? describeLimit(setting, undefined)
                      : { text: "None", tone: "none" as const };
                    return (
                      <td key={col.id} className="px-2 py-2">
                        <LimitChip text={text} tone={tone} />
                      </td>
                    );
                  })}
                </tr>
              )}
              {rows.map((item) => (
                <tr key={item.uid} className="border-b border-border last:border-0">
                  <td className="px-3 py-2">
                    <span className="block max-w-72 truncate text-sm text-foreground" title={item.name}>
                      {item.name}
                    </span>
                  </td>
                  {columns.map((col) => {
                    const setting = item.claim_limits?.[col.id] ?? null;
                    const wording = claimLimitSourceForColumn(item, col.id).wording;
                    const { text, tone } = describeLimit(setting, wording);
                    const sub = (setting?.claim_scope_codes ?? [])
                      .map((code) => scopeLabels.get(code) ?? code)
                      .join(" · ");
                    return (
                      <td key={col.id} className="px-2 py-2 align-top">
                        <LimitChip
                          text={setting?.status === "not_limit" ? "Not a limit" : text}
                          tone={setting ? tone : "none"}
                          sub={setting ? sub || "No claim type" : undefined}
                          unset={!setting}
                        />
                      </td>
                    );
                  })}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}

export function ProductSetupSummary({ policyYearId, template, draft, group, term }: Props) {
  const answers = draft?.answers ?? null;
  const categoryGroups = groupEmployeeCategories(group?.categories ?? []);
  const overlapQuery = useCategoryOverlaps(policyYearId);
  const memberCountsQuery = useMemberCounts(
    policyYearId,
    template.code,
    template.has_dependants,
    categoryGroups.map((categoryGroup) => ({
      key: categoryGroup.key,
      description: categoryGroup.representative.raw_description || categoryGroup.name,
      insured: insuredNames(categoryGroup.representative.plan_assignments?.insured),
    })),
  );
  const overlapsByCategory = new Map(
    (overlapQuery.data ?? []).map((item) => [item.category_id, item.employees]),
  );
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
        scopes={template.claim_scopes}
      />

      <section className="space-y-2 border-t border-border pt-4">
        <h4 className="text-sm font-semibold text-foreground">
          Employee Category & Plan Type
        </h4>
        <UnmatchedEmployeeNotice productCode={template.code} counts={memberCountsQuery.data} />
        {memberCountsQuery.isError && (
          <p role="alert" className="text-xs text-warn">
            Employee coverage counts are unavailable. Refresh to check the listing.
          </p>
        )}
        {categoryGroups.length ? (
          <div className="divide-y divide-border rounded-lg border border-border">
            {categoryGroups.slice(0, 5).map((categoryGroup) => {
              const overlappingIds = new Set(
                categoryGroup.categories.flatMap((category) =>
                  (overlapsByCategory.get(category.id) ?? []).map((employee) => employee.employee_id),
                ),
              );
              const status = categoryStatus(
                categoryGroup,
                overlapQuery.isSuccess ? overlappingIds.size : null,
              );
              return (
                <div
                  key={categoryGroup.key}
                  className="flex flex-wrap items-center justify-between gap-2 px-3 py-2.5"
                >
                  <span className="min-w-0 break-words text-sm text-foreground">
                    {categoryGroup.name}
                  </span>
                  <div className="flex flex-col items-end gap-1 text-right">
                    <Badge variant={status.variant}>{status.label}</Badge>
                    {status.detail && (
                      <span className="text-xs text-muted-foreground">{status.detail}</span>
                    )}
                  </div>
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
