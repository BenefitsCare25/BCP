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

export function RuleResultsList({ results }: { results: RuleResult[] }) {
  if (results.length === 0) {
    return (
      <div className="text-sm text-muted-foreground p-4 text-center border border-dashed border-border rounded-md">
        No rule checks recorded.
      </div>
    );
  }
  return (
    <ul className="space-y-2">
      {results.map((r, i) => {
        const cfg = STATUS[r.status] ?? { label: r.status, variant: "outline" as const };
        return (
          <li key={i} className="rounded-md border border-border bg-card p-3">
            <div className="flex items-start justify-between gap-3">
              <div className="text-sm text-foreground">{r.rule}</div>
              <div className="flex shrink-0 items-center gap-2">
                {/* text-subtle, not muted-foreground/60: the tinted variant
                    dropped below the 4.5:1 contrast floor. */}
                <span className="text-2xs uppercase tracking-wider text-subtle">
                  {r.source === "deterministic" ? "system" : "ai"}
                </span>
                <Badge variant={cfg.variant}>{cfg.label}</Badge>
              </div>
            </div>
            {r.evidence && (
              <div className="mt-1.5 text-xs text-muted-foreground">{r.evidence}</div>
            )}
          </li>
        );
      })}
    </ul>
  );
}
