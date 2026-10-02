/** Authenticated HR admin shell — top bar (company + user + sign out) over the
 * routed content. A tenant is pinned by the subdomain, so there is no client
 * switcher. */
import { Link, Outlet, useNavigate, useRouterState } from "@tanstack/react-router";
import {
  FileText,
  FileSignature,
  LayoutDashboard,
  LogOut,
  ShieldCheck,
} from "lucide-react";
import { useHrMe } from "@/api/hr";
import { hrApi } from "@/api/hrClient";
import { useHrSession } from "@/stores/hrSession";
import { NotificationBell } from "@/components/shell/NotificationBell";
import { LeafScopeContext } from "@/lib/leaf-scope";
import { cn } from "@/lib/cn";

const HR_NAV = [
  { to: "/hr/dashboard", label: "Overview", icon: LayoutDashboard },
  { to: "/hr/claims", label: "Claims", icon: FileText },
  { to: "/hr/enrollment-forms", label: "Enrolment forms", icon: FileSignature },
] as const;

export function HrShell() {
  const navigate = useNavigate();
  const path = useRouterState({ select: (state) => state.location.pathname });
  const me = useHrSession((s) => s.me);
  const clearSession = useHrSession((s) => s.clearSession);
  const { data } = useHrMe();
  const company = data?.company_name ?? me?.company_name;
  const isOverview = path === "/hr/dashboard";

  const signOut = async () => {
    await hrApi.logout();
    clearSession();
    void navigate({ to: "/hr/sign-in" });
  };

  return (
    <LeafScopeContext.Provider value>
    <div className={cn("leaf portal-clay portal-ui hr-portal min-h-screen bg-background", isOverview && "hr-is-overview")}>
      <header className="clay-topbar">
        <div className="clay-topbar-inner mx-auto max-w-[1440px]">
          <div className="portal-nav-row flex items-center gap-0 py-2 pl-3 pr-2 lg:py-2.5 lg:pl-5 lg:pr-3">
            <Link
              to="/hr/dashboard"
              activeOptions={{ exact: true }}
              aria-label="HR dashboard"
              className="portal-nav-brand leaf-focus hidden min-h-11 shrink-0 items-center rounded-pill lg:flex"
            >
              <img src="/inspro-logo-header.png" alt="Inspro Insurance Brokers" width={125} height={40} className="portal-nav-logo hidden h-10 w-auto lg:block" />
            </Link>
            <span aria-hidden className="portal-nav-divider mx-5 hidden h-8 w-px shrink-0 bg-hairline lg:block" />
            <nav aria-label="HR navigation" className="hr-navigation flex items-center gap-0.5">
              {HR_NAV.map(({ to, label, icon: Icon }) => (
                <Link
                  key={to}
                  to={to}
                  aria-label={label}
                  activeOptions={{ exact: to === "/hr/dashboard" }}
                  aria-current={(to === "/hr/dashboard" ? path === to : path.startsWith(to)) ? "page" : undefined}
                  className="portal-nav-link leaf-focus"
                >
                  <Icon aria-hidden strokeWidth={1.75} />
                  <span className="hidden sm:inline">{label}</span>
                </Link>
              ))}
            </nav>
          <div className="portal-nav-actions ml-auto flex shrink-0 items-center gap-1 pl-2 lg:pl-6">
            <NotificationBell largeTarget />
            <Link
              to="/hr/security"
              aria-label="Security"
              aria-current={path === "/hr/security" ? "page" : undefined}
              className="portal-nav-action leaf-focus inline-flex items-center justify-center transition-colors"
            >
              <ShieldCheck className="size-5" aria-hidden />
            </Link>
            <button
              type="button"
              aria-label="Sign out"
              className="portal-nav-action leaf-focus inline-flex items-center justify-center transition-colors"
              onClick={() => void signOut()}
            >
              <LogOut className="size-5" aria-hidden />
            </button>
          </div>
        </div>
        </div>
      </header>
      <main className={cn("mx-auto w-full", !isOverview && "max-w-6xl px-4 py-4")}>
        {!isOverview && <div className="hr-context mb-4 flex flex-wrap items-baseline justify-between gap-2">
          <p className="text-lg font-bold">{company ?? "HR Administration"}</p>
          <p className="text-sm text-label">{me?.display_name || me?.email}</p>
        </div>}
        <Outlet />
      </main>
    </div>
    </LeafScopeContext.Provider>
  );
}
