import type { ReactNode } from "react";
import { Loader2, Search } from "lucide-react";
import { Input } from "@/components/ui/input";
import { cn } from "@/lib/cn";

export type EmployeePickerItem = {
  id: string;
  name: string;
  subtitle?: string;
  /** Optional right-aligned adornment (count badge, status dot, …). */
  trailing?: ReactNode;
};

/** Left-column list of employees/members shared by the coverage page and the
 * enrollment elections tab — one look for every "pick a person" column. */
export function EmployeePicker({
  items,
  selectedId,
  onSelect,
  isLoading = false,
  query,
  onQueryChange,
  searchPlaceholder = "Search name or staff ID",
  header,
  emptyText = "No employees match these filters.",
}: {
  items: EmployeePickerItem[];
  selectedId: string | null;
  onSelect: (id: string) => void;
  isLoading?: boolean;
  query?: string;
  onQueryChange?: (value: string) => void;
  searchPlaceholder?: string;
  header?: ReactNode;
  emptyText?: string;
}) {
  return (
    <div className="rounded-lg border border-border bg-card p-3">
      {onQueryChange && (
        <div className="relative mb-2">
          <Search className="pointer-events-none absolute left-2.5 top-1/2 size-4 -translate-y-1/2 text-muted-foreground" />
          <Input
            value={query ?? ""}
            onChange={(e) => onQueryChange(e.target.value)}
            placeholder={searchPlaceholder}
            className="pl-8"
          />
        </div>
      )}
      {header}
      <div className="max-h-[70vh] space-y-0.5 overflow-y-auto">
        {isLoading ? (
          <div className="flex items-center gap-2 px-2 py-3 text-xs text-muted-foreground">
            <Loader2 className="size-3.5 animate-spin" /> Loading…
          </div>
        ) : items.length === 0 ? (
          <p className="px-2 py-3 text-xs text-muted-foreground">{emptyText}</p>
        ) : (
          items.map((item) => (
            <button
              key={item.id}
              type="button"
              onClick={() => onSelect(item.id)}
              className={cn(
                "flex w-full items-center justify-between gap-2 rounded-md px-2.5 py-1.5 text-left text-sm transition-colors",
                selectedId === item.id
                  ? "bg-sidebar-active text-sidebar-active-foreground font-medium"
                  : "text-foreground hover:bg-muted",
              )}
            >
              <span className="min-w-0">
                <span className="block truncate">{item.name}</span>
                {item.subtitle && (
                  <span className="block font-mono text-2xs text-muted-foreground">
                    {item.subtitle}
                  </span>
                )}
              </span>
              {item.trailing}
            </button>
          ))
        )}
      </div>
    </div>
  );
}
