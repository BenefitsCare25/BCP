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
  const hasIncompleteComparison = ruleResults.some(
    (result) =>
      result.error_code === "ai_output_incomplete" &&
      result.rule.toLowerCase().includes("comparison"),
  );
  const platformRules = ruleResults.filter(
    (r) => r.error_code === "ai_output_incomplete",
  );
  const blockingRules = ruleResults.filter((r) =>
    (r.status === "fail" || r.status === "warning") &&
    r.error_code !== "ai_output_incomplete"
  );
  const passedRules = ruleResults.filter((r) =>
    r.status === "pass" || r.status === "not_applicable"
  );
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
          <div className="text-xs mt-1 opacity-90">
            {flagged
              ? issues[0]?.detail ?? "Review the highlighted validation issue before deciding."
              : "The review did not find validation issues in the configured checks."}
          </div>
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
        title="Review outcome"
        hint="Only unresolved validation items are shown here. Passing checks stay available in the audit details below."
      >
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
      </Section>

      <Section
        title="Comparison details"
        hint="Claim values compared against the uploaded documents. Repeated omitted-comparison notes are hidden when the platform issue above already explains them."
      >
        <ReviewDetails
          label={`${fieldIssues} field issue${fieldIssues === 1 ? "" : "s"} · ${matchingFields} matched`}
        >
          <FieldComparisonTable
            comparisons={comparisons}
            hideOmittedNotes={hasIncompleteComparison}
          />
        </ReviewDetails>
      </Section>

      <Section
        title="Rule checks"
        hint="Failed or warning checks are shown first. Passed checks are collapsed to keep the review readable."
      >
        <div className="space-y-2">
          {blockingRules.length > 0 ? (
            <RuleResultsList results={blockingRules} />
          ) : (
            <div className="rounded-md border border-border bg-good-soft p-3 text-sm text-good">
              No failed or warning rule checks.
            </div>
          )}
          {passedRules.length > 0 && (
            <ReviewDetails
              label={`${passedRules.length} passed / not applicable check${
                passedRules.length === 1 ? "" : "s"
              }`}
            >
              <RuleResultsList results={passedRules} />
            </ReviewDetails>
          )}
          {platformRules.length > 0 && (
            <ReviewDetails label="Technical AI response issue">
              <RuleResultsList results={platformRules} />
            </ReviewDetails>
          )}
        </div>
      </Section>

      {review.summary && (
        <ReviewDetails label="Raw AI summary">
          <div className="whitespace-pre-line text-xs text-muted-foreground">
            {review.summary}
          </div>
        </ReviewDetails>
      )}

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
