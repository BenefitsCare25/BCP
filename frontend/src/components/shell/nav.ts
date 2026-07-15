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
        label: "Product categories",
        to: "/configuration",
        title: "Product Categories",
      },
      {
        label: "Attributes & products",
        to: "/schema",
        title: "Schema — Attributes & Products",
      },
      {
        label: "Panel clinics",
        to: "/configuration/panel-clinics",
        title: "Panel Clinic Locations",
      },
      {
        label: "AI provider",
        to: "/configuration/ai-provider",
        title: "AI Provider",
      },
    ],
  },
  {
    label: "Operations",
    icon: Users,
    base: "/operations",
    items: [
      { label: "Roster", to: "/operations/roster", title: "Operations — Roster" },
      {
        label: "Employee coverage",
        to: "/operations/coverage",
        title: "Operations — Employee Coverage",
      },
      {
        label: "Claims",
        to: "/operations/claims",
        title: "Operations — Claims Review",
      },
      {
        label: "Policy year",
        to: "/operations/activations",
        title: "Policy Year",
      },
    ],
  },
  {
    label: "Enrollment",
    icon: CalendarCheck,
    base: "/enrollment",
    items: [
      { label: "Windows & elections", to: "/enrollment", title: "Enrollment" },
    ],
  },
];

export const ADMIN_GROUP: NavGroup = {
  label: "Administration",
  icon: ShieldCheck,
  base: "/admin",
  items: [
    { label: "Clients & users", to: "/admin", title: "Administration — Clients & Users" },
  ],
};

// Pathname → TopBar title, derived from the nav records above. The schema
// group base also gets an entry so /schema?tab=products still titles cleanly.
export const PAGE_TITLES: Record<string, string> = Object.fromEntries(
  [...NAV_GROUPS, ADMIN_GROUP].flatMap((group) =>
    group.items.map((item) => [item.to, item.title]),
  ),
);
