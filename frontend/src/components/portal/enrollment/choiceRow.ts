/** The selectable row, shared by the plan picker and the family picker.
 *
 * Only the CONTAINER is shared. The two rows carry different content (a tier
 * carries figures, a dependant carries a relationship) and abstracting over
 * that would buy one component with two shapes; what must not drift is the
 * geometry and the selected treatment, because a plan row and a family row sit
 * fifty pixels apart inside one mount.
 *
 * Three things it fixes by construction:
 *   - **44px by construction** (`min-h-11`), so a row can never fall under the
 *     Reach Rule as its content shrinks — the decline row carries one line.
 *   - **Selection is `shade`, never terracotta.** These rows PICK, they do not
 *     DO (The Do-vs-Pick Rule), so they are marked exactly the way the nav, the
 *     dock and the tab strip mark theirs.
 *   - **`-mx-2 px-2`** bleeds the fill two steps past the text so a selected
 *     row reads as a band rather than a boxed word, while every label still
 *     starts on the mount's own left edge (The One-Left-Edge Rule). */
import { cn } from "@/lib/cn";

export function choiceRowClass(selected: boolean, extra?: string): string {
  return cn(
    "-mx-2 flex min-h-11 cursor-pointer gap-3 rounded-control px-2 py-2.5",
    "transition-colors duration-200 ease-leaf",
    selected ? "bg-shade/70" : "hover:bg-shade/40",
    extra,
  );
}

/** The control itself. A native input, not the shared Radix primitives: it
 * inherits the member's own tokens with no portal to escape `.leaf`, it is what
 * a phone's assistive tech expects, and `accent-action` puts it in the portal's
 * touch colour without restyling a box. */
export const choiceControl = "leaf-focus size-4 shrink-0 accent-action";
