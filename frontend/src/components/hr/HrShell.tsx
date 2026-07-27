/** Authenticated HR admin shell — top bar (company + user + sign out) over the
 * routed content. A tenant is pinned by the subdomain, so there is no client
 * switcher. */
import { Link, Outlet, useNavigate } from "@tanstack/react-router";
import { Building2, LogOut, ShieldCheck } from "lucide-react";
import { useHrMe } from "@/api/hr";
import { hrApi } from "@/api/hrClient";
import { useHrSession } from "@/stores/hrSession";
import { Button } from "@/components/ui/button";
import { NotificationBell } from "@/components/shell/NotificationBell";

export function HrShell() {
  const navigate = useNavigate();
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
          <div className="flex items-center gap-2">
            <Building2 className="size-5 text-primary" />
            <span className="text-sm font-semibold text-foreground">
              {company ?? "HR Administration"}
            </span>
          </div>
          <div className="flex items-center gap-1 sm:gap-3">
            <Link
              to="/hr/security"
              className="inline-flex items-center gap-1.5 rounded-md px-2 py-1.5 text-sm text-muted-foreground transition-colors hover:bg-muted hover:text-foreground"
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
      <main className="mx-auto max-w-5xl px-4 py-8">
        <Outlet />
      </main>
    </div>
  );
}
