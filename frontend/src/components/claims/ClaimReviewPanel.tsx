import { useEffect, useRef } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { AlertTriangle, Loader2, RefreshCw, ShieldCheck } from "lucide-react";
import { useClaimReview } from "@/api/claims";
import { FieldComparisonTable } from "@/components/claims/FieldComparisonTable";
import { RuleResultsList } from "@/components/claims/RuleResultsList";
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

      <Section
        title="Field comparisons"
        hint="Each claim value (amount, date, provider) against what the AI read from the uploaded documents."
      >
        <FieldComparisonTable comparisons={review.field_comparisons ?? []} />
      </Section>

      <Section
        title="Rule checks"
        hint="Deterministic system checks (in-period, at least one receipt, no duplicate receipt) plus AI checks. A failed system check flags the claim before any AI spend."
      >
        <RuleResultsList results={review.rule_results ?? []} />
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
