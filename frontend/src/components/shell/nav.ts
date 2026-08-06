import {
  BarChart3,
  Boxes,
  Briefcase,
  Building2,
  CalendarCheck,
  ClipboardCheck,
  Cog,
  Layers,
  ReceiptText,
  Scale,
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

// Grouped by the internal team that owns the work, and the ROUTE PREFIX MIRRORS
// THE GROUP — Policy Admin pages all live under /policy-admin/*, Claims under
// /claims/*, and so on. A nav label and the URL it reaches always agree, so
// renaming an item here means renaming its route in router.tsx to match.
export const COMPANY_NAV: NavGroup[] = [
  {
    label: "Client Relations",
    icon: Briefcase,
    key: "cr",
    items: [
      {
        label: "Company & Benefits",
        to: "/client-relations/company-benefits",
        title: "Company & Benefits",
        icon: Boxes,
      },
      {
        label: "Enrollment",
        to: "/client-relations/enrollment",
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
        label: "Member Listing",
        to: "/policy-admin/member-listing",
        title: "Member Listing / Upload",
        icon: Upload,
      },
      {
        label: "Coverage & Members",
        to: "/policy-admin/coverage-members",
        title: "Coverage & Members",
        icon: UserCheck,
      },
      {
        label: "Panel & Clinics",
        to: "/policy-admin/panel-clinics",
        title: "Panel Clinic Locations",
        icon: Stethoscope,
      },
      {
        label: "Underwriting",
        to: "/policy-admin/underwriting",
        title: "Underwriting Queue",
        icon: ClipboardCheck,
      },
    ],
  },
  {
    label: "Claims",
    icon: Scale,
    key: "claims",
    items: [
      {
        label: "Claims Review",
        to: "/claims/review",
        title: "Claims Review",
        icon: ReceiptText,
      },
      {
        label: "Reports Center",
        to: "/claims/reports",
        title: "Reports Center",
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
        label: "Company settings",
        to: "/settings/company",
        title: "Company settings",
        icon: Cog,
      },
      {
        label: "AI Setting",
        to: "/settings/ai",
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
  icon: Layers,
  key: "firm",
  items: [
    {
      label: "Schema & Reference",
      to: "/firm/schema",
      title: "Schema & Reference",
      icon: SlidersHorizontal,
    },
    {
      label: "Access & Companies",
      to: "/firm/access",
      title: "Access & Companies",
      icon: Building2,
    },
  ],
};

// The firm-wide route prefixes — pages that span every company and therefore
// show the "Firm-wide" context instead of a company chip. Everything else in
// the app shell acts on the active company.
const FIRM_PREFIXES = ["/home", "/firm"];

/** True when the path is a company-scoped page (shows the company context).
 * Firm-wide pages (/home, /firm/* and their children) return false. */
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
