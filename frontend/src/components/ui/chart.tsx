/** Recharts wrapper in the shadcn/ui chart pattern, styled only with theme
 *  tokens. A `ChartConfig` maps each series key to its label and colour (a
 *  `var(--color-…)` token); the container exposes them as `--chart-<key>` so
 *  marks use `fill="var(--chart-<key>)"` and follow a firm's brand colours.
 *
 *  Import from lazily loaded components only: Recharts must stay out of the
 *  broker, HR and portal entry bundles. */
import * as React from "react";
import { ResponsiveContainer, Tooltip, type TooltipContentProps } from "recharts";
import { cn } from "@/lib/cn";

export type ChartConfig = Record<string, { label: string; color: string }>;

interface ChartContextValue {
  config: ChartConfig;
  /** Animation is off when the viewer prefers reduced motion. */
  animate: boolean;
}

const ChartContext = React.createContext<ChartContextValue | null>(null);

export function useChart(): ChartContextValue {
  const context = React.useContext(ChartContext);
  if (!context) throw new Error("useChart must be used within a <ChartContainer />");
  return context;
}

const REDUCED_MOTION = "(prefers-reduced-motion: reduce)";

function usePrefersReducedMotion(): boolean {
  const [reduced, setReduced] = React.useState(
    () => typeof window !== "undefined" && window.matchMedia(REDUCED_MOTION).matches,
  );
  React.useEffect(() => {
    const query = window.matchMedia(REDUCED_MOTION);
    const update = () => setReduced(query.matches);
    query.addEventListener("change", update);
    return () => query.removeEventListener("change", update);
  }, []);
  return reduced;
}

export function ChartContainer({
  config,
  className,
  style,
  children,
  ...props
}: Omit<React.ComponentProps<"div">, "children"> & {
  config: ChartConfig;
  children: React.ReactElement;
}) {
  const reduced = usePrefersReducedMotion();
  const vars = Object.fromEntries(
    Object.entries(config).map(([key, series]) => [`--chart-${key}`, series.color]),
  ) as React.CSSProperties;
  const value = React.useMemo(() => ({ config, animate: !reduced }), [config, reduced]);
  return (
    <ChartContext.Provider value={value}>
      <div
        style={{ ...vars, ...style }}
        className={cn(
          "w-full text-2xs text-muted-foreground [&_.recharts-surface]:outline-none",
          className,
        )}
        {...props}
      >
        <ResponsiveContainer width="100%" height="100%">
          {children}
        </ResponsiveContainer>
      </div>
    </ChartContext.Provider>
  );
}

export const ChartTooltip = Tooltip;

type ChartTooltipContentProps = Partial<
  Pick<TooltipContentProps<number, string>, "active" | "payload" | "label">
> & {
  labelFormatter?: (label: string) => React.ReactNode;
};

/** Hover card: the label, then each series' swatch, name and value. */
export function ChartTooltipContent({
  active,
  payload,
  label,
  labelFormatter,
}: ChartTooltipContentProps) {
  const { config } = useChart();
  if (!active || !payload?.length) return null;
  return (
    <div className="min-w-32 rounded-md border border-border bg-card px-2.5 py-1.5 text-xs text-card-foreground shadow-md">
      <div className="mb-1 font-medium text-foreground">
        {labelFormatter ? labelFormatter(String(label ?? "")) : label}
      </div>
      {payload.map((item) => {
        const key = String(item.dataKey ?? item.name ?? "");
        const series = config[key];
        return (
          <div key={key} className="flex items-center gap-2">
            <span
              aria-hidden="true"
              className="size-2 shrink-0 rounded-sm"
              style={{ background: `var(--chart-${key})` }}
            />
            <span className="text-muted-foreground">{series?.label ?? key}</span>
            <span className="ml-auto font-medium tabular-nums text-foreground">
              {typeof item.value === "number" ? item.value.toLocaleString() : item.value}
            </span>
          </div>
        );
      })}
    </div>
  );
}
