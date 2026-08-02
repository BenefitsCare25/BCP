/** "Is it open right now?" — read from a supplier spreadsheet.
 *
 * The panel workbook ships four free-text cells per clinic (`MON-FRI:`, `SAT:`,
 * `SUN:`, `PH:`) written by whoever maintained the network, and no two rows
 * agree on a format. Measured across all 836 rows of the current panel: 60% of
 * weekday strings parse cleanly, 32% state a blanket session and then a
 * per-day exception (`MON-FRI: 8AM - 1PM, MON, WED, THU: 6PM - 9PM`), and 8%
 * are unreadable (`SUN: BY APPOINTMENT`, a Facebook handle, `SAT: 8PM - 1PM`).
 *
 * **The reading is deliberately asymmetric, and that asymmetry is the whole
 * safety argument.** A member walking to a closed clinic is the failure that
 * matters, so:
 *
 *   - The scan STOPS at the first token it cannot account for. Everything after
 *     a per-day clause is discarded rather than absorbed — reading
 *     `MON, WED, THU: 6PM - 9PM` as a blanket weekday session would tell a
 *     member the clinic is open on a Tuesday evening when it is shut.
 *   - An INCOMPLETE reading may say "Open now" (we are inside a range we
 *     actually read) but may NEVER say "Closed" (there could be an evening
 *     session we stopped before). It returns null instead, and the row simply
 *     shows no state.
 *   - Anything unreadable returns null. Silence is always available.
 *
 * **Known limit, stated rather than hidden: public holidays.** We hold no
 * gazette, so on roughly eleven days a year a clinic that closes for the
 * holiday still reads against its weekday hours. Its own stated PH hours are
 * one tap away in the opened row, which is why the disclosure lists all four
 * lines rather than only today's.
 *
 * Time is resolved in **Asia/Singapore**, never the device's zone — the panel
 * is SG + Johor Bahru (both UTC+8) and a member travelling with a laptop still
 * needs the clinic's local clock. */

export type HoursReading =
  | { kind: "closed" }
  | { kind: "always" }
  /** Minute-of-day pairs. `complete` is false when the scan stopped early. */
  | { kind: "ranges"; ranges: [number, number][]; complete: boolean }
  | { kind: "unknown" };

export type OpenTone = "open" | "shut";

export interface OpenState {
  tone: OpenTone;
  /** "Open now" / "Closed now" / "Open 24 hours" / "Closed today". */
  label: string;
  /** "Until 5:30 pm" / "Opens 6:00 pm" — null when there is nothing to add. */
  detail: string | null;
}

/** Which of the four cells today falls in. */
export type HoursKey = "mon_fri" | "sat" | "sun" | "public_holiday";

/** The day label a supplier puts before the times — dropped before parsing.
 * Anchored, so a `MON, WED, THU:` clause in the MIDDLE of a line is left in
 * place and stops the scan, which is exactly what must happen. */
const DAY_PREFIX =
  /^\s*(?:mon|tues?|weds?|thurs?|thu|fri|sat|sun|ph|p\.h\.|public\s*holiday|weekday|weekend|daily)[a-z\s.&,–—-]*[:;]/i;

const CLOSED = /^(?:closed|close|nil|n\.?a\.?|none|no|-{1,2})\b/i;
const ALL_DAY = /\b24\s*(?:h|hr|hrs|hour|hours)\b/i;

/** One clock time: `9`, `9AM`, `9:30`, `9.30pm`, `830AM`, `1230PM`. */
const TIME = "(\\d{1,4})(?:[:.](\\d{2}))?\\s*(am|pm|a\\.m\\.|p\\.m\\.)?";
const RANGE = new RegExp(`${TIME}\\s*(?:-|–|—|to|till|until)\\s*${TIME}`, "gi");
/** What may legitimately sit BETWEEN two ranges. Anything else ends the scan. */
const SEPARATOR = /^[\s,;&./]*(?:and)?[\s,;&./]*$/i;

function clockToMinutes(
  digits: string,
  minutePart: string | undefined,
  meridiem: string | undefined,
): number | null {
  let hour: number;
  let minute: number;
  if (minutePart !== undefined) {
    hour = Number(digits);
    minute = Number(minutePart);
  } else if (digits.length <= 2) {
    hour = Number(digits);
    minute = 0;
  } else {
    // `830` / `1230` — a compact time with no separator. Splitting on the last
    // two digits is what makes "130PM" 1:30 pm rather than hour 13 in the
    // afternoon, which is how a third of the sheet writes half past.
    hour = Number(digits.slice(0, digits.length - 2));
    minute = Number(digits.slice(-2));
  }
  if (!Number.isFinite(hour) || !Number.isFinite(minute)) return null;
  if (minute > 59) return null;
  const mer = meridiem?.[0]?.toLowerCase();
  if (mer === "p" && hour !== 12) hour += 12;
  if (mer === "a" && hour === 12) hour = 0;
  if (hour > 23) return null;
  return hour * 60 + minute;
}

