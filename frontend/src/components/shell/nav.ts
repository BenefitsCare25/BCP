import {
  Activity,
  BarChart3,
  Boxes,
  Briefcase,
  Building2,
  CalendarCheck,
  ClipboardCheck,
  Cog,
  Globe,
  Landmark,
  Layers,
  ReceiptText,
  Scale,
  ScrollText,
  Settings2,
  ShieldCheck,
  SlidersHorizontal,
  Sparkles,
  Stethoscope,
  Upload,
  UserCheck,
  Users,
  type LucideIcon,
} from "lucide-react";

// Single source of truth for broker navigation. The Sidebar renders these
// groups while the app shell supplies company and benefit-year context.
//
// Two tiers, mirroring the multi-tenant model:
//   COMPANY_NAV — sections that act on ONE insured company (the active client).
//   FIRM_NAV    — firm-wide surfaces that span every company (shared libraries,
//                 tenant/user administration). These never carry a company chip.
export type NavItem = {
  label: string;
  to: string;
  icon: LucideIcon;
  adminOnly?: boolean;
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
        icon: Boxes,
      },
      {
        label: "Enrollment",
        to: "/client-relations/enrollment",
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
        icon: Upload,
      },
      {
        label: "Member Coverage",
        to: "/policy-admin/member-coverage",
        icon: UserCheck,
      },
      {
        label: "Panel & Clinics",
        to: "/policy-admin/panel-clinics",
        icon: Stethoscope,
      },
      {
        label: "Underwriting",
        to: "/policy-admin/underwriting",
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
        icon: ReceiptText,
      },
      {
        label: "WICA",
        to: "/claims/wica",
        icon: Scale,
      },
      {
        label: "Reports Center",
        to: "/claims/reports",
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
        icon: Cog,
      },
      {
        label: "AI Settings",
        to: "/settings/ai",
        icon: Sparkles,
      },
    ],
  },
];

// Firm-wide (all companies). Access & Companies is broker-admin gated in the
// Sidebar and top bar; Schema & Reference is visible to everyone.
export const FIRM_NAV: NavGroup = {
  label: "Firm",
  icon: Layers,
  key: "firm",
  items: [
    {
      label: "Schema & Reference",
      to: "/firm/schema",
      icon: SlidersHorizontal,
    },
    {
      label: "Access & Companies",
      to: "/firm/access",
      icon: Building2,
      adminOnly: true,
    },
  ],
};

// The platform console: every broker firm on the platform. Shown only when
// `/me` reports `platform_console` (a system_admin on a platform host); the
// pages gate on the same flag.
export const PLATFORM_NAV: NavGroup = {
  label: "Platform",
  icon: Globe,
  key: "platform",
  items: [
    { label: "Broker firms", to: "/platform/firms", icon: Landmark },
    { label: "Activity", to: "/platform/activity", icon: Activity },
    { label: "Firm access", to: "/platform/access", icon: ShieldCheck },
    { label: "Audit trail", to: "/platform/audit", icon: ScrollText },
  ],
};

// The firm-wide route prefixes — pages that span every company and therefore
// show the "Firm-wide" context instead of a company chip. Everything else in
// the app shell acts on the active company.
const FIRM_PREFIXES = ["/home", "/firm", "/platform"];

export function isPlatformPath(pathname: string): boolean {
  return pathname === "/platform" || pathname.startsWith("/platform/");
}

/** Shared AI documents and role guidance do not require a company selection. */
export function isAISharedTab(pathname: string, tab?: string): boolean {
  return pathname === "/settings/ai" && (tab === "policies" || tab === "usage");
}

/** True when the path is a company-scoped page (shows the company context).
 * Firm-wide pages (/home, /firm/* and their children) return false. */
export function isCompanyPath(pathname: string): boolean {
  return !FIRM_PREFIXES.some(
    (p) => pathname === p || pathname.startsWith(`${p}/`),
  );
}
