import type { ReactNode } from "react";
import { useMe } from "@/api/hooks";
import { isSystemAdminRole } from "@/lib/roles";

/** Platform-only UI: the shared AI provider, platform credentials and limits,
 *  AI policy library writes and the platform console. Firm-owner actions
 *  (users, removal of saved data) use `FirmOwnerOnly` instead. */
export function SystemAdminOnly({ children }: { children: ReactNode }) {
  const { data: me } = useMe();
  return isSystemAdminRole(me?.role) ? <>{children}</> : null;
}
