import type { ReactNode } from "react";
import type { SobColumn } from "@/types";

/** Benefit × plan table shell shared by the channel and benefit grids, so
 * both line up column for column. */
export function LimitGrid({
  id,
  title,
  hideTitle = false,
  rowHeader,
  columns,
  children,
}: {
  id: string;
  title: string;
  /** Keep the table's accessible name without printing a heading. */
  hideTitle?: boolean;
  rowHeader: string;
  columns: SobColumn[];
  children: ReactNode;
}) {
  return (
    <div className="space-y-2">
      <h4 id={id} className={hideTitle ? "sr-only" : "text-sm font-semibold text-foreground"}>{title}</h4>
      <div className="overflow-x-auto rounded-md border border-border">
        {/* Fixed layout: cells wrap inside their share of the width instead of
            stretching the table past its container; it scrolls only once each
            plan column would drop below 11rem. */}
        <table
          className="w-full table-fixed border-collapse text-sm"
          style={{ minWidth: `${12 + columns.length * 11}rem` }}
          aria-labelledby={id}
        >
          <thead className="bg-muted/60">
            <tr className="border-b border-border">
              <th className="w-48 bg-muted px-3 py-2 text-left text-2xs font-medium uppercase tracking-wider text-muted-foreground sm:sticky sm:left-0 sm:z-10 sm:w-56">
                {rowHeader}
              </th>
              {columns.map((col) => (
                <th key={col.id} className="px-2 py-2 text-left text-2xs font-medium uppercase tracking-wider text-muted-foreground">
                  <span className="block truncate" title={col.label}>{col.label}</span>
                </th>
              ))}
            </tr>
          </thead>
          <tbody>{children}</tbody>
        </table>
      </div>
    </div>
  );
}
