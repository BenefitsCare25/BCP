import { Badge } from "@/components/ui/badge";

/** Member-safe status labels — AI-internal states read as "under review";
 * fraud signals are never surfaced to members. */
const MEMBER_STATUS: Record<string, { label: string; variant: "good" | "warn" | "error" | "outline" | "info" }> = {
  draft: { label: "Draft", variant: "outline" },
  submitted: { label: "Under review", variant: "info" },
  ai_review_pending: { label: "Under review", variant: "info" },
  ai_verified: { label: "Under review", variant: "info" },
  ai_flagged: { label: "Under review", variant: "info" },
  needs_info: { label: "More info needed", variant: "warn" },
  approved: { label: "Approved", variant: "good" },
  rejected: { label: "Rejected", variant: "error" },
};

export function ClaimStatusBadge({ status }: { status: string }) {
  const cfg = MEMBER_STATUS[status] ?? { label: status, variant: "outline" as const };
  return <Badge variant={cfg.variant}>{cfg.label}</Badge>;
}
