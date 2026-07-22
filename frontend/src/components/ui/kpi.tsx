import type { LucideIcon } from "lucide-react";
import { cn } from "@/lib/cn";

/**
 * A headline stat tile: an icon + label over a large number. Shared by the
 * firm Home and the company dashboard. `tone="warn"` turns the value amber when
 * it's non-zero (e.g. items that need action); otherwise the value is neutral.
 */
export function Kpi({
  label,
  value,
  icon: Icon,
  tone = "default",
}: {
  label: string;
  value: number;
  icon: LucideIcon;
  tone?: "default" | "warn";
}) {
  return (
    <div className="rounded-lg border border-border bg-card p-4">
      <div className="flex items-center gap-1.5 text-xs text-muted-foreground">
        <Icon className="size-3.5" />
        {label}
      </div>
      <div
        className={cn(
          "mt-1 text-2xl font-semibold tabular-nums",
          tone === "warn" && value > 0 ? "text-warn" : "text-foreground",
        )}
      >
        {value}
      </div>
    </div>
  );
}
