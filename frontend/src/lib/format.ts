// Compact currency formatting shared across coverage / financials views.
// Handles negatives (e.g. on-change flex credits) and zero — the magnitude drives
// the compaction, the sign is preserved; the exponential branch is only for tiny
// sub-cent positive fractions, never for 0 or negative amounts.
export function fmtCurrency(v: number): string {
  const sign = v < 0 ? "-" : "";
  const a = Math.abs(v);
  if (a >= 1_000_000) return `${sign}$${(a / 1_000_000).toFixed(2)}M`;
  if (a >= 1_000) return `${sign}$${(a / 1_000).toFixed(1)}K`;
  if (a > 0 && a < 0.01) return v.toExponential(2);
  return `${sign}$${a.toLocaleString(undefined, { maximumFractionDigits: 2 })}`;
}

// Full (non-compacting) amount with thousands separators and up to 2 decimals,
// no currency symbol — for editable / exact figures (premiums, amount covered)
// where the compact "$1.2M" form of fmtCurrency would lose precision.
export function fmtAmount(v: number): string {
  return v.toLocaleString(undefined, { maximumFractionDigits: 2 });
}

// Strip time portion from date strings like "1986-04-29 00:00:00" → "1986-04-29".
export function fmtDate(v: string | null | undefined): string {
  if (!v) return "—";
  return String(v).split(/[ T]/)[0];
}

/** Parse a server timestamp. The backend emits UTC, but on SQLite the value is
 * serialized WITHOUT an offset (`2026-08-01T10:56:48.080711`), and `new Date()`
 * reads an offset-less string as browser-LOCAL. In Singapore that is an
 * eight-hour lie on every timestamp — a claim message posted at 18:56 reads as
 * 10:56. Treat a bare string as UTC by appending `Z`.
 *
 * Lives here rather than in `lib/attention.ts` (its first home) because it is
 * a property of the WIRE FORMAT, not of any one feature — every surface that
 * renders a server timestamp needs it, and the claim thread was the second
 * place to be caught by the same trap. */
export function parseServerDate(iso: string): Date {
  const hasTz = /([zZ])|([+-]\d{2}:?\d{2})$/.test(iso);
  return new Date(hasTz ? iso : `${iso}Z`);
}

/** A real timestamp with its time of day, in the viewer's zone — for values
 * that genuinely carry one (a message's `created_at`).
 *
 * Deliberately separate from `fmtDate`, which must never go near `new Date()`:
 * a bare "2026-07-12" parsed that way is midnight UTC and renders a day early
 * west of Greenwich. Anything unparseable falls back to the date alone rather
 * than printing "Invalid Date". */
export function fmtDateTime(v: string | null | undefined): string {
  if (!v) return "—";
  const when = parseServerDate(v);
  if (Number.isNaN(when.getTime())) return fmtDate(v);
  return when.toLocaleString(undefined, {
    year: "numeric",
    month: "short",
    day: "numeric",
    hour: "numeric",
    minute: "2-digit",
  });
}
