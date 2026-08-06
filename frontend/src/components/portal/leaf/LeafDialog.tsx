/** A modal on the member's leaf, built on the NATIVE `<dialog>` element.
 *
 * **This is not a stylistic preference — it is the only kind of modal this
 * world can have.** Every colour, radius and type step in the portal comes from
 * `.leaf`, a CLASS on an ancestor. A Radix/portal dialog renders into
 * `document.body`, outside that ancestor, so it would inherit the broker app's
 * tokens instead: the exact failure DESIGN.md already records for the member's
 * enrolment controls, which is why this surface uses native inputs throughout.
 * A native `<dialog>` is promoted to the browser's top layer WITHOUT leaving
 * its place in the DOM, so the cascade still reaches it and the modal is made
 * of the same glass as the page behind it.
 *
 * What the platform gives us, and we therefore do not reimplement: the focus
 * trap, initial focus, Escape to dismiss, `inert`ing the page behind, and a
 * real `::backdrop` that is not a positioned div competing on z-index.
 *
 * The two things it does NOT give us, both handled here:
 *
 *   1. **A click on the backdrop does not close it.** The `::backdrop` is not
 *      an element, so the click lands on the `<dialog>` box itself. The test is
 *      therefore geometric — was the point outside the dialog's own rect —
 *      rather than `e.target === dialogEl`, which is true for clicks on
 *      padding too and would dismiss a form when someone clicked the gap
 *      beside a label.
 *   2. **The page behind still scrolls** on some engines. Locked while open.
 *
 * On a phone it is a SHEET: full width, pinned to the bottom, rounded on top
 * only, and capped at 88vh so the page it came from stays visible behind it and
 * a long form scrolls inside rather than off. At `sm` and up it is a centred
 * pane. One element, two placements — never two components toggled by a query.
 */
import { useCallback, useEffect, useRef, type ReactNode } from "react";
import { X } from "lucide-react";
import { cn } from "@/lib/cn";
import { glassSurface } from "./Mount";

export function LeafDialog({
  open,
  onClose,
  title,
  gloss,
  children,
  className,
}: {
  open: boolean;
  onClose: () => void;
  title: string;
  gloss?: string;
  children: ReactNode;
  className?: string;
}) {
  const ref = useRef<HTMLDialogElement | null>(null);

  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    if (open && !el.open) el.showModal();
    if (!open && el.open) el.close();
  }, [open]);

  // The page behind must not scroll under the sheet — on a phone that reads as
  // the modal itself sliding away from the finger.
  useEffect(() => {
    if (!open) return;
    const prev = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    return () => {
      document.body.style.overflow = prev;
    };
  }, [open]);

  // Escape fires `cancel`/`close` on the element; the parent owns the state, so
  // route both back to it rather than letting the DOM and React disagree about
  // whether the dialog is open.
  const handleClose = useCallback(() => onClose(), [onClose]);

  return (
    <dialog
      ref={ref}
      onClose={handleClose}
      onCancel={(e) => {
        e.preventDefault();
        handleClose();
      }}
      onClick={(e) => {
        const el = ref.current;
        if (!el) return;
        const r = el.getBoundingClientRect();
        const outside =
          e.clientX < r.left ||
          e.clientX > r.right ||
          e.clientY < r.top ||
          e.clientY > r.bottom;
        // A synthetic click (Enter on a button) reports 0,0 — which is outside
        // every rect, and would close the dialog on the keystroke that
        // submitted it.
        if (outside && e.detail > 0) handleClose();
      }}
      aria-labelledby="leaf-dialog-title"
      className={cn(
        // `open:` because a `<dialog>` is `display:none` until it is shown, and
        // a flex display set unconditionally makes it visible while closed.
        "m-0 hidden max-h-[88vh] w-full max-w-none flex-col overflow-hidden p-0 open:flex",
        "mt-auto rounded-b-none rounded-t-tile",
        // Centred pane once there is room for one.
        "sm:m-auto sm:max-h-[85vh] sm:max-w-lg sm:rounded-tile",
        glassSurface,
        // The backdrop is the ground seen through smoke — warm, never a neutral
        // black, which on this near-white page reads as dirt (see leaf.css).
        "backdrop:bg-record/25 backdrop:backdrop-blur-sm",
        className,
      )}
    >
      {/* `autoFocus` on the HEADING, not left to the platform's default.
          Without it the browser's focusing steps land on the first focusable
          descendant — which is the close button — so the sheet opens with
          "Close" under the cursor and the first Enter dismisses the form the
          member just opened. `tabIndex={-1}` makes the heading focusable
          without adding it to the tab order. */}
      <div
        autoFocus
        tabIndex={-1}
        className="flex items-start justify-between gap-3 px-4 pb-3 pt-4 outline-none sm:px-5 sm:pt-5"
      >
        <div className="min-w-0">
          <h2
            id="leaf-dialog-title"
            className="text-md font-semibold leading-5 text-record"
          >
            {title}
          </h2>
          {gloss && <p className="mt-1 text-row text-label">{gloss}</p>}
        </div>
        <button
          type="button"
          onClick={handleClose}
          // 44px, per the Reach Rule — a close control is the one a member
          // reaches for in a hurry.
          className={
            "leaf-focus -mr-2 -mt-2 flex size-11 shrink-0 items-center justify-center " +
            "rounded-pill text-label transition-colors duration-200 ease-leaf " +
            "hover:bg-shade hover:text-record"
          }
        >
          <X className="size-5" aria-hidden />
          <span className="sr-only">Close</span>
        </button>
      </div>
      {/* The body scrolls, the header and whatever the caller pins do not. */}
      <div className="min-h-0 flex-1 overflow-y-auto px-4 pb-4 sm:px-5 sm:pb-5">
        {children}
      </div>
    </dialog>
  );
}
