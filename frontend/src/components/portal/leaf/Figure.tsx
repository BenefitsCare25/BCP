/** Figures on the leaf.
 *
 * The Tabular-Figure Rule: every figure a member could compare to another
 * figure is set in tabular lining numerals (inherited from `.leaf`, so nothing
 * here opts in) and money is NEVER abbreviated on a member surface —
 * `S$2,700`, never `S$2.7K`. A member reconciling a limit against a receipt is
 * doing arithmetic; an abbreviation makes them do it twice. */
import { cn } from "@/lib/cn";
import { formatValue } from "@/lib/benefitSchedule";

/** Money, written the way a member reads a receipt.
 *
 * `fmtAmount` sets only a MAXIMUM of 2 decimals, so a claim for 88.40 prints
 * as "88.4" — which looks like a truncated number next to the receipt in the
 * member's hand. Cents are shown in full when there are any and omitted when
 * there are none, so a round yearly limit stays "S$2,680" rather than gaining
 * a decorative ".00". */
export function moneyText(v: number): string {
  const cents = Math.round(Math.abs(v) * 100) % 100 !== 0;
  return v.toLocaleString(undefined, {
    minimumFractionDigits: cents ? 2 : 0,
    maximumFractionDigits: 2,
  });
}

/** Currency as a member would write it, not as the database stores it. The
 * backend carries ISO codes ("SGD"), which on a consumer surface reads like a
 * reference than a price. Unknown codes pass through unchanged rather than
 * being guessed at. */
const SYMBOL: Record<string, string> = {
  SGD: "S$",
  MYR: "RM",
  USD: "US$",
  EUR: "€",
  GBP: "£",
};

export function currencySymbol(code: string | null | undefined): string {
  if (!code) return "S$";
  const c = code.trim();
  return SYMBOL[c.toUpperCase()] ?? c;
}

export function Money({
  value,
  currency = "S$",
  className,
  emphasis = "normal",
}: {
  value: number | null | undefined;
  currency?: string | null;
  className?: string;
  /** `hero` is the ONE monumental figure per screen (DESIGN.md's Display tier).
   * Used once. If a screen has two, one of them is not the answer. */
  emphasis?: "normal" | "strong" | "display" | "hero";
}) {
  if (value === null || value === undefined) {
    return <span className={cn("text-label", className)}>—</span>;
  }
  const symbol = currency ? currencySymbol(currency) : "";
  const hero = emphasis === "hero";
  return (
    <span
      className={cn(
        hero &&
          "block text-[2.875rem] font-bold leading-[0.98] tracking-display sm:text-[3.875rem]",
        emphasis === "display" && "text-lg font-semibold tracking-title",
        emphasis === "strong" && "font-semibold",
        "whitespace-nowrap text-record",
        className,
      )}
    >
      {/* At hero size the symbol has to be set as its own unit or it reads as a
          second number beside the figure. 0.44em with a hair of tracking sits it
          on the cap line of the digits. */}
      {symbol &&
        (hero ? (
          <span className="mr-[0.09em] text-[0.44em] font-semibold tracking-title">
            {symbol}
          </span>
        ) : (
          symbol
        ))}
      {moneyText(value)}
    </span>
  );
}

/** A limit that may be either a parsed number or verbatim text from the
 * schedule ("As charged", "S$650 per day"). Renders the text when that is all
 * we have, because paraphrasing an insurer's limit wording on a member surface
 * would be a claim we cannot stand behind. */
export function Limit({
  amount,
  display,
  currency = "S$",
  className,
}: {
  amount: number | null;
  display: string | null;
  currency?: string | null;
  className?: string;
}) {
  // **`limit_display` goes through the SAME formatter the benefits tab uses.**
  // It is the SOB's raw cell, which is commonly a bare number ("20000"), and
  // printed verbatim it sat beside `FillRule`'s "S$0 of S$20,000 used" — two
  // conventions for one figure on the same row. `formatValue` leaves anything
  // already qualified ("S$650/day", "As charged") untouched, so this only
  // reaches the bare numbers; if it can make nothing of the string, the raw
  // cell still prints rather than vanishing.
  if (display) {
    return (
      <span className={cn("text-record", className)}>
        {formatValue(display, undefined, currency ?? undefined) ?? display}
      </span>
    );
  }
  if (amount !== null) return <Money value={amount} currency={currency} className={className} />;
  return <span className={cn("text-label", className)}>No yearly cap</span>;
}
