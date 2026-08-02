/** The chip that PICKS a view — the claims filter strip, the clinic type row,
 * and anything else that switches what a member is looking at.
 *
 * The sibling of `enrollment/choiceRow.ts`, and here for the same reason: two
 * strips of chips written a fortnight apart already carried the recipe
 * verbatim, and the moment one of them is nudged the two stop agreeing about
 * what "selected" looks like on the member's surface.
 *
 * Only the TREATMENT is shared, never the geometry. A filter strip's chips
 * stretch to fill their band and a type row's chips are sized to their label,
 * so each caller passes its own box; what may not drift is:
 *   - **44px by construction** (`min-h-11`), the Reach Rule floor.
 *   - **Selection is `shade`, never terracotta.** These chips PICK, they do not
 *     DO (The Do-vs-Pick Rule), so they are marked exactly the way the nav, the
 *     dock, the tab strip and the enrollment choice rows mark theirs. */
import { cn } from "@/lib/cn";

export function pickChipClass(selected: boolean, extra?: string): string {
  return cn(
    "leaf-focus min-h-11 rounded-pill text-row",
    "transition-colors duration-200 ease-leaf",
    selected
      ? "bg-shade font-semibold text-record"
      : "text-label hover:bg-shade/60 hover:text-record",
    extra,
  );
}
