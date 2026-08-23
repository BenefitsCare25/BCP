import { useEffect, useState } from "react";
import { Loader2 } from "lucide-react";
import { Outlet, useRouterState } from "@tanstack/react-router";
import { useMe } from "@/api/hooks";
import { useSession } from "@/stores/session";
import { Sidebar } from "./Sidebar";
import { TopBar } from "./TopBar";
import { ContextBar } from "./ContextBar";
import { CompanyPickerDialog } from "./CompanyPickerDialog";
import { isCompanyPath, PAGE_TITLES } from "./nav";

const EXTRA_TITLES: Record<string, string> = {
  "/home": "Home",
  "/dashboard": "Company Dashboard",
};

/**
 * Keep the stored active client honest for the HARD gate: clear a stale
 * selection (revoked / carried over from a previous user) so the picker prompts
 * instead of silently acting on an inaccessible client, and auto-enter the sole
 * company when there's no real choice. Crucially it does NOT adopt the server
 * default — under the hard gate, acting on a company must be a deliberate choice
 * (see CompanyPickerDialog). Runs on every app page.
 */
function useActiveClientSync() {
  const { data: me } = useMe();
  const activeClientId = useSession((s) => s.activeClientId);
  const setActiveClient = useSession((s) => s.setActiveClient);
  useEffect(() => {
    if (!me) return;
    const accessible = me.accessible_clients;
    const ids = new Set(accessible.map((c) => c.id));
    if (activeClientId != null && !ids.has(activeClientId)) {
      setActiveClient(null); // stale → force a fresh pick
    } else if (activeClientId == null && accessible.length === 1) {
      setActiveClient(accessible[0].id); // no real choice → auto-enter
    }
  }, [me, activeClientId, setActiveClient]);
}

export function AppShell() {
  const router = useRouterState();
  const path = router.location.pathname;
  useActiveClientSync();
  const { data: me } = useMe();
  const activeClientId = useSession((s) => s.activeClientId);

  // Mobile nav drawer state (lg+ shows the sidebar statically). Close on every
  // navigation so tapping a link dismisses the drawer.
  const [mobileNavOpen, setMobileNavOpen] = useState(false);
  useEffect(() => {
    setMobileNavOpen(false);
  }, [path]);

  const title =
    EXTRA_TITLES[path] ??
    PAGE_TITLES[path] ??
    "Inspro Configuration Platform";

  // Hard gate: a company page needs a deliberately chosen company. While we
  // don't yet know the caller's companies, hold the page (avoid a flash of the
  // default tenant); with a real choice pending, prompt; otherwise render.
  const gate: "loading" | "pick" | "ready" =
    isCompanyPath(path) && activeClientId == null
      ? !me
        ? "loading"
        : me.accessible_clients.length > 1
          ? "pick"
          : "ready"
      : "ready";

  return (
    <div className="flex h-screen w-screen overflow-hidden">
      <Sidebar mobileOpen={mobileNavOpen} onClose={() => setMobileNavOpen(false)} />
      <div className="flex-1 flex flex-col min-w-0">
        <TopBar title={title} onMenuClick={() => setMobileNavOpen(true)} />
        <ContextBar />
        <main className="min-h-0 flex-1 overflow-y-auto p-5">
          {gate === "loading" ? (
            <div className="flex items-center gap-2 p-8 text-sm text-muted-foreground">
              <Loader2 className="size-4 animate-spin" /> Loading…
            </div>
          ) : gate === "pick" ? null : (
            <Outlet />
          )}
        </main>
      </div>
      {gate === "pick" && <CompanyPickerDialog />}
    </div>
  );
}
