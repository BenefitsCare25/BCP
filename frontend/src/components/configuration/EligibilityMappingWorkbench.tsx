import {
  AlertTriangle,
  CheckCircle2,
  Loader2,
  RefreshCw,
  Sparkles,
} from "lucide-react";
import { useEffect, useState } from "react";
import {
  useAICreateMissingCategory,
  useAIStatus,
  useEligibilityMappings,
  useProposeEligibilityMappings,
} from "@/api/hooks";
import { Badge, type BadgeProps } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { formatError } from "@/lib/errors";
import type {
  EligibilityMappingItem,
  EligibilityRuleStatus,
} from "@/types";
import { toast } from "sonner";

interface Props {
  policyYearId: string;
  readOnly?: boolean;
  onReview: (categoryId: string) => void;
}

const STATUS_LABEL: Record<EligibilityRuleStatus, string> = {
  validated: "Roster validated",
  proposed: "Proposal",
  needs_review: "Needs review",
  unmapped: "Unmapped",
};

function statusVariant(status: EligibilityRuleStatus): BadgeProps["variant"] {
  if (status === "validated") return "good";
  if (status === "proposed") return "info";
  if (status === "needs_review") return "warn";
  return "error";
}

function sourceLabel(source: string): string {
  return {
    deterministic: "Exact wording",
    product_context: "Product context",
    roster_values: "Company roster",
    prior_mapping: "Confirmed company mapping",
    prior_year: "Prior policy year",
    manual: "Broker edited",
    ai_extracted: "AI + company roster",
    unmapped: "No mapping",
  }[source] ?? source.replaceAll("_", " ");
}

function countLabel(item: EligibilityMappingItem): string | null {
  if (item.matched_count == null && item.expected_count == null) return null;
  if (item.matched_count == null) {
    return `${item.expected_count} expected on slip`;
  }
  if (item.expected_count == null) {
    return `${item.matched_count} roster match${item.matched_count === 1 ? "" : "es"}`;
  }
  return `${item.matched_count} matched / ${item.expected_count} expected`;
}

