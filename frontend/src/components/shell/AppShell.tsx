import { useEffect, useState } from "react";
import { Loader2 } from "lucide-react";
import { Outlet, useRouterState } from "@tanstack/react-router";
import { useMe } from "@/api/hooks";
import { useSession } from "@/stores/session";
import { Sidebar } from "./Sidebar";
import { TopBar } from "./TopBar";
import { CompanyPickerDialog } from "./CompanyPickerDialog";
import { isCompanyPath } from "./nav";

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

  // The app shell owns scrolling through <main>. Lock the document while this
  // shell is mounted so viewport changes cannot create a second body scrollbar
  // or expose blank space through scroll chaining.
  useEffect(() => {
    const root = document.documentElement;
    const body = document.body;
    root.classList.add("app-shell-mounted");
    body.classList.add("app-shell-mounted");
    return () => {
      root.classList.remove("app-shell-mounted");
      body.classList.remove("app-shell-mounted");
    };
  }, []);

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
    <div className="fixed inset-0 flex w-full overflow-hidden">
      <Sidebar mobileOpen={mobileNavOpen} onClose={() => setMobileNavOpen(false)} />
      <div className="flex min-h-0 min-w-0 flex-1 flex-col overflow-hidden">
        <TopBar onMenuClick={() => setMobileNavOpen(true)} />
        <main className="min-h-0 flex-1 overscroll-contain overflow-x-hidden overflow-y-auto p-5">
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
