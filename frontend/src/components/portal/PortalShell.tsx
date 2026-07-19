/** Slim member-facing shell — top nav, no ClientSwitcher / policy-year picker
 * (a member is pinned to one client and the active policy year server-side). */
import { Link, Outlet, useNavigate, useRouterState } from "@tanstack/react-router";
import { LogOut, ShieldCheck } from "lucide-react";
import { usePortalMe } from "@/api/portal";
import { usePortalSession } from "@/stores/portalSession";
import { formatPolicyRange } from "@/lib/policy-year";
import { cn } from "@/lib/cn";
import { Button } from "@/components/ui/button";

// Mirrored by the broker preview in components/operations/PortalFrame —
// change both together.
const NAV = [
  { label: "My coverage", to: "/portal/coverage" },
  { label: "My card", to: "/portal/card" },
  { label: "My claims", to: "/portal/claims" },
  { label: "Find a clinic", to: "/portal/clinics" },
  { label: "My enrollment", to: "/portal/enrollment" },
] as const;

export function PortalShell() {
  const { location } = useRouterState();
  const navigate = useNavigate();
  const member = usePortalSession((s) => s.member);
  const clearSession = usePortalSession((s) => s.clearSession);
  const { data: me } = usePortalMe();

  const signOut = () => {
    clearSession();
    void navigate({ to: "/portal/sign-in" });
  };

  return (
    <div className="min-h-screen bg-background">
      <header className="border-b border-border bg-card">
        <div className="mx-auto flex h-14 max-w-4xl items-center justify-between px-4">
          <div className="flex items-center gap-2">
            <ShieldCheck className="size-5 text-primary" />
            <span className="text-sm font-semibold text-foreground">
              My Benefits Portal
            </span>
            {me?.policy_year && (
              <span className="ml-2 hidden text-xs text-muted-foreground sm:inline">
                {formatPolicyRange(me.policy_year.start_date, me.policy_year.end_date)}
              </span>
            )}
          </div>
          <div className="flex items-center gap-3">
            <span className="hidden text-xs text-muted-foreground sm:inline">
              {member?.display_name || member?.email}
            </span>
            <Button variant="ghost" size="sm" onClick={signOut}>
              <LogOut className="size-4" />
              <span className="ml-1">Sign out</span>
            </Button>
          </div>
        </div>
        <nav className="mx-auto flex max-w-4xl gap-1 px-4 pb-2">
          {NAV.map((item) => {
            const active = location.pathname.startsWith(item.to);
            // Pulse the enrollment tab while a window is open so members
            // don't miss the deadline.
            const highlight =
              item.to === "/portal/enrollment" && me?.enrollment_open && !active;
            return (
              <Link
                key={item.to}
                to={item.to}
                className={cn(
                  "inline-flex items-center gap-1.5 rounded-md px-3 py-1.5 text-sm transition-colors",
                  active
                    ? "bg-sidebar-active text-sidebar-active-foreground font-medium"
                    : "text-muted-foreground hover:bg-muted hover:text-foreground",
                )}
              >
                {item.label}
                {highlight && (
                  <span
                    className="size-1.5 rounded-full bg-warn"
                    title="Enrollment window open"
                  />
                )}
              </Link>
            );
          })}
        </nav>
      </header>
      <main className="mx-auto max-w-4xl px-4 py-6">
        <Outlet />
      </main>
    </div>
  );
}
