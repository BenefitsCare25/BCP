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
import { fmtDay, parseServerDate } from "@/lib/format";

const MONTHS = [
  "Jan", "Feb", "Mar", "Apr", "May", "Jun",
  "Jul", "Aug", "Sep", "Oct", "Nov", "Dec",
];

/** The bare calendar date at the head of a server value ("2026-07-12"), or ""
 * when there isn't one.
 *
 * The single defensive parse this module is built on — split the time component
 * off, then require a real ISO date. It was written out inline in every
 * function here (and again in the claims ledger), which is how a hardening of
 * one copy would have silently left the others reading a different day.
 */
export function dateKey(iso: string | null | undefined): string {
  if (!iso) return "";
  const [datePart] = String(iso).split(/[ T]/);
  return /^\d{4}-\d{2}-\d{2}$/.test(datePart) ? datePart : "";
}

/** Re-exported rather than reimplemented: `lib/format.ts::fmtDay` is the same
 * defensive split, and the broker surfaces needed it too. Two copies is how a
 * date comes to read one way on a claim queue and another on the member's own
 * record of that claim. */
export const formatDay = fmtDay;

const MONTHS_FULL = [
  "January", "February", "March", "April", "May", "June",
  "July", "August", "September", "October", "November", "December",
];

/** The month a claim belongs to, for a group heading over a ledger.
 *
 * Same defensive parse as `formatDay` and for the same reason — these values
 * arrive as bare calendar dates and `new Date()` reads them a day early west of
 * Greenwich, which on the last day of a month files the claim under the wrong
 * heading. A value we cannot read returns "" so the caller can label the group
 * itself rather than printing a broken month. */
export function monthLabel(iso: string | null | undefined): string {
  if (!iso) return "";
  const [datePart] = String(iso).split(/[ T]/);
  const m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(datePart);
  if (!m) return "";
  const [, year, month] = m;
  const name = MONTHS_FULL[Number(month) - 1];
  return name ? `${name} ${year}` : "";
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

/** A conversation's last-activity stamp, at the size a list row can carry.
 *
 * An inbox with no time on its rows cannot answer the first question a member
 * brings to it — *is this still live?* Every row in the portal's message list
 * shipped without one, so a notice from March and one from this morning read
 * identically.
 *
 * Three grades, because a stamp competing with the title for width is a stamp
 * nobody reads: today is a clock time, this year drops the year, and anything
 * older carries it. Through `parseServerDate`, never a bare `new Date()` — an
 * offset-less SQLite timestamp read as local is eight hours out in
 * Singapore, and "8 hours ago" vs "just now" is exactly the distinction a queue
 * is sorted on.
 */
export function shortMoment(iso: string | null | undefined): string {
  if (!iso) return "";
  const when = parseServerDate(iso);
  if (Number.isNaN(when.getTime())) return formatDay(iso);
  const now = new Date();
  const sameDay =
    when.getFullYear() === now.getFullYear() &&
    when.getMonth() === now.getMonth() &&
    when.getDate() === now.getDate();
  if (sameDay) {
    return when
      .toLocaleTimeString(undefined, { hour: "numeric", minute: "2-digit" })
      .toLowerCase();
  }
  const month = MONTHS[when.getMonth()] ?? "";
  return when.getFullYear() === now.getFullYear()
    ? `${when.getDate()} ${month}`
    : `${when.getDate()} ${month} ${when.getFullYear()}`;
}

/** The heading over a run of messages sent on one day.
 *
 * A thread prints its full date on every message, so six replies exchanged in
 * one afternoon repeated "1 Aug 2026" six times and the eye had to read all of
 * them to find where a new day started. The rail carries the date once; the
 * messages beneath it keep only their clock time. */
export function dayHeading(iso: string | null | undefined): string {
  if (!iso) return "";
  const when = parseServerDate(iso);
  if (Number.isNaN(when.getTime())) return formatDay(iso);
  const now = new Date();
  const midnight = (d: Date) =>
    new Date(d.getFullYear(), d.getMonth(), d.getDate()).getTime();
  const days = Math.round((midnight(now) - midnight(when)) / 86_400_000);
  if (days === 0) return "Today";
  if (days === 1) return "Yesterday";
  const month = MONTHS[when.getMonth()] ?? "";
  return when.getFullYear() === now.getFullYear()
    ? `${when.getDate()} ${month}`
    : `${when.getDate()} ${month} ${when.getFullYear()}`;
}

/** Just the clock, for a message sitting under a day heading that already
 *  carries its date. */
export function clockTime(iso: string | null | undefined): string {
  if (!iso) return "";
  const when = parseServerDate(iso);
  if (Number.isNaN(when.getTime())) return "";
  return when
    .toLocaleTimeString(undefined, { hour: "numeric", minute: "2-digit" })
    .toLowerCase();
}
