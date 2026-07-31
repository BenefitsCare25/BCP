import * as React from "react";
import { cn } from "@/lib/cn";

/**
 * The app's ONE label tier — uppercase micro-caps, shared by `Label` (form
 * controls) and `SectionLabel` (headings, definition terms, captions).
 *
 * There is deliberately a single size. Detail panels used to run two
 * near-identical uppercase tiers — 10px for field labels against 12px for the
 * section headings above them — which is too small a step to read as hierarchy
 * and left the panels looking flat. Hierarchy comes from structure (a rule, a
 * gap, position), not from a 2px difference in the same uppercase treatment.
 *
 * Import this constant rather than retyping the classes, so the two primitives
 * can never drift apart again.
 */
export const labelClass =
  "text-2xs font-medium uppercase tracking-wider text-muted-foreground";

type SectionLabelProps = React.HTMLAttributes<HTMLElement> & {
  /** Element to render. Use `dt` inside a `<dl>` and `h3` for a section
   * heading — a caption must NOT be a `<label>`, which is what `Label` is for
   * and which is invalid without an associated control. */
  as?: "div" | "span" | "p" | "h3" | "h4" | "dt";
};

export function SectionLabel({
  as: Comp = "div",
  className,
  ...props
}: SectionLabelProps) {
  return <Comp className={cn(labelClass, className)} {...props} />;
}
