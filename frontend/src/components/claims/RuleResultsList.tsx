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
          <li key={i} className="rounded-md border border-border bg-card p-2.5">
            <div className="flex items-start justify-between gap-2">
              <div className="text-sm text-foreground">{r.rule}</div>
              <div className="flex items-center gap-1.5 shrink-0">
                <span className="text-[10px] uppercase tracking-wider text-muted-foreground/60">
                  {r.source === "deterministic" ? "system" : "ai"}
                </span>
                <Badge variant={cfg.variant}>{cfg.label}</Badge>
              </div>
            </div>
            {r.evidence && (
              <div className="text-xs text-muted-foreground mt-1">{r.evidence}</div>
            )}
          </li>
        );
      })}
    </ul>
  );
}
