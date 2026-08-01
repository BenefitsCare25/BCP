/** Optional help, revealed as a floating pop-up.
 *
 * **It floats; it does not expand the page.** This used to render the panel
 * inline, which pushed everything below it down and re-flowed whatever row the
 * trigger sat in — on the claim form's header the row visibly jumped as the
 * panel opened. Help is a layer over the page, not a part of it.
 *
 * **Hover opens it, and tap opens it too.** Hover is the interaction on a
 * desktop and it is what this is tuned for. The tap path is kept because the
 * member portal is phone-first and a touch device has no hover state at all —
 * behind hover ALONE this content would be unreachable there, not merely
 * awkward. Both paths open the same panel, so there is one thing to style and
 * one thing to test.
 *
 * Three constructional choices worth keeping:
 *
 * - **The panel is not portalled.** A portalled panel escapes the `.leaf`
 *   subtree and would render in the broker's tokens (its red ring, its type) on
 *   the member's screen. Positioned against its own trigger it inherits
 *   whichever world it was opened in. It anchors to the trigger's RIGHT edge and
 *   grows leftward: these triggers sit at the end of rows, so an
 *   left-anchored panel runs off the screen on a phone.
 * - **Every element is a `<span>`.** These sit inside `<p>` and `<label>` rows
 *   all over the product, where a `<div>` is invalid HTML and browsers silently
 *   close the paragraph around it. Display comes from utilities instead.
 * - **`pointerType` is checked, not assumed.** A touch tap fires `pointerenter`
 *   before `click`; without the guard the enter would open the panel and the
 *   click would immediately toggle it shut, so tapping a hint on a phone would
 *   do nothing at all.
 *
 * The trigger is a full 44×44 target pulled back to ~20px of layout box with a
 * negative margin, so it satisfies The Reach Rule without opening a hole in a
 * dense row. */
import { useEffect, useId, useRef, useState, type ReactNode } from "react";
import { HelpCircle } from "lucide-react";
import { cn } from "@/lib/cn";

export function Hint({
  children,
  label = "More information",
  className,
}: {
  children: ReactNode;
  label?: string;
  className?: string;
}) {
  const [open, setOpen] = useState(false);
  const id = useId();
  const wrap = useRef<HTMLSpanElement>(null);

  // Escape closes, and so does a pointer landing anywhere else — the latter is
  // what dismisses a tap-opened panel on a phone, where there is no
  // "pointer left the trigger" event to close it.
  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") setOpen(false);
    };
    const onDown = (e: PointerEvent) => {
      if (!wrap.current?.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener("keydown", onKey);
    document.addEventListener("pointerdown", onDown);
    return () => {
      document.removeEventListener("keydown", onKey);
      document.removeEventListener("pointerdown", onDown);
    };
  }, [open]);

  return (
    <span
      ref={wrap}
      className={cn("relative inline-flex align-middle", className)}
      onPointerEnter={(e) => {
        if (e.pointerType !== "touch") setOpen(true);
      }}
      onPointerLeave={(e) => {
        if (e.pointerType !== "touch") setOpen(false);
      }}
    >
      <button
        type="button"
        aria-label={label}
        aria-expanded={open}
        aria-controls={open ? id : undefined}
        onClick={() => setOpen((o) => !o)}
        onFocus={() => setOpen(true)}
        onBlur={() => setOpen(false)}
        className="focus-ring -my-3 inline-flex size-11 shrink-0 items-center justify-center text-subtle"
      >
        <HelpCircle className="size-4" aria-hidden />
      </button>
      {open && (
        <span
          id={id}
          className={cn(
            "absolute right-0 top-full z-30 mt-1 block w-max",
            // Capped against the VIEWPORT, not a rem constant alone: at 390px a
            // fixed 17rem panel plus the page's own padding overflows the
            // screen and takes the whole document into horizontal scroll.
            "max-w-[min(18rem,calc(100vw-2.5rem))]",
            "rounded-hint border border-border bg-card p-3 shadow-lg",
            // normal-case / tracking-normal: these hints hang off uppercase
            // section labels ("ADD YOUR FAMILY"), and without the reset the
            // panel inherited the label tier's casing and letter-spacing and
            // rendered a paragraph of tracked capitals.
            "text-left text-[0.8125rem] font-normal normal-case leading-5 tracking-normal text-muted-foreground",
          )}
        >
          {children}
        </span>
      )}
    </span>
  );
}
