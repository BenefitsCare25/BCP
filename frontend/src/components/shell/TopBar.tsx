import { useEffect } from "react";
import { Link, useRouterState } from "@tanstack/react-router";
import { Menu } from "lucide-react";
import { useMe, usePolicyYears } from "@/api/hooks";
import { useSession } from "@/stores/session";
import { cn } from "@/lib/cn";
import { AccountMenu } from "./AccountMenu";
import { NotificationBell } from "./NotificationBell";
import { FIRM_NAV } from "./nav";

export function TopBar({
  title,
  onMenuClick,
}: {
  title: string;
  onMenuClick?: () => void;
}) {
  const { data: years = [], isSuccess } = usePolicyYears();
  const currentId = useSession((s) => s.currentPolicyYearId);
  const setPolicyYear = useSession((s) => s.setPolicyYear);
  const { data: me } = useMe();
  const path = useRouterState({ select: (s) => s.location.pathname });

  // Firm-wide surfaces live in the top bar as icon shortcuts (not the sidebar,
  // which is company-scoped). Access & Companies stays broker-admin gated.
  const canAdmin = me?.role === "broker_admin" || me?.role === "system_admin";
  const firmItems = FIRM_NAV.items.filter((i) => i.to !== "/admin" || canAdmin);

  // The per-page year picker was removed: the session policy year always tracks
  // the CURRENT (active) benefit year, and every page follows it. The
  // Configuration page owns read-only viewing of other years locally. Gated on
  // isSuccess so a still-loading list never clears a valid selection mid-fetch.
  useEffect(() => {
    if (!isSuccess) return;
    if (years.length === 0) {
      if (currentId !== null) setPolicyYear(null);
      return;
    }
    const active = years.find((y) => y.status === "active") ?? years[0];
    if (active && currentId !== active.id) setPolicyYear(active.id);
  }, [isSuccess, years, currentId, setPolicyYear]);

  return (
    <header className="h-14 border-b border-border bg-card px-4 sm:px-6 flex items-center justify-between gap-2 shrink-0">
      <div className="flex items-center gap-2 min-w-0">
        {onMenuClick && (
          <button
            type="button"
            onClick={onMenuClick}
            aria-label="Open navigation menu"
            className="lg:hidden flex size-8 shrink-0 items-center justify-center rounded-md text-muted-foreground hover:bg-muted hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring/50"
          >
            <Menu className="size-5" />
          </button>
        )}
        <h1 className="truncate text-base font-semibold text-foreground">{title}</h1>
      </div>
      <div className="flex items-center gap-2 shrink-0">
        <nav className="flex items-center gap-1" aria-label="Firm-wide">
          {firmItems.map((item) => {
            const Icon = item.icon;
            const active = path === item.to || path.startsWith(`${item.to}/`);
            return (
              <Link
                key={item.to}
                to={item.to}
                title={item.label}
                aria-label={item.label}
                className={cn(
                  "flex size-8 items-center justify-center rounded-md transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring/50",
                  active
                    ? "bg-accent text-primary"
                    : "text-muted-foreground hover:bg-muted hover:text-foreground",
                )}
              >
                <Icon className="size-[18px]" strokeWidth={1.75} />
              </Link>
            );
          })}
        </nav>
        <NotificationBell />
        <div className="mx-0.5 h-5 w-px bg-border" aria-hidden="true" />
        <AccountMenu />
      </div>
    </header>
  );
}
