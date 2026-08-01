import * as React from "react";
import * as TooltipPrimitive from "@radix-ui/react-tooltip";
import { Info } from "lucide-react";
import { cn } from "@/lib/cn";
import { useInLeaf } from "@/lib/leaf-scope";
import { Hint } from "@/components/ui/hint";
import { Label } from "@/components/ui/label";

export const TooltipProvider = TooltipPrimitive.Provider;
export const Tooltip = TooltipPrimitive.Root;
export const TooltipTrigger = TooltipPrimitive.Trigger;

export const TooltipContent = React.forwardRef<
  React.ElementRef<typeof TooltipPrimitive.Content>,
  React.ComponentPropsWithoutRef<typeof TooltipPrimitive.Content>
>(({ className, sideOffset = 6, ...props }, ref) => (
  <TooltipPrimitive.Portal>
    <TooltipPrimitive.Content
      ref={ref}
      sideOffset={sideOffset}
      className={cn(
        "z-50 max-w-[17rem] rounded-md bg-foreground px-2.5 py-1.5 text-xs " +
          "leading-relaxed text-background shadow-md " +
          "data-[state=delayed-open]:animate-in data-[state=closed]:animate-out " +
          "data-[state=closed]:fade-out-0 data-[state=delayed-open]:fade-in-0 " +
          "data-[state=delayed-open]:zoom-in-95",
        className,
      )}
      {...props}
    />
  </TooltipPrimitive.Portal>
));
TooltipContent.displayName = "TooltipContent";

/**
 * Compact info affordance: a small ⓘ icon that reveals help text.
 * Use it in place of long inline helper paragraphs to keep forms dense.
 *
 * **Both surfaces open it on hover; they differ in what opens.** On the broker
 * app it is a Radix tooltip — a dark, compact chip, the right density for a
 * desktop tool. Inside the member portal (`useInLeaf`) it is `Hint`, a light
 * floating panel drawn in the member's own tokens, which ALSO opens on tap: the
 * portal is phone-first, Radix's tooltip has no touch trigger, and its portalled
 * content escapes `.leaf` and would arrive in the broker's tokens on a member's
 * screen. Several of the components carrying these hints (UtilizationView,
 * CoverageCard, FlexCoverageCard, FlexPriceTagSummary, the enrollment election
 * UI) render on BOTH surfaces, which is why the choice is made here from the
 * surrounding world rather than passed down as a prop from each of them.
 */
export function InfoHint({
  children,
  side = "top",
  className,
  label = "More information",
}: {
  children: React.ReactNode;
  side?: "top" | "right" | "bottom" | "left";
  className?: string;
  label?: string;
}) {
  const inLeaf = useInLeaf();
  if (inLeaf) {
    return (
      <Hint label={label} className={className}>
        {children}
      </Hint>
    );
  }
  return (
    <TooltipProvider delayDuration={150}>
      <Tooltip>
        <TooltipTrigger asChild>
          <button
            type="button"
            aria-label={label}
            className={cn(
              "-m-3 inline-flex size-11 items-center justify-center align-middle text-subtle " +
                "transition-colors hover:text-foreground focus-visible:text-foreground " +
                "focus-visible:outline-none",
              className,
            )}
          >
            <Info className="size-3.5" />
          </button>
        </TooltipTrigger>
        <TooltipContent side={side}>{children}</TooltipContent>
      </Tooltip>
    </TooltipProvider>
  );
}

/** A form label with an optional inline info hint — the compact replacement for a
 *  helper paragraph under the field. */
export function FieldLabel({
  children,
  hint,
  htmlFor,
  className,
}: {
  children: React.ReactNode;
  hint?: React.ReactNode;
  htmlFor?: string;
  className?: string;
}) {
  return (
    <div className={cn("flex items-center gap-1", className)}>
      <Label htmlFor={htmlFor}>{children}</Label>
      {hint ? <InfoHint>{hint}</InfoHint> : null}
    </div>
  );
}

/** Compact vertical field: label (+ optional tooltip hint) stacked over its
 *  control. Replaces the repeated `<div className="space-y-1"><Label/>…</div>`
 *  pattern and keeps helper text in a tooltip instead of a wrapping paragraph. */
export function Field({
  label,
  hint,
  htmlFor,
  className,
  children,
}: {
  label: React.ReactNode;
  hint?: React.ReactNode;
  htmlFor?: string;
  className?: string;
  children: React.ReactNode;
}) {
  return (
    <div className={cn("space-y-1.5", className)}>
      <FieldLabel hint={hint} htmlFor={htmlFor}>
        {label}
      </FieldLabel>
      {children}
    </div>
  );
}
