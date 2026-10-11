import { Badge } from "@/components/ui/badge";
import { getPortalLocale, portalText, usePortalTranslation } from "@/i18n/portal";
import { parseServerDate } from "@/lib/format";

const STATUS_LABELS: Record<string, string> = {
  draft: "Draft",
  submitted: "Submitted",
  ai_review_pending: "Under review",
  ai_verified: "Review complete",
  ai_flagged: "Manual review",
  needs_info: "More information needed",
  approved: "Approved",
  rejected: "Not approved",
  sent_to_insurer: "Sent to insurer",
  paid: "Paid",
};

type BadgeVariant = "default" | "good" | "warn" | "error" | "info";

function statusVariant(status: string): BadgeVariant {
  if (status === "approved" || status === "paid") return "good";
  if (status === "rejected") return "error";
  if (status === "needs_info" || status === "ai_flagged") return "warn";
  if (status === "submitted" || status === "ai_review_pending") return "info";
  return "default";
}

export function claimStatusLabel(status: string): string {
  return portalText(STATUS_LABELS[status] ?? status.replaceAll("_", " "));
}

export function ClaimStatus({ status }: { status: string }) {
  usePortalTranslation();
  return <Badge variant={statusVariant(status)}>{claimStatusLabel(status)}</Badge>;
}

export function formatClaimDate(value: string | null): string {
  if (!value) return "—";
  const [year, month, day] = value.slice(0, 10).split("-").map(Number);
  if (!year || !month || !day) return value;
  return new Intl.DateTimeFormat(getPortalLocale(), {
    day: "numeric",
    month: "short",
    year: "numeric",
  }).format(new Date(year, month - 1, day));
}

/** A server TIMESTAMP (`submitted_at`, `created_at`) as its Singapore business
 *  date. `formatClaimDate` reads only the first ten characters, which for a UTC
 *  instant is the UTC day — a day early for anything filed before 08:00 SGT. */
export function formatClaimTimestamp(value: string | null): string {
  if (!value) return "—";
  const when = parseServerDate(value);
  if (Number.isNaN(when.getTime())) return formatClaimDate(value);
  return new Intl.DateTimeFormat(getPortalLocale(), {
    day: "numeric",
    month: "short",
    year: "numeric",
    timeZone: "Asia/Singapore",
  }).format(when);
}

export function formatClaimMoney(amount: number, currency: string): string {
  try {
    return new Intl.NumberFormat(getPortalLocale(), {
      style: "currency",
      currency,
      minimumFractionDigits: 2,
    }).format(amount);
  } catch {
    return `${currency} ${amount.toFixed(2)}`;
  }
}
