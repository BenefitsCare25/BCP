import * as React from "react";
import { cn } from "@/lib/cn";

/**
 * A native `<select>` styled to match the `Input` primitive.
 *
 * Native rather than the Radix `Select` where the option list is short and
 * plain: it keeps the OS picker on touch devices and needs no portal to escape
 * a Sheet's stacking context. Sizing, border and focus treatment come from the
 * same tokens as `Input` so a select never reads as a different tier of control
 * beside one.
 */
export const NativeSelect = React.forwardRef<
  HTMLSelectElement,
  React.SelectHTMLAttributes<HTMLSelectElement>
>(({ className, ...props }, ref) => (
  <select
    ref={ref}
    className={cn(
      "h-8 max-w-full rounded-md border border-input bg-card px-2 text-sm text-foreground shadow-sm transition-colors",
      "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring/40 focus-visible:border-ring",
      "disabled:cursor-not-allowed disabled:opacity-50",
      className,
    )}
    {...props}
  />
));
NativeSelect.displayName = "NativeSelect";
