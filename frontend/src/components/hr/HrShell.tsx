/** Authenticated HR admin shell — top bar (company + user + sign out) over the
 * routed content. A tenant is pinned by the subdomain, so there is no client
 * switcher. */
import { Link, Outlet, useNavigate, useRouterState } from "@tanstack/react-router";
import { Building2, LogOut, ShieldCheck } from "lucide-react";
import { useHrMe } from "@/api/hr";
import { hrApi } from "@/api/hrClient";
import { useHrSession } from "@/stores/hrSession";
import { Button } from "@/components/ui/button";
import { NotificationBell } from "@/components/shell/NotificationBell";
import { cn } from "@/lib/cn";

export function HrShell() {
  const navigate = useNavigate();
  const path = useRouterState({ select: (state) => state.location.pathname });
  const me = useHrSession((s) => s.me);
  const clearSession = useHrSession((s) => s.clearSession);
  const { data } = useHrMe();
  const company = data?.company_name ?? me?.company_name;

  const signOut = async () => {
    await hrApi.logout();
    clearSession();
    void navigate({ to: "/hr/sign-in" });
  };

  return (
    <div className="min-h-screen bg-background">
      <header className="border-b border-border bg-card">
        <div className="mx-auto flex h-14 max-w-5xl items-center justify-between px-4">
          <Link
            to="/hr/dashboard"
            aria-label="HR dashboard"
            className="flex min-w-0 items-center gap-2 rounded-md focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring/40"
          >
            <Building2 className="size-5 shrink-0 text-primary" />
            <span className="truncate text-sm font-semibold text-foreground">
              {company ?? "HR Administration"}
            </span>
          </Link>
          <div className="flex items-center gap-1 sm:gap-3">
            <Link
              to="/hr/security"
              aria-current={path === "/hr/security" ? "page" : undefined}
              className={cn(
                "inline-flex items-center gap-1.5 rounded-md px-2 py-1.5 text-sm transition-colors",
                "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring/40",
                path === "/hr/security"
                  ? "bg-accent font-medium text-accent-foreground"
                  : "text-muted-foreground hover:bg-muted hover:text-foreground",
              )}
            >
              <ShieldCheck className="size-4" />
              <span className="hidden sm:inline">Security</span>
            </Link>
            <span className="hidden text-xs text-muted-foreground sm:inline">
              {me?.display_name || me?.email}
            </span>
            <NotificationBell />
            <Button variant="ghost" size="sm" onClick={() => void signOut()}>
              <LogOut className="size-4" />
              <span className="ml-1">Sign out</span>
            </Button>
          </div>
        </div>
      </header>
      <main className="mx-auto w-full max-w-5xl px-4 py-6 sm:py-8">
        <Outlet />
      </main>
    </div>
  );
}
