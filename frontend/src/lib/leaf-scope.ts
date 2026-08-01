/** Which visual world a shared component is rendering into.
 *
 * A handful of components serve BOTH the broker app and the member portal
 * (UtilizationView, CoverageCard, FlexCoverageCard, FlexPriceTagSummary, the
 * enrollment election UI…). Most of the difference between the two worlds is
 * already handled by tokens — `.leaf` re-points `--color-card`, `--color-border`
 * and friends for its subtree, so those components change appearance without
 * knowing anything.
 *
 * Tokens cannot re-point BEHAVIOUR, and one behaviour genuinely has to differ:
 * help behind hover. The broker app is a desktop tool where a hover tooltip is
 * the right density; the portal is mostly phones, where hover does not exist and
 * the content is simply unreachable (DESIGN.md: "Don't put help behind hover").
 * `InfoHint` reads this flag and switches presentation — the same children, told
 * two ways — so no shared component has to carry a `variant` prop through three
 * layers to say which surface it is on.
 *
 * Set it wherever `.leaf` itself is set (PortalShell, and the broker's
 * employee-view PortalFrame) and nowhere else: the flag and the class name are
 * two halves of the same statement and must not drift apart. */
import { createContext, useContext } from "react";

export const LeafScopeContext = createContext(false);

export function useInLeaf(): boolean {
  return useContext(LeafScopeContext);
}
