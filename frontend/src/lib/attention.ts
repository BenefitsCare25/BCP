import type { CompanySummary } from "@/api/hooks";
import { parseServerDate } from "@/lib/format";

// Shared "needs attention" derivation for the firm Home and company dashboard,
// so the two surfaces can't disagree about what counts as outstanding work.
// Every signal maps to the page that resolves it (`to`) — the dashboard renders
// a "Go →" link, Home renders a plain list.

export type AttentionTone = "warn" | "error";

export type AttentionItem = {
  key: string;
  /** Full sentence for the company dashboard. */
  message: string;
  /** Terse fragment for the firm Home list (no company name — that's rendered separately). */
  short: string;
  tone: AttentionTone;
  to?: string;
  /** Search params for the destination route (e.g. the roster tab to open). */
  search?: Record<string, string>;
};

const plural = (n: number) => (n === 1 ? "" : "s");

// Moved to `lib/format.ts` — it describes the WIRE FORMAT, not this feature,
// and the claim message thread was the second surface to be bitten by parsing
// an offset-less UTC string as local. Re-exported so existing importers keep
// working and there stays exactly one definition.
export { parseServerDate } from "@/lib/format";

/** Whole CALENDAR days from today until a timestamp, in the viewer's timezone:
 * 0 = closes at any time today, 1 = tomorrow, negative once past. Calendar-based
 * (not raw ms) so a deadline later *today* reads as "today", not "tomorrow". */
export function daysUntil(iso: string): number {
  const startOfLocalDay = (d: Date) =>
    new Date(d.getFullYear(), d.getMonth(), d.getDate()).getTime();
  const ms = startOfLocalDay(parseServerDate(iso)) - startOfLocalDay(new Date());
  return Math.round(ms / 86_400_000);
}

/** Ordered by urgency: a missing benefit year first (everything else depends on
 * it), then the operational backlog. */
export function companyAttention(c: CompanySummary): AttentionItem[] {
  if (!c.current_year) {
    return [
      {
        key: "year",
        message: "No current benefit year — set one in Company & Benefits",
        short: "no current benefit year",
        tone: "error",
        to: "/client-relations/company-benefits",
      },
    ];
  }

  const out: AttentionItem[] = [];

  if (c.claims_to_review > 0) {
    out.push({
      key: "claims",
      message: `${c.claims_to_review} claim${plural(c.claims_to_review)} awaiting review`,
      short: `${c.claims_to_review} claim${plural(c.claims_to_review)} to review`,
      tone: "warn",
      to: "/claims/review",
    });
  }
  if (c.employees_unmatched > 0) {
    out.push({
      key: "unmatched",
      message: `${c.employees_unmatched} member${plural(c.employees_unmatched)} not matched to a category`,
      short: `${c.employees_unmatched} unmatched member${plural(c.employees_unmatched)}`,
      tone: "warn",
      to: "/policy-admin/member-listing",
    });
  }
  if (c.matching_stale) {
    out.push({
      key: "stale",
      message: "Categories changed since the last matching run — re-run matching",
      short: "matching is stale",
      tone: "warn",
      to: "/policy-admin/member-listing",
    });
  }
  if (c.dependants_pending > 0) {
    out.push({
      key: "deps",
      message: `${c.dependants_pending} dependant approval${plural(c.dependants_pending)} pending`,
      short: `${c.dependants_pending} dependant approval${plural(c.dependants_pending)}`,
      tone: "warn",
      // The approval UI lives on the roster's Dependants tab, not the default
      // Employees tab.
      to: "/policy-admin/member-listing",
      search: { tab: "dependants" },
    });
  }
  if (c.underwriting_pending > 0) {
    out.push({
      key: "uw",
      message: `${c.underwriting_pending} underwriting case${plural(c.underwriting_pending)} pending`,
      short: `${c.underwriting_pending} U/W case${plural(c.underwriting_pending)} pending`,
      tone: "warn",
      to: "/policy-admin/underwriting",
    });
  }
  if (c.enrollment_closes_at) {
    const days = daysUntil(c.enrollment_closes_at);
    if (days <= 7) {
      const when =
        days <= 0 ? "closes today" : `closes in ${days} day${plural(days)}`;
      out.push({
        key: "enroll",
        message: `Enrolment period ${when}`,
        short: `enrolment ${when}`,
        tone: "warn",
        to: "/client-relations/enrollment",
      });
    }
  }

  return out;
}
