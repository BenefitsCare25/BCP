import type { ReactNode } from "react";
import { useWriteAccess } from "@/lib/roles";

/** Firm-owner actions — users, invitations, web addresses and every broker
 *  portal control that removes, clears, unlinks or resets saved data — for a
 *  firm's `firm_admin` and the platform's `system_admin`, except under a
 *  read-only access grant. The API enforces the same rule; this only keeps the
 *  controls off screen for everyone else. */
export function FirmOwnerOnly({ children }: { children: ReactNode }) {
  const { canRemove } = useWriteAccess();
  return canRemove ? <>{children}</> : null;
}
