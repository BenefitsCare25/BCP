import * as React from "react";
import * as LabelPrimitive from "@radix-ui/react-label";
import { labelClass } from "@/components/ui/section-label";
import { cn } from "@/lib/cn";

/** Label for a FORM CONTROL — renders a real `<label>`, so it needs `htmlFor`
 * (or a wrapped control). For a section heading or a definition term use
 * `SectionLabel`, which shares this exact treatment without the `<label>`
 * semantics. */
export const Label = React.forwardRef<
  React.ElementRef<typeof LabelPrimitive.Root>,
  React.ComponentPropsWithoutRef<typeof LabelPrimitive.Root>
>(({ className, ...props }, ref) => (
  <LabelPrimitive.Root ref={ref} className={cn(labelClass, className)} {...props} />
));
Label.displayName = "Label";
