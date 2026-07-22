import { useEffect } from "react";
import { Outlet, useRouterState } from "@tanstack/react-router";
import { useMe } from "@/api/hooks";
import { useSession } from "@/stores/session";
import { Sidebar } from "./Sidebar";
import { TopBar } from "./TopBar";
import { ContextBar } from "./ContextBar";
import { PAGE_TITLES } from "./nav";

const EXTRA_TITLES: Record<string, string> = {
  "/home": "Home",
  "/dashboard": "Company Dashboard",
};

/**
 * Adopt the server-resolved active client when our stored selection is unset OR
 * stale (not in accessible_clients — carried over from a previous user on this
 * browser, or access revoked). Lives here (not in ContextBar) so it runs on
 * EVERY app page, including firm-level pages that render no company control —
 * otherwise a stale tenant id would keep being sent until a company page mounts.
 */
function useActiveClientSync() {
  const { data: me } = useMe();
  const activeClientId = useSession((s) => s.activeClientId);
  const setActiveClient = useSession((s) => s.setActiveClient);
  useEffect(() => {
    if (!me) return;
    const accessible = new Set(me.accessible_clients.map((c) => c.id));
    const stale = activeClientId != null && !accessible.has(activeClientId);
    if ((activeClientId == null || stale) && me.active_client_id) {
      setActiveClient(me.active_client_id);
    }
  }, [me, activeClientId, setActiveClient]);
}

export function AppShell() {
  const router = useRouterState();
  const path = router.location.pathname;
  useActiveClientSync();
  const title =
    EXTRA_TITLES[path] ??
    PAGE_TITLES[path] ??
    "Inspro Configuration Platform";
  return (
    <div className="flex h-screen w-screen overflow-hidden">
      <Sidebar />
      <div className="flex-1 flex flex-col min-w-0">
        <TopBar title={title} />
        <ContextBar />
        <main className="flex-1 overflow-y-auto p-5">
          <Outlet />
        </main>
      </div>
    </div>
  );
}
