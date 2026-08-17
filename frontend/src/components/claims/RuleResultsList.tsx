import { Badge } from "@/components/ui/badge";
import type { RuleResult } from "@/api/claims";

const STATUS: Record<
  RuleResult["status"],
  { label: string; variant: "good" | "warn" | "error" | "outline" }
> = {
  pass: { label: "Pass", variant: "good" },
  fail: { label: "Fail", variant: "error" },
  warning: { label: "Warning", variant: "warn" },
  not_applicable: { label: "N/A", variant: "outline" },
};

const INCOMPLETE_COMPARISON_PREFIX =
  "The AI response omitted or duplicated field comparison: ";

function incompleteComparisonName(result: RuleResult): string | null {
  if (result.error_code !== "ai_output_incomplete") return null;
  if (result.affected_fields?.length) return null;
  if (!result.evidence.startsWith(INCOMPLETE_COMPARISON_PREFIX)) return null;
  return result.evidence
    .slice(INCOMPLETE_COMPARISON_PREFIX.length)
    .replace(/\.$/, "");
}

export function compactRuleResults(results: RuleResult[]): RuleResult[] {
  const missingComparisonNames: string[] = [];
  const kept: RuleResult[] = [];
  for (const result of results) {
    const name = incompleteComparisonName(result);
    if (name) {
      missingComparisonNames.push(name);
    } else {
      kept.push(result);
    }
  }
  if (missingComparisonNames.length === 0) return results;
  const fields = [...new Set(missingComparisonNames)].sort();
  return [
    ...kept,
    {
      rule: "AI comparison output incomplete.",
      status: "fail",
      source: "platform",
      error_code: "ai_output_incomplete",
      affected_fields: fields,
      evidence:
        "The AI did not return usable comparison results for every configured claim field.",
    },
  ];
}

export function RuleResultsList({ results }: { results: RuleResult[] }) {
  const compacted = compactRuleResults(results);
  if (compacted.length === 0) {
    return (
      <div className="text-sm text-muted-foreground p-4 text-center border border-dashed border-border rounded-md">
        No rule checks recorded.
      </div>
    );
  }
  return (
    <ul className="space-y-2">
      {compacted.map((r, i) => {
        const cfg = STATUS[r.status] ?? { label: r.status, variant: "outline" as const };
        const isIncompleteAiOutput = r.error_code === "ai_output_incomplete";
        return (
          <li key={i} className="rounded-md border border-border bg-card p-3">
            <div className="flex items-start justify-between gap-3">
              <div className="text-sm text-foreground">{r.rule}</div>
              <div className="flex shrink-0 items-center gap-2">
                {/* text-subtle, not muted-foreground/60: the tinted variant
                    dropped below the 4.5:1 contrast floor. */}
                <span className="text-2xs uppercase tracking-wider text-subtle">
                  {r.source === "ai" ? "ai" : "system"}
                </span>
                <Badge variant={cfg.variant}>{cfg.label}</Badge>
              </div>
            </div>
            {r.evidence && (
              <div className="mt-1.5 text-xs text-muted-foreground">{r.evidence}</div>
            )}
            {isIncompleteAiOutput && r.affected_fields?.length ? (
              <div className="mt-2 rounded-md bg-muted p-2 text-xs text-muted-foreground">
                <div>
                  Affected fields:{" "}
                  <span className="font-medium text-foreground">
                    {r.affected_fields.join(", ")}
                  </span>
                </div>
                <div className="mt-1">
                  Recommended fix: re-run AI review. If this repeats, check the
                  claim-type field mappings and AI prompt so the model returns the
                  exact configured claim keys.
                </div>
              </div>
            ) : null}
          </li>
        );
      })}
    </ul>
  );
}
