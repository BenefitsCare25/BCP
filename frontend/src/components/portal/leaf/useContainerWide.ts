/** "Is this COMPONENT wide enough", not "is the window".
 *
 * Two surfaces mount the portal's two-region layouts — the member's own page,
 * and the broker's employee-view preview, whose column is far narrower than the
 * window it sits in. A viewport media query hands that column a desktop layout
 * it cannot fit, which is the bug this hook exists to make impossible.
 *
 * **Measured synchronously first.** Left to the observer alone the component
 * paints its narrow layout for one commit before flipping — on every desktop
 * mount and every return to the tab, the layout visibly reflows. A layout
 * effect runs before paint, so setting the right answer there means there is
 * nothing to see.
 *
 * **It hands back a CALLBACK ref, not a `useRef` object, and that is the whole
 * API decision.** A ref object's identity never changes, so an effect keyed on
 * it runs exactly once — and if the measured element was not mounted on that
 * commit (a page that renders a loading skeleton first, which is every page
 * that fetches), `ref.current` is null, the observer is never attached, and the
 * hook reports "narrow" forever. It fails silently and it looks like a layout
 * that simply doesn't work at width. A callback ref fires when the node
 * arrives, so the measurement cannot be missed.
 *
 * Extracted from `Deck`, which had the only copy and the only element that
 * happened to always be mounted; the messages inbox hit the trap above on its
 * first render.
 */
import { useCallback, useLayoutEffect, useState } from "react";

export function useContainerWide(
  minWidth: number,
): [(node: HTMLElement | null) => void, boolean] {
  const [el, setEl] = useState<HTMLElement | null>(null);
  const [wide, setWide] = useState(false);

  useLayoutEffect(() => {
    if (!el) return;
    setWide(el.getBoundingClientRect().width >= minWidth);
    const ro = new ResizeObserver(([entry]) => {
      setWide(entry.contentRect.width >= minWidth);
    });
    ro.observe(el);
    return () => ro.disconnect();
  }, [el, minWidth]);

  // Stable, so passing it as `ref=` doesn't detach and reattach every render.
  const measure = useCallback((node: HTMLElement | null) => setEl(node), []);
  return [measure, wide];
}
