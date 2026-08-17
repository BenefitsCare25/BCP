import { useEffect, useRef } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { Loader2, RefreshCw } from "lucide-react";
import { useClaimReview, type FieldComparison, type RuleResult } from "@/api/claims";
import { FieldComparisonTable } from "@/components/claims/FieldComparisonTable";
import {
  compactRuleResults,
  RuleResultsList,
} from "@/components/claims/RuleResultsList";
import { Button } from "@/components/ui/button";
import { SectionLabel } from "@/components/ui/section-label";
import { InfoHint } from "@/components/ui/tooltip";
import { formatError } from "@/lib/errors";

function Section({
  title,
  hint,
  children,
}: {
  title: string;
  hint?: React.ReactNode;
  children: React.ReactNode;
}) {
  return (
    <div>
      <div className="mb-2 flex items-center gap-1">
        <SectionLabel>{title}</SectionLabel>
        {hint ? <InfoHint>{hint}</InfoHint> : null}
      </div>
      {children}
    </div>
  );
}

const STAGE_LABELS: Record<string, string> = {
  queued: "Waiting for a review worker",
  deterministic: "Running system checks",
  extraction: "Reading claim documents",
  comparison: "Comparing claim details",
  vision: "Verifying uncertain fields",
  verdict: "Preparing the recommendation",
  persist: "Saving the review",
};

/** The AI review of one claim — verdict banner, summary, field comparisons,
 * rule results, vision checks. Broker-only (members never see fraud signals). */
const FIELD_LABELS: Record<string, string> = {
  amount_claimed: "Amount claimed",
  incurred_date: "Incurred date",
  provider_name: "Provider",
  invoice_number: "Invoice number",
  currency: "Currency",
  diagnosis: "Diagnosis",
};

function fieldLabel(name: string): string {
  return FIELD_LABELS[name] ?? name.replace(/_/g, " ");
}

type AttentionItem = {
  title: string;
  detail: string;
  action?: string;
};

function attentionItems(
  comparisons: FieldComparison[],
  rules: RuleResult[],
): AttentionItem[] {
  const items: AttentionItem[] = [];
  for (const result of rules) {
    if (result.error_code !== "ai_output_incomplete") continue;
    const fields = result.affected_fields?.map(fieldLabel).join(", ");
    items.push({
      title: "AI comparison output incomplete",
      detail: fields
        ? `The AI did not return usable comparison results for: ${fields}.`
        : result.evidence || "The AI response missed one or more configured outputs.",
      action:
        "Re-run AI review. If it repeats, check the claim-type field mappings and prompt keys.",
    });
  }
  const hasIncompleteComparison = rules.some(
    (result) =>
      result.error_code === "ai_output_incomplete" &&
      result.rule.toLowerCase().includes("comparison"),
  );
  for (const comparison of comparisons) {
    if (comparison.status === "MATCH") continue;
    if (
      hasIncompleteComparison &&
      comparison.status === "UNCERTAIN" &&
      comparison.notes === "The AI response omitted this configured comparison."
    ) {
      continue;
    }
    items.push({
      title: `${fieldLabel(comparison.field_name)} needs review`,
      detail:
        comparison.notes ||
        `Comparison result: ${comparison.status.replace(/_/g, " ").toLowerCase()}.`,
      action: "Compare the claim value against the uploaded document value.",
    });
  }
  for (const result of rules) {
    if (
      result.error_code === "ai_output_incomplete" ||
      (result.status !== "fail" && result.status !== "warning")
    ) {
      continue;
    }
    items.push({
      title: result.rule,
      detail: result.evidence,
      action:
        result.status === "fail"
          ? "Resolve before approving or record a manual justification."
          : "Review before making the final decision.",
    });
  }
  const seen = new Set<string>();
  return items
    .filter((item) => {
      const key = `${item.title}|${item.detail}`;
      if (seen.has(key)) return false;
      seen.add(key);
      return true;
    })
    .slice(0, 5);
}

function ReviewDetails({
  label,
  children,
}: {
  label: React.ReactNode;
  children: React.ReactNode;
}) {
  return (
    <details className="rounded-md border border-border bg-card">
      <summary className="cursor-pointer list-none px-3 py-2 text-sm font-medium text-foreground hover:bg-muted focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring/40">
        {label}
      </summary>
      <div className="border-t border-border p-3">{children}</div>
    </details>
  );
}

