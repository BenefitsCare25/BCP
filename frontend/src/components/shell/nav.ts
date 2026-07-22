import {
  BarChart3,
  Boxes,
  Briefcase,
  Building2,
  CalendarCheck,
  ClipboardCheck,
  ReceiptText,
  Settings2,
  SlidersHorizontal,
  Sparkles,
  Stethoscope,
  Upload,
  UserCheck,
  Users,
  type LucideIcon,
} from "lucide-react";

// Single source of truth for broker navigation. The Sidebar renders these
// groups and the AppShell derives its TopBar titles from the same records, so
// links and titles can't drift apart.
//
// Two tiers, mirroring the multi-tenant model:
//   COMPANY_NAV — sections that act on ONE insured company (the active client).
//   FIRM_NAV    — firm-wide surfaces that span every company (shared libraries,
//                 tenant/user administration). These never carry a company chip.
export type NavItem = {
  label: string;
  to: string;
  title: string;
  icon: LucideIcon;
};
export type NavGroup = {
  label: string;
  icon: LucideIcon;
  key: string;
  items: NavItem[];
};

// Grouped by the internal team that owns the work, NOT by the URL prefix — a
// team's pages can span several route prefixes (e.g. Policy Admin mixes
// /operations/* with /configuration/panel-clinics).
export const COMPANY_NAV: NavGroup[] = [
  {
    label: "Client Relations",
    icon: Briefcase,
    key: "cr",
    items: [
      {
        label: "Company & Benefits",
        to: "/configuration",
        title: "Company & Benefits",
        icon: Boxes,
      },
      {
        label: "Enrollment",
        to: "/enrollment",
        title: "Benefits Selection",
        icon: CalendarCheck,
      },
    ],
  },
  {
    label: "Policy Admin",
    icon: Users,
    key: "pa",
    items: [
      {
        label: "Membership",
        to: "/operations/roster",
        title: "Membership / Listing Upload",
        icon: Upload,
      },
      {
        label: "Coverage & Members",
        to: "/operations/coverage",
        title: "Coverage & Members",
        icon: UserCheck,
      },
      {
        label: "Panel & Clinics",
        to: "/configuration/panel-clinics",
        title: "Panel Clinic Locations",
        icon: Stethoscope,
      },
      {
        label: "Underwriting",
        to: "/operations/underwriting",
        title: "Underwriting Queue",
        icon: ClipboardCheck,
      },
    ],
  },
  {
    label: "Claims",
    icon: ReceiptText,
    key: "claims",
    items: [
      {
        label: "Claims Review",
        to: "/operations/claims",
        title: "Claims Review",
        icon: ReceiptText,
      },
      {
        label: "Reports Center",
        to: "/operations/reports",
        title: "Reports",
        icon: BarChart3,
      },
    ],
  },
  {
    label: "Settings",
    icon: Settings2,
    key: "settings",
    items: [
      {
        label: "AI Setting",
        to: "/configuration/ai-provider",
        title: "AI Setting",
        icon: Sparkles,
      },
    ],
  },
];

// Firm-wide (all companies). Access & Companies is broker-admin gated in the
// Sidebar; Schema & Reference is visible to everyone.
export const FIRM_NAV: NavGroup = {
  label: "Firm",
  icon: Building2,
  key: "firm",
  items: [
    {
      label: "Schema & Reference",
      to: "/schema",
      title: "Schema & Reference",
      icon: SlidersHorizontal,
    },
    {
      label: "Access & Companies",
      to: "/admin",
      title: "Access & Companies",
      icon: Building2,
    },
  ],
};

// The firm-wide route prefixes — pages that span every company and therefore
// show the "Firm-wide" context instead of a company chip. Everything else in
// the app shell acts on the active company.
const FIRM_PREFIXES = ["/home", "/schema", "/admin"];

/** True when the path is a company-scoped page (shows the company context).
 * Firm-wide pages (/home, /schema, /admin and their children) return false. */
export function isCompanyPath(pathname: string): boolean {
  return !FIRM_PREFIXES.some(
    (p) => pathname === p || pathname.startsWith(`${p}/`),
  );
}

// Pathname → TopBar title, derived from every nav record above.
export const PAGE_TITLES: Record<string, string> = Object.fromEntries(
  [...COMPANY_NAV, FIRM_NAV].flatMap((group) =>
    group.items.map((item) => [item.to, item.title]),
  ),
);
