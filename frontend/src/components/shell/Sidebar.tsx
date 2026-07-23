import { useState } from "react";
import { Link, useRouterState } from "@tanstack/react-router";
import { ChevronDown, Home, Layers, ListChecks } from "lucide-react";
import { cn } from "@/lib/cn";
import { useMe } from "@/api/hooks";
import { COMPANY_NAV, FIRM_NAV, type NavGroup, type NavItem } from "./nav";

const COLLAPSE_KEY = "inspro.nav.collapsed";

function readCollapsed(): Set<string> {
  try {
    const raw = localStorage.getItem(COLLAPSE_KEY);
    return new Set(raw ? (JSON.parse(raw) as string[]) : []);
  } catch {
    return new Set();
  }
}

export function Sidebar({
  mobileOpen = false,
  onClose,
}: {
  mobileOpen?: boolean;
  onClose?: () => void;
}) {
  const router = useRouterState();
  const path = router.location.pathname;
  const { data: me } = useMe();
  const canAdmin = me?.role === "broker_admin" || me?.role === "system_admin";

  const [collapsed, setCollapsed] = useState<Set<string>>(readCollapsed);
  const toggle = (key: string) => {
    setCollapsed((prev) => {
      const next = new Set(prev);
      if (next.has(key)) next.delete(key);
      else next.add(key);
      try {
        localStorage.setItem(COLLAPSE_KEY, JSON.stringify([...next]));
      } catch {
        /* private mode / quota — collapse state is non-critical */
      }
      return next;
    });
  };

  // Firm admin surface is broker-admin only; drop it from the firm zone for
  // everyone else (the page itself also gates, this just hides the link).
  const firmItems = FIRM_NAV.items.filter(
    (i) => i.to !== "/admin" || canAdmin,
  );

  return (
    <>
      {/* Scrim behind the drawer on mobile; static sidebar has no scrim (lg+). */}
      {mobileOpen && (
        <div
          className="fixed inset-0 z-40 bg-black/50 lg:hidden"
          onClick={onClose}
          aria-hidden="true"
        />
      )}
      <aside
        className={cn(
          "w-60 shrink-0 border-r border-border bg-sidebar flex flex-col",
          "fixed inset-y-0 left-0 z-50 h-full transition-transform duration-200",
          "lg:static lg:z-auto lg:translate-x-0",
          mobileOpen ? "translate-x-0" : "-translate-x-full lg:translate-x-0",
        )}
      >
      <div className="h-14 px-5 flex items-center border-b border-border">
        <img
          src="/inspro-logo.png"
          alt="Inspro Insurance Brokers"
          className="max-h-9 w-auto"
        />
      </div>
      <nav className="flex-1 p-3 overflow-y-auto space-y-1">
        <HomeLink active={path === "/home"} />

        {COMPANY_NAV.map((group) => (
          <Group
            key={group.key}
            group={group}
            path={path}
            open={!collapsed.has(group.key)}
            onToggle={() => toggle(group.key)}
          />
        ))}

        <div className="pt-3">
          <div className="px-2.5 pb-1 flex items-center gap-1.5 text-[10px] uppercase tracking-wider font-semibold text-muted-foreground/70">
            <Layers className="size-3" />
            Firm · all companies
          </div>
          <ul className="space-y-0.5">
            {firmItems.map((item) => (
              <ItemLink key={item.to} item={item} active={path === item.to} />
            ))}
          </ul>
        </div>
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
    </>
  );
}

function HomeLink({ active }: { active: boolean }) {
  return (
    <Link
      to="/home"
      className={cn(
        "flex items-center gap-2.5 rounded-md px-2.5 py-2 text-sm font-medium tracking-tight transition-colors",
        active
          ? "bg-sidebar-active text-sidebar-active-foreground"
          : "text-foreground/80 hover:bg-sidebar-hover hover:text-foreground",
      )}
    >
      <Home className="size-[18px] shrink-0" strokeWidth={1.75} />
      Home
    </Link>
  );
}

function Group({
  group,
  path,
  open,
  onToggle,
}: {
  group: NavGroup;
  path: string;
  open: boolean;
  onToggle: () => void;
}) {
  const Icon = group.icon;
  const active = group.items.some((item) => path === item.to);
  return (
    <div>
      <button
        type="button"
        onClick={onToggle}
        aria-expanded={open}
        className={cn(
          "flex w-full items-center gap-2.5 rounded-md px-2.5 py-2 text-sm font-medium tracking-tight transition-colors hover:bg-sidebar-hover",
          active ? "text-foreground" : "text-muted-foreground hover:text-foreground",
        )}
      >
        <Icon className="size-[18px] shrink-0" strokeWidth={1.75} />
        <span className="flex-1 text-left">{group.label}</span>
        <ChevronDown
          className={cn(
            "size-4 shrink-0 text-muted-foreground/70 transition-transform duration-200",
            open ? "" : "-rotate-90",
          )}
          strokeWidth={2}
        />
      </button>
      {open && (
        <ul className="mt-0.5 space-y-0.5">
          {group.items.map((item) => (
            <ItemLink key={item.to} item={item} active={path === item.to} indent />
          ))}
        </ul>
      )}
    </div>
  );
}

function ItemLink({
  item,
  active,
  indent,
}: {
  item: NavItem;
  active: boolean;
  indent?: boolean;
}) {
  const Icon = item.icon;
  return (
    <li>
      <Link
        to={item.to}
        className={cn(
          "flex items-center gap-2.5 rounded-md py-1.5 pr-2.5 text-sm transition-colors",
          indent ? "pl-4" : "pl-2.5",
          active
            ? "bg-sidebar-active text-sidebar-active-foreground font-medium"
            : "text-foreground/80 hover:bg-sidebar-hover hover:text-foreground",
        )}
      >
        <Icon className="size-[18px] shrink-0" strokeWidth={1.75} />
        <span className="truncate">{item.label}</span>
      </Link>
    </li>
  );
}
