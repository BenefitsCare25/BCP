import { useEffect, useRef } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { AlertTriangle, Loader2, RefreshCw, ShieldCheck } from "lucide-react";
import { useClaimReview, type FieldComparison, type RuleResult } from "@/api/claims";
import { FieldComparisonTable } from "@/components/claims/FieldComparisonTable";
import {
  compactRuleResults,
  RuleResultsList,
} from "@/components/claims/RuleResultsList";
import { VisionCheckList } from "@/components/claims/VisionCheckList";
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

function ReviewMetric({
  label,
  value,
  tone = "default",
}: {
  label: string;
  value: React.ReactNode;
  tone?: "default" | "good" | "warn" | "error";
}) {
  const toneClass =
    tone === "good"
      ? "text-good"
      : tone === "warn"
        ? "text-warn"
        : tone === "error"
          ? "text-error"
          : "text-foreground";
  return (
    <div className="rounded-md border border-border bg-card p-3">
      <div className={`text-lg font-semibold tabular-nums ${toneClass}`}>
        {value}
      </div>
      <div className="mt-0.5 text-2xs uppercase tracking-wider text-subtle">
        {label}
      </div>
    </div>
  );
}

function attentionItems(
  comparisons: FieldComparison[],
  rules: RuleResult[],
): string[] {
  const items: string[] = [];
  for (const result of rules) {
    if (result.error_code !== "ai_output_incomplete") continue;
    const fields = result.affected_fields?.map(fieldLabel).join(", ");
    items.push(
      fields
        ? `${result.rule} Affected: ${fields}.`
        : result.evidence || result.rule,
    );
  }
  for (const comparison of comparisons) {
    if (comparison.status === "MATCH") continue;
    items.push(
      `${fieldLabel(comparison.field_name)} is ${comparison.status
        .replace(/_/g, " ")
        .toLowerCase()}: ${comparison.notes || "review the claim and document values."}`,
    );
  }
  for (const result of rules) {
    if (
      result.error_code === "ai_output_incomplete" ||
      (result.status !== "fail" && result.status !== "warning")
    ) {
      continue;
    }
    items.push(`${result.rule}: ${result.evidence}`);
  }
  return [...new Set(items)].slice(0, 6);
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
    return (
      <div className="space-y-3">
        <div className="flex items-start gap-2 text-sm p-3 border border-border rounded-md bg-warn-soft text-warn">
          <AlertTriangle className="size-4 shrink-0 mt-0.5" />
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
        {(review.rule_results?.length ?? 0) > 0 && (
          <Section title="System checks (ran before the failure)">
            <RuleResultsList results={review.rule_results ?? []} />
          </Section>
        )}
      </div>
    );
  }

  const flagged = review.verdict === "flagged";
  const comparisons = review.field_comparisons ?? [];
  const ruleResults = compactRuleResults(review.rule_results ?? []);
  const failedRules = ruleResults.filter((r) => r.status === "fail").length;
  const warningRules = ruleResults.filter((r) => r.status === "warning").length;
  const matchingFields = comparisons.filter((c) => c.status === "MATCH").length;
  const fieldIssues = comparisons.length - matchingFields;
  const issues = attentionItems(comparisons, ruleResults);
  return (
    <div className="space-y-4">
      <div
        className={`flex items-start gap-2 text-sm p-3 rounded-md border ${
          flagged
            ? "border-error/30 bg-error-soft text-error"
            : "border-good/30 bg-good-soft text-good"
        }`}
      >
        {flagged ? (
          <AlertTriangle className="size-4 shrink-0 mt-0.5" />
        ) : (
          <ShieldCheck className="size-4 shrink-0 mt-0.5" />
        )}
        <div className="min-w-0">
          <div className="flex items-center gap-1 font-medium">
            {review.deterministic_short_circuit
              ? "Flagged by system checks — AI document review was not run"
              : flagged
                ? "Flagged for attention"
                : "AI-verified — no concerns found"}
            <InfoHint>
              {flagged
                ? "One or more checks need a human look — the AI found a discrepancy or couldn't confirm a value. You still make the final decision."
                : "The pipeline found no discrepancies against the uploaded documents. This is a recommendation — you still approve or reject."}
            </InfoHint>
            {review.confidence != null && (
              <span className="font-normal opacity-80">
                {" "}
                · confidence {(review.confidence * 100).toFixed(0)}%
              </span>
            )}
          </div>
          {review.summary && (
            <div className="text-xs mt-1 whitespace-pre-line opacity-90">
              {review.summary}
            </div>
          )}
        </div>
      </div>

      <div className="grid grid-cols-2 gap-2 sm:grid-cols-4">
        <ReviewMetric
          label="Field matches"
          value={`${matchingFields}/${comparisons.length}`}
          tone={fieldIssues ? "warn" : "good"}
        />
        <ReviewMetric
          label="Field issues"
          value={fieldIssues}
          tone={fieldIssues ? "warn" : "good"}
        />
        <ReviewMetric
          label="Failed checks"
          value={failedRules}
          tone={failedRules ? "error" : "good"}
        />
        <ReviewMetric
          label="Warnings"
          value={warningRules}
          tone={warningRules ? "warn" : "good"}
        />
      </div>

      <Section
        title="What needs attention"
        hint="A short assessor-focused summary of failed, warning, uncertain, or incomplete AI validation items."
      >
        {issues.length > 0 ? (
          <ol className="space-y-2 rounded-md border border-border bg-card p-3">
            {issues.map((item, index) => (
              <li key={item} className="flex gap-2 text-sm text-foreground">
                <span className="mt-0.5 flex size-5 shrink-0 items-center justify-center rounded-full bg-muted text-2xs font-medium text-muted-foreground">
                  {index + 1}
                </span>
                <span>{item}</span>
              </li>
            ))}
          </ol>
        ) : (
          <div className="rounded-md border border-border bg-good-soft p-3 text-sm text-good">
            No validation issues found in the AI review.
          </div>
        )}
      </Section>

      <Section
        title="Field comparisons"
        hint="Each claim value (amount, date, provider) against what the AI read from the uploaded documents."
      >
        <FieldComparisonTable comparisons={comparisons} />
      </Section>

      <Section
        title="Rule checks"
        hint="Deterministic system checks (in-period, at least one receipt, no duplicate receipt) plus AI checks. A failed system check flags the claim before any AI spend."
      >
        <RuleResultsList results={ruleResults} />
      </Section>

      {(review.vision_checks?.length ?? 0) > 0 && (
        <Section
          title="Vision re-checks"
          hint="Targeted image re-reads used to confirm values the text pass was unsure about."
        >
          <VisionCheckList checks={review.vision_checks ?? []} />
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
