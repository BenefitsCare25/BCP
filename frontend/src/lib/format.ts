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
