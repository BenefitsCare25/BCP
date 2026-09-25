import type { ReactNode } from "react";
import { AlertTriangle, X } from "lucide-react";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/cn";

/** The editor frame shared by every claim-limit cell: title, labelled rows,
 * then the action bar. */
export function EditorShell({
  title,
  onClose,
  footer,
  children,
}: {
  title: ReactNode;
  onClose: () => void;
  footer: ReactNode;
  children: ReactNode;
}) {
  return (
    <div className="rounded-md border border-border bg-card">
      <div className="flex items-center justify-between gap-3 border-b border-border px-4 py-2.5">
        <p className="min-w-0 truncate text-sm font-semibold text-foreground">{title}</p>
        <Button type="button" size="icon" variant="ghost" className="size-8 shrink-0" aria-label="Close" onClick={onClose}>
          <X className="size-4" aria-hidden />
        </Button>
      </div>
      <div className="flex flex-col gap-4 px-4 py-4">{children}</div>
      <div className="flex flex-wrap items-center gap-2 border-t border-border bg-muted/30 px-4 py-2.5">{footer}</div>
    </div>
  );
}

/** One labelled row: label left at width, stacked on a phone. */
export function Field({
  label,
  htmlFor,
  labelId,
  children,
}: {
  label: string;
  htmlFor?: string;
  labelId?: string;
  children: ReactNode;
}) {
  const labelClass = "text-xs font-medium text-muted-foreground sm:pt-1.5";
  return (
    <div className="grid gap-1.5 sm:grid-cols-[9rem_minmax(0,1fr)] sm:gap-4">
      {htmlFor ? (
        <label htmlFor={htmlFor} className={labelClass}>{label}</label>
      ) : (
        <span id={labelId} className={labelClass}>{label}</span>
      )}
      <div className="min-w-0">{children}</div>
    </div>
  );
}

export interface SegmentOption<T extends string> {
  value: T;
  label: string;
  disabled?: boolean;
}

/** A single-choice pill group backed by real radios. */
export function Segmented<T extends string>({
  name,
  labelledBy,
  value,
  options,
  onChange,
}: {
  name: string;
  labelledBy: string;
  value: T | null;
  options: SegmentOption<T>[];
  onChange: (value: T) => void;
}) {
  return (
    <div role="radiogroup" aria-labelledby={labelledBy} className="flex flex-wrap gap-1.5">
      {options.map((option) => {
        const checked = value === option.value;
        return (
          <label
            key={option.value}
            className={cn(
              "relative inline-flex min-h-8 items-center rounded-full border px-3 text-xs transition-colors",
              "has-[:focus-visible]:ring-2 has-[:focus-visible]:ring-ring/50",
              option.disabled
                ? "cursor-not-allowed border-border text-muted-foreground/60"
                : checked
                  ? "cursor-pointer border-primary bg-primary text-primary-foreground"
                  : "cursor-pointer border-border text-foreground hover:bg-muted",
            )}
          >
            <input
              type="radio"
              name={name}
              className="absolute inset-0 m-0 cursor-[inherit] appearance-none rounded-full opacity-0"
              checked={checked}
              disabled={option.disabled}
              onChange={() => onChange(option.value)}
            />
            {option.label}
          </label>
        );
      })}
    </div>
  );
}

export function Notice({ children }: { children: ReactNode }) {
  return (
    <p className="flex items-start gap-2 rounded-md border border-warn/40 bg-warn/10 px-3 py-2 text-xs text-foreground">
      <AlertTriangle className="mt-0.5 size-3.5 shrink-0 text-warn" aria-hidden />
      {children}
    </p>
  );
}

export function Preview({ children }: { children: ReactNode }) {
  return <p className="rounded-md bg-muted/50 px-3 py-2 text-sm text-foreground">{children}</p>;
}

export const DRAWDOWN_OPTIONS = (canDraw = true): SegmentOption<"drawdown" | "display">[] => [
  { value: "drawdown", label: "Allow drawdown from limit", disabled: !canDraw },
  { value: "display", label: "No drawdown" },
];