export function ClaimReviewPanel({
  claimId,
  claimStatus,
}: {
  claimId: string;
  /** Current claim status — while it's `ai_review_pending` the panel polls
   * for the finished review instead of asking the broker to refresh. */
  claimStatus?: string;
}) {
  const qc = useQueryClient();
  const previousStatus = useRef<string | null>(null);
  const {
    data: review,
    isLoading,
    isError,
    error,
    refetch,
  } = useClaimReview(claimId, claimStatus === "ai_review_pending");

  useEffect(() => {
    const status = review?.status ?? null;
    const wasActive = previousStatus.current &&
      ["queued", "running", "retry_wait"].includes(previousStatus.current);
    const isTerminal = status && ["complete", "error", "cancelled"].includes(status);
    if (wasActive && isTerminal) {
      void qc.invalidateQueries({ queryKey: ["claims"] });
      void qc.invalidateQueries({ queryKey: ["claim-detail"] });
    }
    previousStatus.current = status;
  }, [qc, review?.status]);

  if (isLoading) {
    return (
      <div className="flex items-center gap-2 text-sm text-muted-foreground p-4">
        <Loader2 className="size-4 animate-spin" /> Loading AI review…
      </div>
    );
  }
  if (isError) {
    return (
      <div className="flex items-center justify-between gap-3 text-sm p-4 border border-border rounded-md bg-warn-soft text-warn">
        <span>Couldn't load the AI review — {formatError(error)}</span>
        <Button size="sm" variant="outline" onClick={() => void refetch()}>
          <RefreshCw className="size-4" /> Retry
        </Button>
      </div>
    );
  }
  if (!review) {
    if (claimStatus === "ai_review_pending") {
      return (
        <div className="flex items-center gap-2 text-sm text-muted-foreground p-4 border border-border rounded-md bg-muted">
          <Loader2 className="size-4 animate-spin" /> Creating the durable review job…
        </div>
      );
    }
    return (
      <div className="text-sm text-muted-foreground p-4 text-center border border-dashed border-border rounded-md">
        No AI review yet.
      </div>
    );
  }
  if (["queued", "running", "retry_wait"].includes(review.status)) {
    const elapsed = review.started_at
      ? Math.max(0, Math.floor((Date.now() - new Date(review.started_at).getTime()) / 1_000))
      : null;
    const progress = review.progress_total > 0
      ? ` · ${Math.min(review.progress_current, review.progress_total)}/${review.progress_total} documents`
      : "";
    return (
      <div className="flex items-start gap-2 text-sm text-muted-foreground p-4 border border-border rounded-md bg-muted">
        <Loader2 className="size-4 animate-spin mt-0.5" />
        <div>
          <div className="font-medium text-foreground">
            {review.status === "retry_wait"
              ? "Review interrupted — retrying automatically"
              : STAGE_LABELS[review.stage] ?? "AI review is running"}
          </div>
          <div className="text-xs mt-1">
            Attempt {Math.max(review.attempt, 1)}{progress}
            {elapsed != null ? ` · ${elapsed}s elapsed` : ""}. This panel refreshes automatically.
          </div>
        </div>
      </div>
    );
  }
  if (review.status === "error" || review.status === "cancelled") {
    const systemChecks = compactRuleResults(review.rule_results ?? []);
    return (
      <div className="space-y-4">
        <div className="text-sm p-3 border border-border rounded-md bg-card text-foreground">
          <div>
            <div className="font-medium">
              {review.status === "cancelled"
                ? "AI review was cancelled because the claim changed."
                : "AI review did not complete — review manually."}
            </div>
            {review.error_code && (
              <div className="text-xs mt-0.5 opacity-80">Code: {review.error_code}</div>
            )}
            {review.error_detail && (
              <div className="text-xs mt-0.5 opacity-80">{review.error_detail}</div>
            )}
          </div>
        </div>
        {systemChecks.length > 0 && (
          <ReviewDetails label="System checks completed before failure">
            <RuleResultsList results={systemChecks} />
          </ReviewDetails>
        )}
      </div>
    );
  }

  const comparisons = review.field_comparisons ?? [];
  const ruleResults = compactRuleResults(review.rule_results ?? []);
  const visibleRuleResults = ruleResults.filter(
    (result) => result.error_code !== "ai_output_incomplete",
  );
  const matchingFields = comparisons.filter((c) => c.status === "MATCH").length;
  const fieldIssues = comparisons.length - matchingFields;
  const issues = attentionItems(comparisons, ruleResults);
  const hasIncompleteComparison = ruleResults.some(
    (result) =>
      result.error_code === "ai_output_incomplete" &&
      result.rule.toLowerCase().includes("comparison"),
  );
  return (
    <div className="space-y-4">
      <div>
        {issues.length > 0 ? (
          <ol className="space-y-2 rounded-md border border-border bg-card p-3">
            {issues.map((item, index) => (
              <li
                key={`${item.title}-${item.detail}`}
                className="flex gap-2 text-sm text-foreground"
              >
                <span className="mt-0.5 flex size-5 shrink-0 items-center justify-center rounded-full bg-muted text-2xs font-medium text-muted-foreground">
                  {index + 1}
                </span>
                <span>
                  <span className="font-medium">{item.title}</span>
                  <span className="block text-xs text-muted-foreground">
                    {item.detail}
                  </span>
                  {item.action && (
                    <span className="mt-1 block text-xs text-foreground">
                      Recommended action: {item.action}
                    </span>
                  )}
                </span>
              </li>
            ))}
          </ol>
        ) : (
          <div className="rounded-md border border-border bg-good-soft p-3 text-sm text-good">
            No validation issues found in the AI review.
          </div>
        )}
      </div>

      <ReviewDetails
        label={`${fieldIssues} field issue${fieldIssues === 1 ? "" : "s"} · ${matchingFields} matched`}
      >
        <FieldComparisonTable
          comparisons={comparisons}
          hideOmittedNotes={hasIncompleteComparison}
        />
      </ReviewDetails>

      {visibleRuleResults.length > 0 && (
        <Section
          title="Rule review"
          hint="Deterministic system checks plus AI rules. Passed checks are retained for audit."
        >
          <RuleResultsList results={visibleRuleResults} />
        </Section>
      )}

      <div className="border-t border-border pt-3 text-2xs text-subtle">
        {review.model && <>Model {review.model} · </>}
        {(review.input_tokens ?? 0) + (review.output_tokens ?? 0)} tokens
        {review.cost_estimate_usd != null && (
          <> · est. ${review.cost_estimate_usd.toFixed(4)}</>
        )}
        {" · "}
        {new Date(review.created_at).toLocaleString()}
      </div>
    </div>
  );
}
