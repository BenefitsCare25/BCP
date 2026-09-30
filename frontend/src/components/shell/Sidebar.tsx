import { useEffect } from "react";
import { Link, useRouterState } from "@tanstack/react-router";
import { Home } from "lucide-react";
import { cn } from "@/lib/cn";
import { useMe } from "@/api/hooks";
import { Sheet, SheetContent, SheetTitle, SheetDescription } from "@/components/ui/sheet";
import { useMediaQuery } from "@/lib/useMediaQuery";
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
  const desktop = useMediaQuery("(min-width: 1024px)");
  useEffect(() => { if (desktop) onClose?.(); }, [desktop, onClose]);

  return (
    <>
      <aside
        className="hidden h-full w-60 shrink-0 flex-col border border-border bg-sidebar lg:flex"
      >
        <NavigationContent path={path} canAdmin={canAdmin} />
      </aside>
      <Sheet open={mobileOpen && !desktop} onOpenChange={open => { if (!open) onClose?.(); }}>
        <SheetContent id="broker-navigation-drawer" side="left" className="w-60 max-w-[calc(100vw-2rem)] border bg-sidebar"
          onCloseAutoFocus={event => {
            event.preventDefault();
            const target = window.matchMedia("(min-width: 1024px)").matches
              ? "aside a[href]" : "[data-mobile-nav-trigger]";
            document.querySelector<HTMLElement>(target)?.focus();
          }}>
          <SheetTitle className="sr-only">Navigation</SheetTitle>
          <SheetDescription className="sr-only">Navigate between broker pages.</SheetDescription>
          <NavigationContent path={path} canAdmin={canAdmin} mobile onNavigate={onClose} />
        </SheetContent>
      </Sheet>
    </>
  );
}

function NavigationContent({ path, canAdmin, mobile = false, onNavigate }: {
  path: string; canAdmin: boolean; mobile?: boolean; onNavigate?: () => void;
}) {
  return <>
    <div className="flex h-14 shrink-0 items-center border-b border-border px-4">
      <img src="/inspro-logo.png" alt="Inspro Insurance Brokers" className="max-h-8 w-auto" />
    </div>
    <nav aria-label="Broker navigation" className="flex-1 overflow-y-auto px-2 py-2.5"
      onClick={event => { if ((event.target as HTMLElement).closest("a[href]")) onNavigate?.(); }}>
      <HomeLink active={path === "/home"} />
      {COMPANY_NAV.map(group => {
        const items = group.items.filter(item => !item.adminOnly || canAdmin);
        return items.length > 0 ? <Section key={group.key} group={{ ...group, items }} path={path} /> : null;
      })}
      {mobile && <Section group={{ ...FIRM_NAV, items: FIRM_NAV.items.filter(item => !item.adminOnly || canAdmin) }} path={path} />}
    </nav>
  </>;
}

function HomeLink({ active }: { active: boolean }) {
  return (
    <Link
      to="/home"
      className={cn(
        "group/item relative flex items-center gap-2.5 rounded-lg px-2.5 py-1.5 text-sm font-medium tracking-tight transition-colors duration-150",
        active
          ? "bg-sidebar-active text-sidebar-active-foreground"
          : "text-foreground/80 hover:bg-sidebar-hover hover:text-foreground",
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
          "group/item relative flex items-center gap-2.5 rounded-lg py-1.5 pl-2.5 pr-2 text-sm transition-colors duration-150",
          active
            ? "bg-sidebar-active text-sidebar-active-foreground font-semibold"
            : "text-foreground/75 hover:bg-sidebar-hover hover:text-foreground",
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
