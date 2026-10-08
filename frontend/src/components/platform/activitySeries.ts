/** Series definitions and text summaries for the daily activity charts. Kept
 *  apart from the chart module so pages can describe a trend without loading
 *  Recharts. */
import type { DailyActivity } from "@/api/platformActivity";
import type { ChartConfig } from "@/components/ui/chart";
import { fmtDay } from "@/lib/format";

export type TrendSeries = "sign_ins" | "claims_submitted" | "enrolments_submitted";

// One series per chart, so one brand hue throughout; the chart title names it.
export const TREND_CONFIG = {
  sign_ins: { label: "Sign-ins", color: "var(--color-primary)" },
  claims_submitted: { label: "Claims submitted", color: "var(--color-primary)" },
  enrolments_submitted: { label: "Enrolments submitted", color: "var(--color-primary)" },
} satisfies ChartConfig;

/** "Sign-ins per day, 30 days: 42 in total, highest 7 on 3 Oct 2026." */
export function trendSummary(daily: DailyActivity[], series: TrendSeries): string {
  const label = TREND_CONFIG[series].label;
  let total = 0;
  let peak: DailyActivity | null = null;
  for (const day of daily) {
    const value = day[series] ?? 0;
    total += value;
    if (value > 0 && (peak === null || value > (peak[series] ?? 0))) peak = day;
  }
  const head = `${label} per day, ${daily.length} days: ${total.toLocaleString()} in total`;
  return peak
    ? `${head}, highest ${(peak[series] ?? 0).toLocaleString()} on ${fmtDay(peak.date)}.`
    : `${head}.`;
}

/** Grid columns for a row of small-multiple charts. */
export function trendGridClass(count: number): string {
  if (count > 2) return "grid gap-5 lg:grid-cols-3";
  return count === 2 ? "grid gap-5 md:grid-cols-2" : "grid gap-5";
}
