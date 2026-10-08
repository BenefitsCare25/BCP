/** Daily activity as small multiples: one bar chart per series, each on its own
 *  axis (sign-ins outnumber claims many times over, and one shared axis would
 *  flatten the smaller series). Loaded lazily through `ActivityTrend`, so
 *  Recharts ships only with the platform console. */
import { Bar, BarChart, CartesianGrid, XAxis, YAxis } from "recharts";
import type { DailyActivity } from "@/api/platformActivity";
import {
  type ChartConfig,
  ChartContainer,
  ChartTooltip,
  ChartTooltipContent,
  useChart,
} from "@/components/ui/chart";
import { cn } from "@/lib/cn";
import { fmtDay } from "@/lib/format";
import { type TrendSeries, TREND_CONFIG, trendGridClass, trendSummary } from "./activitySeries";

/** "3 Oct" from "2026-10-03". */
function shortDay(date: string): string {
  return fmtDay(date).split(" ").slice(0, 2).join(" ");
}

const AXIS_TICK = { fill: "var(--color-muted-foreground)", fontSize: 11 };

function SeriesBars({ series }: { series: TrendSeries }) {
  const { animate } = useChart();
  return (
    <Bar
      dataKey={series}
      fill={`var(--chart-${series})`}
      radius={[4, 4, 0, 0]}
      maxBarSize={24}
      isAnimationActive={animate}
    />
  );
}

function SeriesChart({
  daily,
  series,
  height,
}: {
  daily: DailyActivity[];
  series: TrendSeries;
  height: string;
}) {
  const config: ChartConfig = { [series]: TREND_CONFIG[series] };
  const label = TREND_CONFIG[series].label;
  const readable = daily.every((d) => d[series] !== null);
  return (
    <figure className="min-w-0 space-y-1.5">
      <figcaption className="text-xs font-medium text-foreground">{label}</figcaption>
      {readable ? (
        <ChartContainer
          config={config}
          className={height}
          role="img"
          aria-label={trendSummary(daily, series)}
        >
          <BarChart data={daily} barCategoryGap="20%" accessibilityLayer={false}>
            <CartesianGrid vertical={false} stroke="var(--color-border)" />
            <XAxis
              dataKey="date"
              tickFormatter={shortDay}
              tick={AXIS_TICK}
              tickLine={false}
              axisLine={false}
              minTickGap={24}
            />
            <YAxis
              allowDecimals={false}
              tick={AXIS_TICK}
              tickLine={false}
              axisLine={false}
              width={32}
            />
            <ChartTooltip
              cursor={{ fill: "var(--color-muted)" }}
              content={<ChartTooltipContent labelFormatter={fmtDay} />}
            />
            <SeriesBars series={series} />
          </BarChart>
        </ChartContainer>
      ) : (
        <p className={cn("flex items-center text-xs text-muted-foreground", height)}>
          Not available: this firm&apos;s data could not be read.
        </p>
      )}
    </figure>
  );
}

export default function ActivityTrendCharts({
  daily,
  series,
  height = "h-40",
}: {
  daily: DailyActivity[];
  series: TrendSeries[];
  height?: string;
}) {
  return (
    <div className={trendGridClass(series.length)}>
      {series.map((s) => (
        <SeriesChart key={s} daily={daily} series={s} height={height} />
      ))}
    </div>
  );
}
