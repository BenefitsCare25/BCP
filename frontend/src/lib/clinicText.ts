/** Turning a panel workbook's shouting into something a member can read.
 *
 * The supplier ships every field in capitals — `ATRIO FAMILY CLINIC PTE LTD`,
 * `618 YISHUN RING ROAD #01-3238 SINGAPORE 760618`, `DR ZHANG HAO TIAN`. Forty
 * characters of tracked capitals is the least readable thing a page can print,
 * and DESIGN.md already bans the treatment for headings; a clinic name IS the
 * row's heading.
 *
 * Everything here is display-only. Nothing is written back, and searching
 * still runs server-side against the stored value. */

/** Runs that are initialisms, not words — they stay capitalised. Matched
 * against each alphabetic run on its own, so `(YISHUN)` and `24-HR` decompose
 * correctly around their punctuation and digits. `PTE`/`LTD` are deliberately
 * absent: they read as Pte Ltd everywhere in Singapore, which the default
 * title-case already produces. */
const ALL_CAPS = new Set(["tcm", "gp", "sp", "hdb", "amk", "cbd", "ntuc", "llp", "ii", "iii", "iv"]);
/** Lowercased inside a name, never as its first word. */
const MINOR = new Set(["of", "the", "and", "at", "in", "for", "on", "to", "de"]);
/** Units of time, which arrive welded to their figure in the hours cells. */
const UNITS = new Set(["am", "pm", "hr", "hrs", "h", "min", "mins", "noon"]);

/** Title-case a SHOUTED value, and leave anything else alone.
 *
 * The guard matters: a supplier who already sent mixed case has made a
 * decision (`McDonald`, `iHealth`) and re-casing it would be vandalism. Only a
 * string that is entirely uppercase is treated as shouting. */
export function readableCase(value: string | null | undefined): string {
  if (!value) return "";
  const text = value.trim();
  if (!text || text !== text.toUpperCase()) return text;
  let first = true;
  return text.replace(/[A-Za-z]+/g, (word: string, offset: number) => {
    const lower = word.toLowerCase();
    const wasFirst = first;
    first = false;
    // A UNIT welded to a digit is not a word: `8AM`, `2PM`, `24HRS`. Title
    // case gives "8Am - 2Pm", which reads like a typo. The set is closed on
    // purpose — lowercasing every digit-adjacent run instead turned the
    // clinic chain "1DOC" into "1doc".
    if (offset > 0 && UNITS.has(lower) && /\d/.test(text[offset - 1])) return lower;
    // A possessive is part of the word before it: MINISTER'S, not Minister'S.
    if (offset > 0 && text[offset - 1] === "'") return lower;
    if (ALL_CAPS.has(lower)) return word.toUpperCase();
    if (!wasFirst && MINOR.has(lower)) return lower;
    return lower[0].toUpperCase() + lower.slice(1);
  });
}

/** A clinic's address, shortened the way a person would say it. The supplier
 * repeats the country in every row and the postal code is already the last
 * six digits, so `SINGAPORE 760618` becomes `S760618` — twelve characters
 * back on a line that has to survive truncation on a 390px screen. */
export function readableAddress(value: string | null | undefined): string {
  if (!value) return "";
  return readableCase(value)
    .replace(/\bSingapore\s+(\d{6})\b/i, "S$1")
    // Several rows end on the separator left by an empty Address3 column.
    .replace(/[\s,;]+$/, "");
}

export interface PhoneParts {
  /** Grouped for reading: `6286 1923`. Empty when there is no dialable number. */
  display: string;
  /** Digits only, for the `tel:` href. */
  dial: string;
  /** The member-relevant half of the remark, or null. */
  note: string | null;
}

/** Remarks that describe the PANEL'S ADMINISTRATION rather than the visit.
 * These are broker paperwork and DESIGN.md is explicit that broker vocabulary
 * does not go to members — but "last registration is 30 mins before closing"
 * on the same field genuinely changes whether someone sets off, so the cell
 * cannot simply be dropped either. */
const ADMIN_NOTE =
  /\b(?:onboard(?:ing|ed)?|first day of operation|effective date|date of (?:joining|onboarding)|panel since|contract)\b/i;

/** Split `62353490 - LAST REGISTRATION IS 45 MINS BEFORE CLOSING` into a
 * number you can dial and a note you can read.
 *
 * Today the whole cell is interpolated into the button label, so 159 of 835
 * rows render a button reading "Call 63652908 - SURCHARGE MAY APPLY". */
/** The number, wherever it sits in the cell.
 *
 * Deliberately UNANCHORED, which the first version was not: a cell labelled
 * `TEL 62353490` then matched nothing and the row rendered no Call action at
 * all. The leading `\(?` is what lets a bracketed country code be recognised
 * as one instead of being flattened into the local number — `(65) 6235 3490`
 * anchored-and-stripped dials 6562353490, which is not a number. */
const PHONE_RUN = /\+?\(?\d[\d\s()+-]{4,}/;

export function splitPhone(raw: string | null | undefined): PhoneParts {
  const empty: PhoneParts = { display: "", dial: "", note: null };
  if (!raw) return empty;
  const text = raw.trim();
  const match = text.match(PHONE_RUN);
  if (!match || match.index === undefined) return empty;
  // `(65) …` is a country code, so it keeps its `+`; anything else is local.
  const run = match[0].replace(/^\((\d+)\)/, "+$1");
  const digits = run.replace(/\D/g, "");
  if (digits.length < 6) return empty;
  const dial = (run.startsWith("+") ? "+" : "") + digits;

  const rest = text
    .slice(match.index + match[0].length)
    .replace(/^[\s\-–—:.,]+/, "")
    .trim();
  let note: string | null = null;
  if (rest && !ADMIN_NOTE.test(rest)) {
    const sentence = readableCase(rest);
    note = /[.!?]$/.test(sentence) ? sentence : `${sentence}.`;
  }
  return { display: groupPhone(dial), dial, note };
}

/** Singapore and Malaysian landlines read in pairs of four. An international
 * number keeps its `+` and its country code separate; anything of an unfamiliar
 * length is left exactly as the supplier wrote it rather than grouped wrongly. */
function groupPhone(dial: string): string {
  if (dial.startsWith("+")) {
    const body = dial.slice(1);
    // +65 6235 3490 — only for a 2-digit code and an 8-digit SG/MY local part.
    if (body.length === 10) {
      return `+${body.slice(0, 2)} ${body.slice(2, 6)} ${body.slice(6)}`;
    }
    return dial;
  }
  return dial.length === 8 ? `${dial.slice(0, 4)} ${dial.slice(4)}` : dial;
}
