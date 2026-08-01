/** The centre slot of the page's heading row.
 *
 * The heading row (member's name on the left, benefit-year control on the
 * right) belongs to the shell, but the thing that wants its middle — Coverage's
 * three-tab strip — belongs to a route. This is the seam: the shell hangs an
 * empty element in the row and publishes it; a route renders into it through a
 * React portal, so the strip is *in* that row in the DOM while staying inside
 * its own `Tabs` provider in the React tree.
 *
 * **It renders in place when there is no rail, and that fallback is the whole
 * safety property.** Two surfaces mount leaf routes — `PortalShell` and the
 * broker's employee-view preview (`components/operations/PortalFrame`, whose
 * heading row is inside a much narrower column and does not offer a rail) — and
 * a slot that rendered nothing without a target would silently delete the
 * Coverage tabs from the preview.
 *
 * **One instance, never two.** The obvious alternative is to render the strip
 * twice and toggle the copies with `hidden`, the way the shell already toggles
 * its two `h1`s. That works for a heading; it does not work for a tablist,
 * which owns roving focus and `aria-controls` wiring that a second live copy
 * duplicates. The rail is therefore mounted by a media query in JS rather than
 * by a CSS breakpoint. */
import {
  createContext,
  useContext,
  useEffect,
  useState,
  type ReactNode,
} from "react";
import { createPortal } from "react-dom";

const HeadRailContext = createContext<HTMLElement | null>(null);

export const HeadRailProvider = HeadRailContext.Provider;

/** True at `lg` and up — the width at which the heading row can seat the name,
 * a tab strip and the year control without the name truncating to nothing.
 * Below it the rail is not rendered at all, so `HeadRail` falls back to
 * rendering its children where the route put them. Kept in step with the `lg:`
 * sizing the route applies to the strip: the breakpoint and the rail's
 * existence are one decision. */
export function useHeadRailWidth() {
  const [wide, setWide] = useState(
    () => window.matchMedia("(min-width: 64rem)").matches,
  );
  useEffect(() => {
    const mq = window.matchMedia("(min-width: 64rem)");
    const sync = () => setWide(mq.matches);
    sync();
    mq.addEventListener("change", sync);
    return () => mq.removeEventListener("change", sync);
  }, []);
  return wide;
}

export function HeadRail({ children }: { children: ReactNode }) {
  const rail = useContext(HeadRailContext);
  return rail ? createPortal(children, rail) : <>{children}</>;
}
