import { useEffect, useState } from "react";
import { Loader2 } from "lucide-react";
import { Outlet, useRouterState } from "@tanstack/react-router";
import { useMe } from "@/api/hooks";
import { useSession } from "@/stores/session";
import {
  resetClientSelection,
  useClientSelectionNotice,
} from "@/stores/clientSelection";
import { Sidebar } from "./Sidebar";
import { TopBar } from "./TopBar";
import { CompanyPickerDialog } from "./CompanyPickerDialog";
import { NoCompanyNotice } from "./NoCompanyNotice";
import { isCompanyPath } from "./nav";

/**
 * Keep the stored active client honest for the HARD gate: clear a stale
 * selection (revoked / carried over from a previous user) so the picker prompts
 * instead of silently acting on an inaccessible client, and auto-enter the sole
 * company when there's no real choice. Crucially it does NOT adopt the server
 * default — under the hard gate, acting on a company must be a deliberate choice
 * (see CompanyPickerDialog). Runs on every app page.
 *
 * Stale is judged by the company /me was actually answered for. Reads with an
 * unusable `X-Inspro-Client` fall back to the caller's default company, so a
 * selection the server did not honour means every cached read may hold another
 * company's data: the selection AND the cache go.
 */
function useActiveClientSync() {
  const { data: me } = useMe();
  const activeClientId = useSession((s) => s.activeClientId);
  const setActiveClient = useSession((s) => s.setActiveClient);
  useEffect(() => {
    if (!me) return;
    const accessible = me.accessible_clients;
    if (activeClientId != null && me.active_client_id !== activeClientId) {
      resetClientSelection(); // stale → force a fresh pick
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
  const selectionNotice = useClientSelectionNotice((s) => s.notice);
  const clearSelectionNotice = useClientSelectionNotice((s) => s.clear);

  // The notice explains one refused write; a company chosen by any route
  // (picker, top bar, Home, single-company auto-enter) settles it.
  useEffect(() => {
    if (activeClientId != null && selectionNotice != null) clearSelectionNotice();
  }, [activeClientId, selectionNotice, clearSelectionNotice]);

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
  // default tenant); with a real choice pending, prompt; with no company at
  // all, say so instead of rendering a page every read of which is refused;
  // otherwise render.
  const gate: "loading" | "pick" | "none" | "ready" =
    isCompanyPath(path) && path !== "/settings/ai" && activeClientId == null
      ? !me
        ? "loading"
        : me.accessible_clients.length > 1
          ? "pick"
          : me.accessible_clients.length === 0
            ? "none"
            : "ready"
      : "ready";
  // A request refused for its company selection still needs a company chosen
  // on pages that don't gate on one (Home, firm-wide pages). There the picker
  // is offered rather than imposed.
  const offerPicker =
    gate === "ready" &&
    selectionNotice != null &&
    activeClientId == null &&
    (me?.accessible_clients.length ?? 0) > 1;

  return (
    <div className="fixed inset-0 flex w-full overflow-hidden">
      <Sidebar mobileOpen={mobileNavOpen} onClose={() => setMobileNavOpen(false)} />
      <div className="flex min-h-0 min-w-0 flex-1 flex-col overflow-hidden">
        <TopBar onMenuClick={() => setMobileNavOpen(true)} menuOpen={mobileNavOpen} />
        <main className="min-h-0 flex-1 overscroll-contain overflow-x-hidden overflow-y-auto p-5">
          {gate === "loading" ? (
            <div className="flex items-center gap-2 p-8 text-sm text-muted-foreground">
              <Loader2 className="size-4 animate-spin" /> Loading…
            </div>
          ) : gate === "pick" ? null : gate === "none" ? (
            <NoCompanyNotice role={me?.role} />
          ) : (
            <Outlet />
          )}
        </main>
      </div>
      {(gate === "pick" || offerPicker) && (
        <CompanyPickerDialog dismissible={gate !== "pick"} />
      )}
    </div>
  );
}
