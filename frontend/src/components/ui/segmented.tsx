import { cn } from "@/lib/cn";

/**
 * Small segmented control — two/three mutually-exclusive options shown as a
 * single button group with the active one highlighted. Clearer than a switch
 * when both choices need a visible label (no "is on = which?" ambiguity).
 */
export function Segmented<T extends string>({
  value,
  onChange,
  options,
  className,
  disabled = false,
}: {
  value: T;
  onChange: (v: T) => void;
  options: { value: T; label: string }[];
  className?: string;
  disabled?: boolean;
}) {
  return (
    <div
      className={cn(
        "inline-flex shrink-0 overflow-hidden rounded-md border border-border text-xs",
        className,
      )}
    >
      {options.map((o, i) => (
        <button
          key={o.value}
          type="button"
          disabled={disabled}
          aria-pressed={value === o.value}
          onClick={() => onChange(o.value)}
          className={cn(
            "px-2.5 py-1 transition-colors disabled:cursor-not-allowed disabled:opacity-60",
            i > 0 && "border-l border-border",
            value === o.value
              ? "bg-sidebar-active font-medium text-sidebar-active-foreground"
              : "bg-card text-muted-foreground hover:bg-muted",
          )}
        >
          {o.label}
        </button>
      ))}
    </div>
  );
}
