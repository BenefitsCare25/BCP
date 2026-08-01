/** Dates as a member reads them.
 *
 * The API carries ISO strings, sometimes with a time component the roster never
 * meant ("1986-04-29 00:00:00"). Printing those raw makes a claim page look
 * like a database dump, and "2026-07-12" is the one date format that is
 * genuinely ambiguous to no one but also natural to no one.
 *
 * Parsed as a plain calendar date, NOT through `new Date(iso)`: that treats a
 * bare "2026-07-12" as midnight UTC, so every date west of Greenwich renders
 * one day early. A claim's incurred date deciding it happened yesterday is the
 * kind of bug nobody reports and everybody distrusts.
 */
import { parseServerDate } from "@/lib/format";

const MONTHS = [
  "Jan", "Feb", "Mar", "Apr", "May", "Jun",
  "Jul", "Aug", "Sep", "Oct", "Nov", "Dec",
];

export function formatDay(iso: string | null | undefined): string {
  if (!iso) return "—";
  const [datePart] = String(iso).split(/[ T]/);
  const m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(datePart);
  if (!m) return datePart;
  const [, year, month, day] = m;
  const monthName = MONTHS[Number(month) - 1];
  if (!monthName) return datePart;
  return `${Number(day)} ${monthName} ${year}`;
}

/** The date rail beside a message: month over day.
 *
 * Takes the same defensive path as `formatDay` — a message's `created_at` is a
 * real timestamp, but this is also handed plain dates, and a bare "2026-07-12"
 * through `new Date()` lands a day early west of Greenwich. */
export function dayStamp(iso: string | null | undefined): {
  month: string;
  day: string;
} {
  if (!iso) return { month: "", day: "—" };
  const [datePart] = String(iso).split(/[ T]/);
  const m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(datePart);
  if (!m) return { month: "", day: datePart };
  const [, , month, day] = m;
  return { month: MONTHS[Number(month) - 1] ?? "", day: String(Number(day)) };
}

/** A timestamp as a person reads it: "11 Feb 2026, 2:00 pm".
 *
 * Only for values that genuinely carry a time (message `created_at`), rendered
 * in the BROWSER's zone — which is right here and wrong for a bare date, hence
 * the split from `formatDay`, which must never go near it.
 *
 * **Through `parseServerDate`, never bare `new Date()`.** The backend writes
 * UTC but SQLite serializes it with no offset, and JS reads an offset-less
 * string as LOCAL — so a message posted at 18:56 SGT printed as 10:56 on both
 * this thread and the broker's. Eight hours, on the one surface whose whole
 * job is to be a record of what was said and when. */
export function formatMoment(iso: string | null | undefined): string {
  if (!iso) return "—";
  const when = parseServerDate(iso);
  if (Number.isNaN(when.getTime())) return formatDay(iso);
  const time = when
    .toLocaleTimeString(undefined, { hour: "numeric", minute: "2-digit" })
    .toLowerCase();
  return `${when.getDate()} ${MONTHS[when.getMonth()]} ${when.getFullYear()}, ${time}`;
}

/** Today, as the calendar on the member's own wall reads it.
 *
 * `new Date().toISOString().slice(0, 10)` is UTC, so anywhere east of Greenwich
 * it names YESTERDAY for the first hours of the day — in Singapore, every
 * morning before 08:00. The claim form clamps its date picker to this value,
 * so a member claiming a visit on the morning it happened was told the date was
 * in the future and could not enter it. The same trap this file's header
 * warns about, arriving from the other direction: there, a UTC date was read a
 * day early; here, it is WRITTEN a day early.
 *
 * `en-CA` because it is the locale whose short date format is ISO. */
export function todayISO(): string {
  return new Date().toLocaleDateString("en-CA");
}
