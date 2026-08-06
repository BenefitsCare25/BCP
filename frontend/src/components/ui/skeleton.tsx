import { cn } from "@/lib/cn";

/**
 * Loading placeholder. Use in place of plain "Loading…" text. Width can be
 * tuned per use site via className.
 */
export function Skeleton({
  className,
  ...props
}: React.HTMLAttributes<HTMLDivElement>) {
  return (
    <div
      className={cn(
        "animate-pulse rounded-md bg-muted/60",
        className,
      )}
      aria-hidden="true"
      {...props}
    />
  );
}

interface SkeletonTableProps {
  rows?: number;
  columns?: number;
  /** Tailwind width tokens per column, e.g. ["w-24", "flex-1", "w-16"]. */
  columnWidths?: string[];
}

export function SkeletonTable({
  rows = 5,
  columns = 5,
  columnWidths,
}: SkeletonTableProps) {
  const widths =
    columnWidths ??
    Array.from({ length: columns }, (_, i) =>
      i === 1 ? "flex-1" : "w-24",
    );
  return (
    <div
      role="status"
      aria-label="Loading rows"
      className="flex flex-col gap-3 py-2"
    >
      {Array.from({ length: rows }).map((_, r) => (
        <div key={r} className="flex items-center gap-3">
          {widths.map((w, c) => (
            <Skeleton key={c} className={cn("h-4", w)} />
          ))}
        </div>
      ))}
    </div>
  );
}

