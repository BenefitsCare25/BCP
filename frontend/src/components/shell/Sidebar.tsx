import { Link, useRouterState } from "@tanstack/react-router";
import { Layers, ListChecks } from "lucide-react";
import { cn } from "@/lib/cn";
import { useMe } from "@/api/hooks";
import { ADMIN_GROUP, NAV_GROUPS } from "./nav";

export function Sidebar() {
  const router = useRouterState();
  const path = router.location.pathname;
  const { data: me } = useMe();
  const canAdmin = me?.role === "broker_admin" || me?.role === "system_admin";
  const nav = canAdmin ? [...NAV_GROUPS, ADMIN_GROUP] : NAV_GROUPS;
  return (
    <aside className="w-60 shrink-0 border-r border-border bg-sidebar h-full flex flex-col">
      <div className="h-14 px-5 flex items-center border-b border-border">
        <img
          src="/inspro-logo.png"
          alt="Inspro Insurance Brokers"
          className="max-h-9 w-auto"
        />
      </div>
      <nav className="flex-1 p-3 overflow-y-auto space-y-5">
        {nav.map((group) => {
          const Icon = group.icon;
          const active =
            path.startsWith(group.base) ||
            group.items.some(
              (item) => path === item.to || path.startsWith(`${item.to}/`),
            );
          return (
            <div key={group.base}>
              <div
                className={cn(
                  "flex items-center gap-2 px-2 py-1.5 text-xs uppercase tracking-wider font-medium",
                  active ? "text-foreground" : "text-muted-foreground",
                )}
              >
                <Icon className="size-3.5" />
                {group.label}
              </div>
              <ul className="space-y-0.5">
                {group.items.map((item) => {
                  const isActive = path === item.to;
                  return (
                    <li key={item.to}>
                      <Link
                        to={item.to}
                        className={cn(
                          "block w-full text-left rounded-md px-3 py-1.5 text-sm transition-colors",
                          isActive
                            ? "bg-sidebar-active text-sidebar-active-foreground font-medium"
                            : "text-sidebar-foreground hover:bg-sidebar-hover",
                        )}
                      >
                        {item.label}
                      </Link>
                    </li>
                  );
                })}
              </ul>
            </div>
          );
        })}
      </nav>
      <div className="p-3 border-t border-border text-xs text-muted-foreground space-y-1">
        <div className="flex items-center gap-2">
          <ListChecks className="size-3.5" />
          Spike v0 — SQLite-backed
        </div>
        <div className="flex items-center gap-2">
          <Layers className="size-3.5" />
          Singapore region
        </div>
      </div>
    </aside>
  );
}
