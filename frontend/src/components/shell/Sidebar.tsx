import { Link, useRouterState } from "@tanstack/react-router";
import { Home } from "lucide-react";
import { cn } from "@/lib/cn";
import { useMe } from "@/api/hooks";
import { COMPANY_NAV, FIRM_NAV, type NavGroup, type NavItem } from "./nav";

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

  // Firm admin surface is broker-admin only; drop it from the firm zone for
  // everyone else (the page itself also gates, this just hides the link).
  const firmItems = FIRM_NAV.items.filter((i) => i.to !== "/admin" || canAdmin);

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
          "w-64 shrink-0 border-r border-border flex flex-col",
          "bg-gradient-to-b from-sidebar via-sidebar to-muted/40",
          "fixed inset-y-0 left-0 z-50 h-full transition-transform duration-200",
          "lg:static lg:z-auto lg:translate-x-0",
          mobileOpen ? "translate-x-0" : "-translate-x-full lg:translate-x-0",
        )}
      >
        <div className="h-14 px-5 flex items-center border-b border-border">
          <img
            src="/inspro-logo.png"
            alt="Inspro Insurance Brokers"
            className="max-h-8 w-auto"
          />
        </div>

        <nav className="flex-1 px-3 py-4 overflow-y-auto">
          <HomeLink active={path === "/home"} />

          {COMPANY_NAV.map((group) => (
            <Section key={group.key} group={group} path={path} />
          ))}

          {/* Firm-wide zone — a distinct tinted surface so an all-companies
              action can never read as a per-company one. */}
          <div className="mt-6 rounded-xl bg-muted/60 p-2">
            <SectionLabel group={FIRM_NAV} active={false} />
            <ul className="mt-1 space-y-0.5">
              {firmItems.map((item) => (
                <ItemLink key={item.to} item={item} active={path === item.to} />
              ))}
            </ul>
          </div>
        </nav>

      </aside>
    </>
  );
}

function HomeLink({ active }: { active: boolean }) {
  return (
    <Link
      to="/home"
      className={cn(
        "group/item relative flex items-center gap-3 rounded-lg px-3 py-2 text-sm font-medium tracking-tight transition-all duration-150",
        active
          ? "bg-sidebar-active text-sidebar-active-foreground"
          : "text-foreground/80 hover:bg-sidebar-hover hover:text-foreground motion-safe:hover:translate-x-0.5",
      )}
    >
      <Home
        className={cn(
          "size-[18px] shrink-0 transition-colors",
          active
            ? "text-primary"
            : "text-muted-foreground group-hover/item:text-foreground",
        )}
        strokeWidth={1.75}
      />
      <span className="flex-1">Home</span>
      {active && <ActiveDot />}
    </Link>
  );
}

function Section({ group, path }: { group: NavGroup; path: string }) {
  const active = group.items.some((item) => path === item.to);
  return (
    <div className="mt-6 first:mt-5">
      <SectionLabel group={group} active={active} />
      <ul className="mt-1 space-y-0.5">
        {group.items.map((item) => (
          <ItemLink key={item.to} item={item} active={path === item.to} />
        ))}
      </ul>
    </div>
  );
}

function SectionLabel({ group, active }: { group: NavGroup; active: boolean }) {
  return (
    <div className="mb-0.5 px-3">
      <span
        className={cn(
          "text-[11px] font-semibold uppercase tracking-[0.08em] transition-colors",
          active ? "text-foreground/70" : "text-subtle",
        )}
      >
        {group.label}
      </span>
    </div>
  );
}

function ItemLink({ item, active }: { item: NavItem; active: boolean }) {
  const Icon = item.icon;
  return (
    <li>
      <Link
        to={item.to}
        className={cn(
          "group/item relative flex items-center gap-3 rounded-lg py-2 pl-3 pr-2.5 text-sm transition-all duration-150",
          active
            ? "bg-sidebar-active text-sidebar-active-foreground font-semibold"
            : "text-foreground/75 hover:bg-sidebar-hover hover:text-foreground motion-safe:hover:translate-x-0.5",
        )}
      >
        <Icon
          className={cn(
            "size-[18px] shrink-0 transition-colors",
            active
              ? "text-primary"
              : "text-muted-foreground group-hover/item:text-foreground",
          )}
          strokeWidth={1.75}
        />
        <span className="flex-1 truncate">{item.label}</span>
        {active && <ActiveDot />}
      </Link>
    </li>
  );
}

function ActiveDot() {
  return (
    <span
      className="size-1.5 shrink-0 rounded-full bg-primary"
      aria-hidden="true"
    />
  );
}