/** Drop the `MON-FRI:` the supplier writes in front of the times. Exported
 * because the opened row prints these values beside a `Mon–Fri` term of its
 * own, and the cell would otherwise say it twice.
 *
 * Anchored and day-word-led on purpose: a naive "cut at the first colon" eats
 * `8:` out of `8:30am - 1pm`. */
export function stripDayPrefix(raw: string): string {
  return raw.replace(DAY_PREFIX, "").trim();
}

export function parseHoursLine(raw: string | null | undefined): HoursReading {
  if (!raw) return { kind: "unknown" };
  const text = stripDayPrefix(raw);
  if (!text) return { kind: "unknown" };
  if (CLOSED.test(text)) return { kind: "closed" };
  if (ALL_DAY.test(text)) return { kind: "always" };

  const ranges: [number, number][] = [];
  let cursor = 0;
  let complete = true;
  RANGE.lastIndex = 0;
  for (let m = RANGE.exec(text); m; m = RANGE.exec(text)) {
    // Everything skipped over since the last range has to be punctuation. The
    // moment it is a word, the line is describing specific days and this scan
    // has no business reading further.
    if (!SEPARATOR.test(text.slice(cursor, m.index))) {
      complete = false;
      break;
    }
    const end = clockToMinutes(m[4], m[5], m[6]);
    // A range with no meridiem on its END is not resolvable — "8 - 1" could be
    // either eight hours or five. Stop rather than guess.
    if (end === null || !m[6]) {
      complete = false;
      break;
    }
    let start = clockToMinutes(m[1], m[2], m[3]);
    if (start === null) {
      complete = false;
      break;
    }
    if (!m[3]) {
      // No meridiem on the start ("9 - 1PM"): take the reading that puts the
      // start before the end, preferring am.
      const asPm = clockToMinutes(m[1], m[2], "pm");
      if (start >= end && asPm !== null && asPm < end) start = asPm;
    }
    if (start >= end) {
      complete = false;
      break;
    }
    ranges.push([start, end]);
    cursor = m.index + m[0].length;
  }
  if (complete && !SEPARATOR.test(text.slice(cursor))) complete = false;
  if (ranges.length === 0) return { kind: "unknown" };
  return { kind: "ranges", ranges, complete };
}

function formatClock(minutes: number): string {
  const h24 = Math.floor(minutes / 60);
  const m = minutes % 60;
  const suffix = h24 >= 12 ? "pm" : "am";
  const h12 = h24 % 12 === 0 ? 12 : h24 % 12;
  return m === 0 ? `${h12} ${suffix}` : `${h12}:${String(m).padStart(2, "0")} ${suffix}`;
}

/** Singapore's own clock, whatever the device is set to. */
export function singaporeNow(now: Date = new Date()): {
  key: HoursKey;
  minutes: number;
} {
  const parts = new Intl.DateTimeFormat("en-GB", {
    timeZone: "Asia/Singapore",
    weekday: "short",
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  }).formatToParts(now);
  const get = (type: string) => parts.find((p) => p.type === type)?.value ?? "";
  const weekday = get("weekday").toLowerCase();
  // `hour` is "24" at midnight under hour12:false in some engines.
  const hour = Number(get("hour")) % 24;
  const minutes = hour * 60 + Number(get("minute"));
  const key: HoursKey = weekday === "sat" ? "sat" : weekday === "sun" ? "sun" : "mon_fri";
  return { key, minutes };
}

/** The state to strike on the row, or null to say nothing at all. */
export function openStateFor(
  hours: Partial<Record<HoursKey, string | undefined>> | null | undefined,
  clock: { key: HoursKey; minutes: number },
): OpenState | null {
  const reading = parseHoursLine(hours?.[clock.key]);
  if (reading.kind === "closed") {
    return { tone: "shut", label: "Closed today", detail: null };
  }
  if (reading.kind === "always") {
    return { tone: "open", label: "Open 24 hours", detail: null };
  }
  if (reading.kind !== "ranges") return null;

  const inside = reading.ranges.find(
    ([start, end]) => clock.minutes >= start && clock.minutes < end,
  );
  if (inside) {
    return { tone: "open", label: "Open now", detail: `Until ${formatClock(inside[1])}` };
  }
  // Outside every range we READ — which only settles the question if we read
  // the whole line. See the asymmetry note at the top.
  if (!reading.complete) return null;
  const next = reading.ranges.find(([start]) => start > clock.minutes);
  return {
    tone: "shut",
    label: "Closed now",
    detail: next ? `Opens ${formatClock(next[0])}` : null,
  };
}