export function EligibilityMappingWorkbench({
  policyYearId,
  readOnly = false,
  onReview,
}: Props) {
  const { data, isLoading, error } = useEligibilityMappings(policyYearId);
  const propose = useProposeEligibilityMappings();
  const createMissing = useAICreateMissingCategory();
  const { data: aiStatus } = useAIStatus();
  const [eligibilityText, setEligibilityText] = useState<Record<string, string>>({});
  const [issuesOnly, setIssuesOnly] = useState(true);

  useEffect(() => {
    setEligibilityText({});
    setIssuesOnly(true);
  }, [policyYearId]);

  if (isLoading) return null;
  if (error) {
    return (
      <Card>
        <CardContent className="flex items-start gap-2 p-4 text-sm text-error">
          <AlertTriangle className="mt-0.5 size-4 shrink-0" />
          Could not load employee-category mappings: {formatError(error)}
        </CardContent>
      </Card>
    );
  }
  if (!data || (data.total === 0 && data.missing_categories === 0)) return null;

  const issueCount = data.categories.filter(
    (item) => item.rule_status === "needs_review" || item.rule_status === "unmapped",
  ).length;
  const effectiveIssuesOnly = issuesOnly && issueCount > 0;
  const visibleCategories = effectiveIssuesOnly
    ? data.categories.filter(
        (item) =>
          item.rule_status === "needs_review" || item.rule_status === "unmapped",
      )
    : data.categories;
  const byProduct = new Map<string, EligibilityMappingItem[]>();
  for (const item of visibleCategories) {
    const code = item.product_code ?? "Unassigned";
    byProduct.set(code, [...(byProduct.get(code) ?? []), item]);
  }

  const refresh = async () => {
    try {
      const result = await propose.mutateAsync(policyYearId);
      toast.success("Employee-category proposals refreshed", {
        description: `${result.validated} roster validated · ${result.needs_review} need review · ${result.unmapped} unmapped`,
      });
    } catch (reason) {
      toast.error(formatError(reason));
    }
  };

  const createMissingCategory = async (planId: string) => {
    const description = (eligibilityText[planId] ?? "").trim();
    if (!description) {
      toast.error("Enter the authoritative employee eligibility wording first");
      return;
    }
    try {
      const category = await createMissing.mutateAsync({
        policyYearId,
        planId,
        eligibilityDescription: description,
      });
      toast.success("Employee category created for review", {
        description: "AI compiled the wording against the company roster; broker confirmation is still required.",
      });
      onReview(category.id);
    } catch (reason) {
      toast.error(formatError(reason));
    }
  };

  return (
    <Card>
      <CardHeader className="gap-3 sm:flex-row sm:items-start sm:justify-between">
        <div className="space-y-1">
          <CardTitle className="text-base">Employee category matching</CardTitle>
          <CardDescription>
            Rules are compiled from this company&apos;s employee fields and actual
            roster values. Confirmed mappings are reused only within this
            company.
          </CardDescription>
        </div>
        <Button
          size="sm"
          variant="outline"
          onClick={refresh}
          disabled={readOnly || propose.isPending}
        >
          {propose.isPending ? (
            <Loader2 className="size-3.5 animate-spin" />
          ) : (
            <RefreshCw className="size-3.5" />
          )}
          {propose.isPending ? "Rebuilding proposals…" : "Rebuild proposals"}
        </Button>
      </CardHeader>
      <CardContent className="space-y-4">
        {data.employee_count === 0 && (
          <div className="flex items-start gap-2 rounded-md border border-warn/40 bg-warn-soft/40 p-3 text-sm">
            <AlertTriangle className="mt-0.5 size-4 shrink-0 text-warn" />
            <div className="space-y-1">
              <p className="font-medium text-foreground">
                No active employee roster is available
              </p>
              <p className="text-xs text-muted-foreground">
                Proposals can still use slip wording and confirmed company
                mappings, but roster validation and employee match counts remain
                unavailable until an employee listing is uploaded for this
                benefit year.
              </p>
            </div>
          </div>
        )}

        <div className="flex flex-wrap items-center gap-2 text-sm">
          <Badge variant="good">{data.validated} roster validated</Badge>
          <Badge variant="info">{data.proposed} proposed</Badge>
          <Badge variant="warn">{data.needs_review} need review</Badge>
          <Badge variant="error">{data.unmapped} unmapped</Badge>
          {data.not_applicable > 0 && (
            <Badge variant="outline">
              {data.not_applicable} dependant-only excluded
            </Badge>
          )}
          {data.missing_categories > 0 && (
            <Badge variant="error">
              {data.missing_categories} plan{data.missing_categories === 1 ? "" : "s"} missing categories
            </Badge>
          )}
          {data.reused > 0 && (
            <Badge variant="outline">{data.reused} reused</Badge>
          )}
          <span className="ml-auto text-xs text-muted-foreground">
            {data.employee_count} active employee
            {data.employee_count === 1 ? "" : "s"}
          </span>
        </div>

        <div className="flex flex-wrap items-center justify-between gap-2 rounded-md bg-muted/40 px-3 py-2 text-xs">
          <span className="text-muted-foreground">
            {effectiveIssuesOnly
              ? `Showing ${issueCount} categor${issueCount === 1 ? "y" : "ies"} with mapping problems`
              : `Showing all ${data.total} employee categor${data.total === 1 ? "y" : "ies"}`}
          </span>
          {issueCount > 0 && issueCount < data.total && (
            <Button
              size="sm"
              variant="ghost"
              onClick={() => setIssuesOnly((current) => !current)}
            >
              {effectiveIssuesOnly ? `Show all ${data.total}` : `Show ${issueCount} issues only`}
            </Button>
          )}
        </div>

        {data.missing_category_plans.length > 0 && (
          <div className="space-y-3 rounded-lg border border-error/40 bg-error-soft/30 p-3">
            <div className="space-y-1">
              <p className="flex items-center gap-2 text-sm font-medium">
                <AlertTriangle className="size-4 text-error" />
                Plans without an employee category
              </p>
              <p className="text-xs text-muted-foreground">
                A benefit schedule does not reliably say who is eligible. Enter
                the authoritative slip or broker wording; AI will compile it
                against this company&apos;s non-PII roster values and save a
                reviewable category. It will not invent coverage.
              </p>
            </div>
            <div className="space-y-3">
              {data.missing_category_plans.map((plan) => {
                const description = eligibilityText[plan.plan_id] ?? "";
                return (
                  <div
                    key={plan.plan_id}
                    className="space-y-2 rounded-md border border-border bg-background p-3"
                  >
                    <div className="flex flex-wrap items-center gap-2 text-sm">
                      <span className="font-medium">{plan.product_code}</span>
                      <Badge variant="outline">Plan {plan.plan_code}</Badge>
                      <span className="text-muted-foreground">
                        {plan.plan_display_name}
                      </span>
                    </div>
                    {plan.source_hint && (
                      <p className="text-2xs text-muted-foreground">
                        Plan note (context only): {plan.source_hint}
                      </p>
                    )}
                    <div className="flex flex-col gap-2 sm:flex-row">
                      <Input
                        value={description}
                        onChange={(event) =>
                          setEligibilityText((current) => ({
                            ...current,
                            [plan.plan_id]: event.target.value,
                          }))
                        }
                        placeholder="Who is eligible? e.g. Senior Managers / Managers"
                        aria-label={`Eligibility wording for ${plan.plan_display_name}`}
                        disabled={readOnly}
                      />
                      <Button
                        size="sm"
                        variant="outline"
                        onClick={() => createMissingCategory(plan.plan_id)}
                        disabled={
                          readOnly ||
                          !aiStatus?.configured ||
                          createMissing.isPending ||
                          !description.trim()
                        }
                        title={
                          aiStatus?.configured
                            ? "Create a roster-validated category proposal"
                            : "Configure the AI provider first"
                        }
                      >
                        {createMissing.isPending ? (
                          <Loader2 className="size-3.5 animate-spin" />
                        ) : (
                          <Sparkles className="size-3.5" />
                        )}
                        Create with AI
                      </Button>
                    </div>
                  </div>
                );
              })}
            </div>
          </div>
        )}

        <div className="space-y-2">
          {[...byProduct.entries()]
            .sort(([left], [right]) => left.localeCompare(right))
            .map(([productCode, items]) => {
              const productIssueCount = items.filter(
                (item) =>
                  item.rule_status === "needs_review" ||
                  item.rule_status === "unmapped",
              ).length;
              return (
                <details
                  key={productCode}
                  className="rounded-lg border border-border bg-background"
                >
                  <summary className="cursor-pointer select-none px-3 py-2.5 text-sm font-medium">
                    {productCode}
                    <span className="ml-2 text-xs font-normal text-muted-foreground">
                      {items.length} categor{items.length === 1 ? "y" : "ies"}
                      {productIssueCount > 0
                        ? ` · ${productIssueCount} need attention`
                        : ""}
                    </span>
                  </summary>
                  <div className="divide-y divide-border border-t border-border">
                  {items.map((item) => {
                    const count = countLabel(item);
                    const messages = [...item.errors, ...item.warnings];
                    return (
                      <div
                        key={item.category_id}
                        className="flex flex-col gap-3 px-3 py-3 sm:flex-row sm:items-start"
                      >
                        <div className="min-w-0 flex-1 space-y-1.5">
                          <div className="flex flex-wrap items-center gap-2">
                            <span className="font-medium text-sm">
                              {item.display_name}
                            </span>
                            {item.plan_code && (
                              <Badge variant="outline">Plan {item.plan_code}</Badge>
                            )}
                            <Badge variant={statusVariant(item.rule_status)}>
                              {item.rule_status === "validated" && (
                                <CheckCircle2 className="mr-1 size-3" />
                              )}
                              {STATUS_LABEL[item.rule_status]}
                            </Badge>
                            {item.category_status === "confirmed" && (
                              <Badge variant="outline">Confirmed</Badge>
                            )}
                          </div>
                          <p className="text-xs text-muted-foreground">
                            {item.rule_human_readable ??
                              "No employee-field rule could be proposed."}
                          </p>
                          <div className="flex flex-wrap gap-x-3 gap-y-1 text-2xs text-muted-foreground">
                            <span>Source: {sourceLabel(item.source)}</span>
                            {count && <span>{count}</span>}
                          </div>
                          {messages.length > 0 && (
                            <ul className="space-y-0.5 text-xs text-warn">
                              {messages.map((message) => (
                                <li key={message}>• {message}</li>
                              ))}
                            </ul>
                          )}
                        </div>
                        <Button
                          size="sm"
                          variant={
                            item.rule_status === "needs_review" ||
                            item.rule_status === "unmapped"
                              ? "outline"
                              : "ghost"
                          }
                          onClick={() => onReview(item.category_id)}
                        >
                          Review rule
                        </Button>
                      </div>
                    );
                  })}
                  </div>
                </details>
              );
            })}
        </div>
      </CardContent>
    </Card>
  );
}
