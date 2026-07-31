import { Badge } from "@/components/ui/badge";
import type { VisionCheck } from "@/api/claims";

const VERDICT: Record<
  VisionCheck["verdict"],
  { label: string; variant: "good" | "warn" | "error" }
> = {
  CONFIRMED: { label: "Value found", variant: "good" },
  REFUTED: { label: "Not in document", variant: "error" },
  UNCERTAIN: { label: "Uncertain", variant: "warn" },
};

export function VisionCheckList({ checks }: { checks: VisionCheck[] }) {
  if (checks.length === 0) return null;
  return (
    <ul className="space-y-2">
      {checks.map((v, i) => {
        const cfg = VERDICT[v.verdict] ?? { label: v.verdict, variant: "warn" as const };
        return (
          <li key={i} className="rounded-md border border-border bg-card p-3">
            <div className="flex items-start justify-between gap-3">
              <div className="text-sm text-foreground">{v.question}</div>
              <Badge variant={cfg.variant} className="shrink-0">
                {cfg.label}
              </Badge>
            </div>
            <div className="mt-1.5 text-xs text-muted-foreground">
              {v.explanation}
              {v.file_name && <span className="text-subtle"> · {v.file_name}</span>}
            </div>
          </li>
        );
      })}
    </ul>
  );
}
