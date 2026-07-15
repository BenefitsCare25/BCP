import { Badge } from "@/components/ui/badge";
import type { CategoryStatus, SourceKind } from "@/types";

export function sourcePill(source: SourceKind) {
  if (source === "manual") return <Badge variant="primary">Manual</Badge>;
  if (source === "ai_extracted") return <Badge variant="warn">AI</Badge>;
  if (source === "system_generated") return <Badge variant="outline">Auto</Badge>;
  return <Badge variant="default">CSV</Badge>;
}

export function statusPill(status: CategoryStatus) {
  if (status === "confirmed") return <Badge variant="good">Confirmed</Badge>;
  if (status === "needs_review") return <Badge variant="warn">Needs review</Badge>;
  return <Badge variant="outline">Draft</Badge>;
}

export function confidencePill(conf: number | null) {
  if (conf === null) return null;
  const pct = Math.round(conf * 100);
  if (conf >= 0.85) return <Badge variant="good">{pct}%</Badge>;
  if (conf >= 0.5) return <Badge variant="warn">{pct}%</Badge>;
  return <Badge variant="error">{pct}%</Badge>;
}
