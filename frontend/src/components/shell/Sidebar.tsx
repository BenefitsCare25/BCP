import { Link, useRouterState } from "@tanstack/react-router";
import { Home } from "lucide-react";
import { cn } from "@/lib/cn";
import { COMPANY_NAV, type NavGroup, type NavItem } from "./nav";

export function Sidebar({
  mobileOpen = false,
  onClose,
}: {
  mobileOpen?: boolean;
  onClose?: () => void;
}) {
  const router = useRouterState();
  const path = router.location.pathname;

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
          "w-60 shrink-0 border-r border-border flex flex-col",
          "bg-gradient-to-b from-sidebar via-sidebar to-muted/40",
          "fixed inset-y-0 left-0 z-50 h-full transition-transform duration-200",
          "lg:static lg:z-auto lg:translate-x-0",
          mobileOpen ? "translate-x-0" : "-translate-x-full lg:translate-x-0",
        )}
      >
        <div className="h-14 px-4 flex items-center border-b border-border">
          <img
            src="/inspro-logo.png"
            alt="Inspro Insurance Brokers"
            className="max-h-8 w-auto"
          />
        </div>

        <nav className="flex-1 px-2 py-2.5 overflow-y-auto">
          <HomeLink active={path === "/home"} />

          {COMPANY_NAV.map((group) => (
            <Section key={group.key} group={group} path={path} />
          ))}
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
        "group/item relative flex items-center gap-2.5 rounded-lg px-2.5 py-1.5 text-sm font-medium tracking-tight transition-all duration-150",
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
    <div className="mt-4 first:mt-3">
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
          "text-2xs font-semibold uppercase tracking-[0.08em] transition-colors",
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
          "group/item relative flex items-center gap-2.5 rounded-lg py-1.5 pl-2.5 pr-2 text-sm transition-all duration-150",
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
