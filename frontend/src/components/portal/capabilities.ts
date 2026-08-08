/** What a member may still do, as the server serves it.
 *
 * The names are the values of `Capability` in
 * `backend/app/services/member_access.py`, and they arrive on every response as
 * `PortalMe.access.capabilities`. **Never re-derive them from `access.state`.**
 * Which capabilities a state carries is the server's table; a copy of it here
 * would go on showing the panel-card tab the day that table changes — the same
 * drift class as mirroring the pending-claim status set into TypeScript.
 *
 * Typed rather than bare strings because three surfaces close on this list —
 * the shell nav, the broker's preview frame and the home mosaic — and they are
 * entry points to the SAME destinations. A leaver whose Clinics tab is gone but
 * whose "Find a clinic" tile is still there just reaches the 403 by a different
 * route, which is exactly the "this app is broken" outcome the access notice
 * exists to prevent. A typo in any one of the three used to be invisible.
 */
export type Capability =
  | "record"
  | "respond"
  | "claim"
  | "elect"
  | "entitlement";

/** Whether a destination gated on `needs` should be shown.
 *
 * Two deliberate "yes"es. A destination that needs nothing is always shown; and
 * so is one whose requirement we cannot yet judge, because `capabilities` is
 * undefined until `/portal/me` resolves and blinking the nav away on every cold
 * load is worse than showing a tab whose endpoint would refuse. The endpoints
 * refuse whatever they must regardless — this is presentation, not a gate.
 */
export function holds(
  capabilities: readonly string[] | undefined,
  needs?: Capability,
): boolean {
  if (!needs) return true;
  if (!capabilities) return true;
  return capabilities.includes(needs);
}
