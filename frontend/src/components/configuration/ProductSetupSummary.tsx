import { Badge } from "@/components/ui/badge";
import { fmtDay, fmtMoney } from "@/lib/format";
import { insuredNames } from "@/lib/insured";
import type {
  CategoryGroup,
  ProductSetup,
  ProductTemplate,
  ProductTerm,
  SetupAnswers,
  TemplateField,
} from "@/types";
import { selectedMemberCover } from "./setup/memberEligibility";
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

function countEnabled(answers: SetupAnswers | null): number {
  return Object.values(answers?.arrangements ?? {}).filter(Boolean).length;
}

function selectedPlans(answers: SetupAnswers | null) {
  return (answers?.plans ?? []).filter((plan) => plan.selected);
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

export function ProductSetupSummary({ template, draft, group, term }: Props) {
  const answers = draft?.answers ?? null;
  const plans = selectedPlans(answers);
  const categoryGroups = groupEmployeeCategories(group?.categories ?? []);
  const categoryCount = categoryGroups.length;
  const sobCount = answers?.sob?.items.length ?? 0;
  const arrangements = countEnabled(answers);
  const entities = insuredNames(answers?.header?.entities);
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
      <div className="flex flex-wrap items-center gap-2">
        <Badge variant={draft?.status === "confirmed" ? "good" : "outline"}>
          {draft?.status === "confirmed" ? "Confirmed" : "Draft"}
        </Badge>
        <span className="text-sm text-muted-foreground">
          {plans.length} plan{plans.length === 1 ? "" : "s"} · {categoryCount}{" "}
          employee categor{categoryCount === 1 ? "y" : "ies"} · {sobCount} benefit row
          {sobCount === 1 ? "" : "s"}
        </span>
      </div>

      {!draft && (
        <div className="rounded-lg border border-dashed border-border bg-muted/20 p-4 text-sm text-muted-foreground">
          This product has not been configured yet. Open edit mode to enter the
          setup details and confirm it.
        </div>
      )}

      <section className="space-y-3 border-t border-border pt-4">
        <h4 className="text-sm font-semibold text-foreground">Product Setup</h4>
        <dl className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-4">
          <div>
            <dt className="text-2xs uppercase tracking-wider text-muted-foreground">
              Product code
            </dt>
            <dd className="mt-1 font-mono text-sm font-semibold text-foreground">
              {template.code}
            </dd>
          </div>
          <div>
            <dt className="text-2xs uppercase tracking-wider text-muted-foreground">
              Plans
            </dt>
            <dd className="mt-1 text-sm text-foreground">
              {plans.length
                ? plans.map((plan) => plan.label || plan.code).join(", ")
                : "Not set"}
            </dd>
          </div>
          <div>
            <dt className="text-2xs uppercase tracking-wider text-muted-foreground">
              Entities covered
            </dt>
            <dd className="mt-1 text-sm text-foreground">
              {entities.length ? entities.join(", ") : "All entities"}
            </dd>
          </div>
          <div>
            <dt className="text-2xs uppercase tracking-wider text-muted-foreground">
              Arrangements
            </dt>
            <dd className="mt-1 text-sm text-foreground">
              {arrangements ? `${arrangements} enabled` : "None enabled"}
            </dd>
          </div>
        </dl>
      </section>

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
