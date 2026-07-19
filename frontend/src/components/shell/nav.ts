import {
  CalendarCheck,
  Settings,
  ShieldCheck,
  Users,
  type LucideIcon,
} from "lucide-react";

// Single source of truth for broker navigation. The Sidebar renders these
// groups and the AppShell derives its TopBar titles from the same records,
// so links and titles can't drift apart.
export type NavItem = { label: string; to: string; title: string };
export type NavGroup = {
  label: string;
  icon: LucideIcon;
  base: string;
  items: NavItem[];
};

export const NAV_GROUPS: NavGroup[] = [
  {
    label: "Configuration",
    icon: Settings,
    base: "/configuration",
    items: [
      {
        label: "Product Setting",
        to: "/configuration",
        title: "Product Setting",
      },
      {
        label: "Attributes Setting",
        to: "/schema",
        title: "Attributes Setting",
      },
      {
        label: "Panel clinics",
        to: "/configuration/panel-clinics",
        title: "Panel Clinic Locations",
      },
      {
        label: "AI Setting",
        to: "/configuration/ai-provider",
        title: "AI Setting",
      },
    ],
  },
  {
    label: "Operations",
    icon: Users,
    base: "/operations",
    items: [
      {
        label: "Listing Upload",
        to: "/operations/roster",
        title: "Listing Upload",
      },
      {
        label: "Employee Coverage",
        to: "/operations/coverage",
        title: "Employee Coverage",
      },
      {
        label: "Claims",
        to: "/operations/claims",
        title: "Operations — Claims Review",
      },
      {
        label: "Reports",
        to: "/operations/reports",
        title: "Operations — Insurer Reports",
      },
      {
        label: "Underwriting",
        to: "/operations/underwriting",
        title: "Operations — Underwriting Queue",
      },
    ],
  },
  {
    label: "Enrollment",
    icon: CalendarCheck,
    base: "/enrollment",
    items: [
      { label: "Benefits Selection", to: "/enrollment", title: "Benefits Selection" },
    ],
  },
];

export const ADMIN_GROUP: NavGroup = {
  label: "Administration",
  icon: ShieldCheck,
  base: "/admin",
  items: [
    { label: "Company & Users", to: "/admin", title: "Company & Users" },
  ],
};

// Pathname → TopBar title, derived from the nav records above. The schema
// group base also gets an entry so /schema?tab=products still titles cleanly.
export const PAGE_TITLES: Record<string, string> = Object.fromEntries(
  [...NAV_GROUPS, ADMIN_GROUP].flatMap((group) =>
    group.items.map((item) => [item.to, item.title]),
  ),
);
