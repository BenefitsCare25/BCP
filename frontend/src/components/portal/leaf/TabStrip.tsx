/** The section switcher inside a page — Coverage's three readings.
 *
 * It is the same statement the chrome already makes one level up, so it is made
 * of the same material: a glass pill carrying every destination at once, the
 * current one filled in `bg-shade` with ink text. That is exactly the desktop
 * nav's treatment and the phone dock's, and it is deliberately NOT the brand —
 * a red rule under the active tab would be the brand's third appearance on a
 * screen that already spends both (The Twice Rule).
 *
 * It replaces the previous printed underline strip, which was the old world's
 * device: an ink rule on a matte ground, drawn rather than lit.
 *
 * Exported as wrappers rather than class strings because both surfaces drive
 * real Radix `Tabs` — the member page from the router, the broker's
 * employee-view preview from local state — so there are no route generics to
 * erase, and one component means the two cannot drift. */
import type { ReactNode } from "react";
import { TabsList, TabsTrigger } from "@/components/ui/tabs";
import { cn } from "@/lib/cn";
import { glassSurface } from "./Mount";

export function LeafTabsList({
  children,
  className,
  label,
}: {
  children: ReactNode;
  className?: string;
  /** Names the group for a screen reader — "Coverage", not "Tabs". */
  label?: string;
}) {
  return (
    <TabsList
      aria-label={label}
      className={cn(
        glassSurface,
        // Full width on a phone, where the triggers share the width equally so
        // a three-word label never truncates. On desktop it shrinks to its
        // labels: stretched across a 1180px column, three pills stop reading as
        // one control and start reading as three buttons.
        // The shared list's bottom rule and padding are overridden here rather
        // than in the primitive — the broker app still wants them.
        "flex h-auto w-full gap-1 rounded-pill p-1.5 sm:inline-flex sm:w-auto",
        className,
      )}
    >
      {children}
    </TabsList>
  );
}

export function LeafTabsTrigger({
  value,
  children,
  className,
}: {
  value: string;
  children: ReactNode;
  /** Only for a surface that seats the strip somewhere tighter than the page
   * column — see the head rail on `routes/portal/coverage`. */
  className?: string;
}) {
  return (
    <TabsTrigger
      value={value}
      className={cn(
        "leaf-focus min-h-11 flex-1 rounded-pill border-0 px-2 text-row font-medium sm:flex-none sm:px-6",
        "text-label shadow-none transition-colors duration-200 ease-leaf hover:text-record",
        "data-[state=active]:border-0 data-[state=active]:bg-shade",
        "data-[state=active]:font-semibold data-[state=active]:text-record",
        "data-[state=active]:shadow-none",
        className,
      )}
    >
      {children}
    </TabsTrigger>
  );
}
