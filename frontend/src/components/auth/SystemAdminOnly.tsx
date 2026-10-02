import type { ReactNode } from "react";
import { useMe } from "@/api/hooks";

/** Broker portal actions that delete or clear saved data are platform-admin only. */
export function SystemAdminOnly({ children }: { children: ReactNode }) {
  const { data: me } = useMe();
  return me?.role === "system_admin" ? <>{children}</> : null;
}
