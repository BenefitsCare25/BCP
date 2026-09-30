import type { LucideIcon } from "lucide-react";

export function SummaryMetric({ icon: Icon, label, value, breakdown, onClick, controls, expanded }: {
  icon: LucideIcon; label: string; value: number;
  breakdown?: Array<{ label: string; value: number }>;
  onClick?: () => void; controls?: string; expanded?: boolean;
}) {
  return <div className="min-w-0 p-4 sm:p-5">
    <div className="flex items-center gap-2 text-xs font-medium text-muted-foreground">
      <Icon className="size-3.5 shrink-0" strokeWidth={1.75} aria-hidden="true" />
      <span className="min-w-0 leading-4">{label}</span>
    </div>
    <div className="mt-1.5 text-2xl font-semibold tracking-tight tabular-nums text-foreground sm:text-3xl">
      {onClick ? <button type="button" onClick={onClick} aria-label={`Show ${label.toLowerCase()}`}
        aria-controls={controls} aria-expanded={expanded}
        className="min-h-10 min-w-10 rounded-md text-primary hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring">{value.toLocaleString()}</button>
        : value.toLocaleString()}
    </div>
    {breakdown && <dl className="mt-2 flex flex-wrap gap-x-4 gap-y-1 text-xs leading-4">
      {breakdown.map(item => <div key={item.label} className="flex items-baseline gap-1.5">
        <dt className="text-muted-foreground">{item.label}</dt>
        <dd className="font-semibold tabular-nums text-foreground">{item.value.toLocaleString()}</dd>
      </div>)}
    </dl>}
  </div>;
}
