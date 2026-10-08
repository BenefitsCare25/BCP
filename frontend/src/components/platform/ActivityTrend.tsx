import { lazy, Suspense } from "react";
import { type DailyActivity, fmtCount } from "@/api/platformActivity";
import { Skeleton } from "@/components/ui/skeleton";
import { fmtDay } from "@/lib/format";
import { type TrendSeries, TREND_CONFIG, trendGridClass } from "./activitySeries";

// Recharts loads with the first chart, never with the app's entry bundle.
const ActivityTrendCharts = lazy(() => import("./ActivityTrendCharts"));

/** The charted values as a table, for screen readers and keyboard users. */
function TrendTable({ caption, daily }: { caption: string; daily: DailyActivity[] }) {
  const columns: TrendSeries[] = ["sign_ins", "claims_submitted", "enrolments_submitted"];
  return (
    <table className="sr-only">
      <caption>{caption}</caption>
      <thead>
        <tr>
          <th scope="col">Day</th>
          {columns.map((c) => (
            <th key={c} scope="col">{TREND_CONFIG[c].label}</th>
          ))}
        </tr>
      </thead>
      <tbody>
        {daily.map((day) => (
          <tr key={day.date}>
            <th scope="row">{fmtDay(day.date)}</th>
            {columns.map((c) => (
              <td key={c}>{fmtCount(day[c])}</td>
            ))}
          </tr>
        ))}
      </tbody>
    </table>
  );
}

/** Daily activity bars, one small chart per series, with the values in a
 *  visually hidden table. */
export function ActivityTrend({
  caption,
  daily,
  series,
  height = "h-40",
}: {
  caption: string;
  daily: DailyActivity[];
  series: TrendSeries[];
  height?: string;
}) {
  return (
    <div>
      <TrendTable caption={caption} daily={daily} />
      <Suspense
        fallback={
          <div className={trendGridClass(series.length)} aria-hidden="true">
            {series.map((s) => (
              <Skeleton key={s} className={height} />
            ))}
          </div>
        }
      >
        <ActivityTrendCharts daily={daily} series={series} height={height} />
      </Suspense>
    </div>
  );
}
