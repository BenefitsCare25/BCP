/** The benefit-year control — a SCOPE control, not a caption.
 *
 * It changes what the whole page is showing, which is why it sits with the
 * account controls rather than stacked under the member's name. Stacking it
 * there read as a subtitle and made the name look like it needed explaining.
 *
 * **Today it renders as plain text**, because the portal resolves the member
 * against the CURRENT benefit year only (`portal_auth.active_policy_year` →
 * `resolve_member_employee`). Offering a chevron that opens nothing would be a
 * lie about a capability. The moment the API can list the years a member
 * actually held a record in, pass `years` and the same control becomes a menu —
 * the closed state, the menu and the past-year treatment are all designed. See
 * docs/PORTAL_REDESIGN_PLAN.md, open item 1.
 *
 * A past year must never be silently browsable: claims cannot be submitted
 * against a closed year, so selecting one is expected to surface that in the
 * page as well as tint this control. */
import { ChevronDown, CalendarDays } from "lucide-react";
import { formatPolicyRange } from "@/lib/policy-year";
import { cn } from "@/lib/cn";

export type BenefitYearOption = {
  id: number;
  start_date: string;
  end_date: string;
  is_current: boolean;
};

export function BenefitYearControl({
  start,
  end,
  years,
  compact = false,
  className,
}: {
  start: string;
  end: string;
  /** Every year this member can open. One (or none) renders as static text. */
  years?: BenefitYearOption[];
  /** Phone width — shows the calendar year alone, since the bar has no room
   * for a full range beside the member's name and three controls. */
  compact?: boolean;
  className?: string;
}) {
  // Sliced, never `new Date(start).getFullYear()`: a bare ISO date parses as
  // midnight UTC, so every timezone west of Greenwich renders the PREVIOUS year
  // for a benefit year starting 1 January. Same trap `leaf/date.ts` documents.
  const label = compact ? start.slice(0, 4) : formatPolicyRange(start, end);
  const selectable = (years?.length ?? 0) > 1;

  const shell = cn(
    "inline-flex shrink-0 items-center gap-2 rounded-control text-row font-semibold text-record",
    compact ? "h-9 px-2.5" : "h-10 px-3",
    selectable
      ? "leaf-focus border border-input bg-bar transition-colors duration-200 ease-leaf hover:bg-shade"
      : // A hairline, not a transparent border: on the near-white ground the
        // fill alone gives the chip no edge, and a scope label with no edge
        // reads as stray text sitting near the heading. It is the DECORATIVE
        // rule colour rather than the control edge — this variant is a label,
        // and borrowing the control edge would promise a menu that is not
        // there (see the note above about the years list).
        "border border-hairline bg-shade",
    className,
  );

  const inner = (
    <>
      {!compact && (
        <CalendarDays className="size-4 shrink-0 text-label" aria-hidden />
      )}
      {label}
      {selectable && (
        <ChevronDown className="size-3.5 shrink-0 text-label" aria-hidden />
      )}
    </>
  );

  if (!selectable) {
    // Static, but still announced — a member using a screen reader needs to
    // know which year the figures on this page belong to.
    return (
      <span className={shell}>
        <span className="sr-only">Benefit year</span>
        {inner}
      </span>
    );
  }

  return (
    <button type="button" className={shell} aria-haspopup="listbox">
      <span className="sr-only">Benefit year</span>
      {inner}
    </button>
  );
}
