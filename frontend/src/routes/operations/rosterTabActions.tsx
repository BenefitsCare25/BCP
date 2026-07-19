import { createContext, useContext, type ReactNode } from "react";
import { createPortal } from "react-dom";

// Portal target for a roster tab's page-level action buttons. The RosterPage
// renders an empty slot on the tab row (right of the TabsList); each active tab
// portals its buttons into it via <RosterTabActions>. This standardizes the
// layout (actions on the tab row, like the Schema page) while keeping every
// button's handler and state local to its own tab component.
export const RosterActionsSlot = createContext<HTMLElement | null>(null);

export function RosterTabActions({ children }: { children: ReactNode }) {
  const slot = useContext(RosterActionsSlot);
  return slot ? createPortal(children, slot) : null;
}
