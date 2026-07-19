const MONTHS_SHORT = [
  "Jan",
  "Feb",
  "Mar",
  "Apr",
  "May",
  "Jun",
  "Jul",
  "Aug",
  "Sep",
  "Oct",
  "Nov",
  "Dec",
];

export const MONTHS = [
  { value: 1, label: "January", short: "Jan" },
  { value: 2, label: "February", short: "Feb" },
  { value: 3, label: "March", short: "Mar" },
  { value: 4, label: "April", short: "Apr" },
  { value: 5, label: "May", short: "May" },
  { value: 6, label: "June", short: "Jun" },
  { value: 7, label: "July", short: "Jul" },
  { value: 8, label: "August", short: "Aug" },
  { value: 9, label: "September", short: "Sep" },
  { value: 10, label: "October", short: "Oct" },
  { value: 11, label: "November", short: "Nov" },
  { value: 12, label: "December", short: "Dec" },
];

const ISO_DATE = /^(\d{4})-(\d{2})-(\d{2})$/;

function parts(iso: string): { y: number; m: number; d: number } | null {
  const match = ISO_DATE.exec(iso);
  if (!match) return null;
  return {
    y: Number.parseInt(match[1], 10),
    m: Number.parseInt(match[2], 10),
    d: Number.parseInt(match[3], 10),
  };
}

/** `2026-09-01` + `2027-08-31` → `1 Sep 2026 – 31 Aug 2027` */
export function formatPolicyRange(startIso: string, endIso: string): string {
  const s = parts(startIso);
  const e = parts(endIso);
  if (!s || !e) return `${startIso} – ${endIso}`;
  return `${s.d} ${MONTHS_SHORT[s.m - 1]} ${s.y} – ${e.d} ${MONTHS_SHORT[e.m - 1]} ${e.y}`;
}

/** Last day of a given month (handles leap years). */
export function lastDayOfMonth(year: number, month: number): number {
  return new Date(year, month, 0).getDate();
}

/** Pad to ISO yyyy-mm-dd. */
export function toIsoDate(year: number, month: number, day: number): string {
  const pad = (n: number) => String(n).padStart(2, "0");
  return `${year}-${pad(month)}-${pad(day)}`;
}

/** Local today as ISO yyyy-mm-dd (local, not UTC — no midnight day skew). */
export function todayIso(): string {
  const n = new Date();
  return toIsoDate(n.getFullYear(), n.getMonth() + 1, n.getDate());
}

/** A benefit year whose period has ended — view-only on the config page. */
export function isPastPolicyPeriod(endIso: string): boolean {
  return endIso < todayIso();
}

/** Today falls within the period — required before a year can be "current". */
export function isWithinPolicyPeriod(startIso: string, endIso: string): boolean {
  const t = todayIso();
  return startIso <= t && t <= endIso;
}

const MONTH_LOOKUP = new Map<string, number>();
for (const m of MONTHS) {
  MONTH_LOOKUP.set(m.label.toLowerCase(), m.value);
  MONTH_LOOKUP.set(m.short.toLowerCase(), m.value);
}

// One date token in any common slip format. Day-first numeric (Singapore
// convention) with `/`, `.`, or `-` separators; 2- or 4-digit year. Also
// textual "1 Jul 2027" / "1 July 2027" and month-first "Jul 1, 2027".
const DATE_TOKEN =
  /\d{1,2}[/.\-]\d{1,2}[/.\-]\d{2,4}|\d{1,2}\s+[A-Za-z]{3,9}\.?\s+\d{4}|[A-Za-z]{3,9}\.?\s+\d{1,2},?\s+\d{4}/g;

function parseOneDate(token: string): { y: number; m: number; d: number } | null {
  const t = token.trim();
  const num = /^(\d{1,2})[/.\-](\d{1,2})[/.\-](\d{2,4})$/.exec(t);
  if (num) {
    const a = Number.parseInt(num[1], 10);
    const b = Number.parseInt(num[2], 10);
    let y = Number.parseInt(num[3], 10);
    if (y < 100) y += 2000;
    // Disambiguate day vs month: a value > 12 can only be a day. When both are
    // ≤ 12 it's ambiguous — default to day-first (SG convention).
    let d: number;
    let m: number;
    if (b > 12 && a <= 12) {
      m = a;
      d = b;
    } else {
      d = a;
      m = b;
    }
    if (m < 1 || m > 12 || d < 1 || d > lastDayOfMonth(y, m)) return null;
    return { y, m, d };
  }
  const dmy = /^(\d{1,2})\s+([A-Za-z]+)\.?\s+(\d{4})$/.exec(t);
  const mdy = /^([A-Za-z]+)\.?\s+(\d{1,2}),?\s+(\d{4})$/.exec(t);
  const textual = dmy
    ? { day: dmy[1], mon: dmy[2], year: dmy[3] }
    : mdy
      ? { day: mdy[2], mon: mdy[1], year: mdy[3] }
      : null;
  if (textual) {
    const m = MONTH_LOOKUP.get(textual.mon.toLowerCase());
    const d = Number.parseInt(textual.day, 10);
    const y = Number.parseInt(textual.year, 10);
    if (!m || d < 1 || d > lastDayOfMonth(y, m)) return null;
    return { y, m, d };
  }
  return null;
}

/**
 * Parse a free-text "Period of Insurance" value (e.g. from a placement slip)
 * into an ISO start/end range. Returns null when the text can't be confidently
 * parsed into two valid dates with end on/after start — callers must treat a
 * null as "unknown" and not surface a mismatch.
 */
export function parsePeriodOfInsurance(
  text: string | null | undefined,
): { start: string; end: string } | null {
  if (!text) return null;
  const dates: Array<{ y: number; m: number; d: number }> = [];
  for (const match of text.matchAll(DATE_TOKEN)) {
    const parsed = parseOneDate(match[0]);
    if (parsed) dates.push(parsed);
    if (dates.length === 2) break;
  }
  if (dates.length < 2) return null;
  const start = toIsoDate(dates[0].y, dates[0].m, dates[0].d);
  const end = toIsoDate(dates[1].y, dates[1].m, dates[1].d);
  if (end < start) return null;
  return { start, end };
}
