import {
  Outlet,
  createRootRoute,
  createRoute,
  createRouter,
  lazyRouteComponent,
  redirect,
} from "@tanstack/react-router";
import { AppShell } from "@/components/shell/AppShell";
import {
  GlobalErrorComponent,
  NotFoundComponent,
} from "@/components/shell/ErrorBoundary";
import { ENTRA_ENABLED, getActiveAccount } from "@/auth/msal";
import { PortalShell } from "@/components/portal/PortalShell";
import { hasValidPortalSession } from "@/stores/portalSession";
import { HrShell } from "@/components/hr/HrShell";
import { hasValidHrSession } from "@/stores/hrSession";

// Each page is split into its own chunk; the initial bundle ships only the
// shell + router + the heavy infra (MSAL, react-query, tanstack-router).
const SchemaPage = lazyRouteComponent(
  () => import("@/routes/schema/index"),
  "SchemaPage",
);
const AIProviderPage = lazyRouteComponent(
  () => import("@/routes/configuration/ai-provider"),
  "AIProviderPage",
);
const ConfigurationPage = lazyRouteComponent(
  () => import("@/routes/configuration/index"),
  "ConfigurationPage",
);
const CompanySettingsPage = lazyRouteComponent(
  () => import("@/routes/configuration/settings"),
  "CompanySettingsPage",
);
const PanelClinicsPage = lazyRouteComponent(
  () => import("@/routes/configuration/panel-clinics"),
  "PanelClinicsPage",
);
const RosterPage = lazyRouteComponent(
  () => import("@/routes/operations/roster"),
  "RosterPage",
);
const EmployeeCoveragePage = lazyRouteComponent(
  () => import("@/routes/operations/coverage"),
  "EmployeeCoveragePage",
);
const ClaimsQueuePage = lazyRouteComponent(
  () => import("@/routes/operations/claims"),
  "ClaimsQueuePage",
);
const ReportsPage = lazyRouteComponent(
  () => import("@/routes/operations/reports"),
  "ReportsPage",
);
const UnderwritingPage = lazyRouteComponent(
  () => import("@/routes/operations/underwriting"),
  "UnderwritingPage",
);
const EnrollmentPage = lazyRouteComponent(
  () => import("@/routes/enrollment/index"),
  "EnrollmentPage",
);
const AdminPage = lazyRouteComponent(
  () => import("@/routes/admin/index"),
  "AdminPage",
);
const AuthCallbackPage = lazyRouteComponent(
  () => import("@/routes/auth/callback"),
  "AuthCallbackPage",
);
const PortalSignInPage = lazyRouteComponent(
  () => import("@/routes/portal/sign-in"),
  "PortalSignInPage",
);
const PortalSetPasswordPage = lazyRouteComponent(
  () => import("@/routes/portal/set-password"),
  "PortalSetPasswordPage",
);
const PortalCoveragePage = lazyRouteComponent(
  () => import("@/routes/portal/coverage"),
  "PortalCoveragePage",
);
const PortalEnrollmentPage = lazyRouteComponent(
  () => import("@/routes/portal/enrollment"),
  "PortalEnrollmentPage",
);
const PortalClaimsPage = lazyRouteComponent(
  () => import("@/routes/portal/claims/index"),
  "PortalClaimsPage",
);
const PortalClinicsPage = lazyRouteComponent(
  () => import("@/routes/portal/clinics"),
  "PortalClinicsPage",
);
const PortalCardPage = lazyRouteComponent(
  () => import("@/routes/portal/card"),
  "PortalCardPage",
);
const PortalNewClaimPage = lazyRouteComponent(
  () => import("@/routes/portal/claims/new"),
  "PortalNewClaimPage",
);
const PortalClaimDetailPage = lazyRouteComponent(
  () => import("@/routes/portal/claims/detail"),
  "PortalClaimDetailPage",
);
const HrSignInPage = lazyRouteComponent(
  () => import("@/routes/hr/sign-in"),
  "HrSignInPage",
);
const HrSetPasswordPage = lazyRouteComponent(
  () => import("@/routes/hr/set-password"),
  "HrSetPasswordPage",
);
const HrDashboardPage = lazyRouteComponent(
  () => import("@/routes/hr/dashboard"),
  "HrDashboardPage",
);
const HrSecurityPage = lazyRouteComponent(
  () => import("@/routes/hr/security"),
  "HrSecurityPage",
);
const SignInPage = lazyRouteComponent(
  () => import("@/routes/auth/sign-in"),
  "SignInPage",
);
const HomePage = lazyRouteComponent(() => import("@/routes/home"), "HomePage");
const CompanyDashboardPage = lazyRouteComponent(
  () => import("@/routes/dashboard"),
  "CompanyDashboardPage",
);

// Routes that are reachable without being signed in. Everything else goes
// through the AppShell which requires an active MSAL account when Entra is
// enabled.
const PUBLIC_PATHS = new Set(["/auth/callback", "/sign-in"]);

const rootRoute = createRootRoute({
  component: () => <Outlet />,
});

const authCallbackRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/auth/callback",
  component: AuthCallbackPage,
});

const signInRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/sign-in",
  beforeLoad: () => {
    // If we're already signed in, skip the render flash and go straight home.
    if (ENTRA_ENABLED && getActiveAccount() !== null) {
      throw redirect({ to: "/" });
    }
  },
  component: SignInPage,
});

// ── Employee portal — a sibling surface with its OWN auth (member OTP token,
// not MSAL). The broker shell's guard never runs for /portal/* routes.
const portalSignInRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/portal/sign-in",
  beforeLoad: () => {
    // Already signed in (and not following a fresh magic link) → straight in.
    if (hasValidPortalSession() && !window.location.search.includes("code=")) {
      throw redirect({ to: "/portal/coverage" });
    }
  },
  component: PortalSignInPage,
});

const portalSetPasswordRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/portal/set-password",
  component: PortalSetPasswordPage,
});

// ── HR admin — a sibling surface with its OWN credential auth (HR access
// token + rotating refresh cookie, not MSAL). Tenant is pinned by subdomain.
const hrSignInRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/hr/sign-in",
  beforeLoad: () => {
    if (hasValidHrSession()) {
      throw redirect({ to: "/hr/dashboard" });
    }
  },
  component: HrSignInPage,
});

const hrSetPasswordRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/hr/set-password",
  component: HrSetPasswordPage,
});

const hrLayoutRoute = createRoute({
  getParentRoute: () => rootRoute,
  id: "hr-shell",
  beforeLoad: ({ location }) => {
    if (
      location.pathname === "/hr/sign-in" ||
      location.pathname === "/hr/set-password"
    ) {
      return;
    }
    if (!hasValidHrSession()) {
      throw redirect({ to: "/hr/sign-in" });
    }
  },
  component: HrShell,
});

const hrIndexRoute = createRoute({
  getParentRoute: () => hrLayoutRoute,
  path: "/hr",
  beforeLoad: () => {
    throw redirect({ to: "/hr/dashboard" });
  },
  component: () => null,
});

const hrDashboardRoute = createRoute({
  getParentRoute: () => hrLayoutRoute,
  path: "/hr/dashboard",
  component: HrDashboardPage,
});

const hrSecurityRoute = createRoute({
  getParentRoute: () => hrLayoutRoute,
  path: "/hr/security",
  component: HrSecurityPage,
});

const portalLayoutRoute = createRoute({
  getParentRoute: () => rootRoute,
  id: "portal-shell",
  beforeLoad: ({ location }) => {
    if (location.pathname === "/portal/sign-in") return;
    if (!hasValidPortalSession()) {
      throw redirect({ to: "/portal/sign-in" });
    }
  },
  component: PortalShell,
});

const portalIndexRoute = createRoute({
  getParentRoute: () => portalLayoutRoute,
  path: "/portal",
  beforeLoad: () => {
    throw redirect({ to: "/portal/coverage" });
  },
  component: () => null,
});

const portalCoverageRoute = createRoute({
  getParentRoute: () => portalLayoutRoute,
  path: "/portal/coverage",
  component: PortalCoveragePage,
});

// Legacy portal paths — benefits/usage/dependants merged into "My coverage".
const portalBenefitsRedirect = createRoute({
  getParentRoute: () => portalLayoutRoute,
  path: "/portal/benefits",
  beforeLoad: () => {
    throw redirect({ to: "/portal/coverage", search: { tab: "benefits" } });
  },
  component: () => null,
});

const portalUtilizationRedirect = createRoute({
  getParentRoute: () => portalLayoutRoute,
  path: "/portal/utilization",
  beforeLoad: () => {
    throw redirect({ to: "/portal/coverage", search: { tab: "usage" } });
  },
  component: () => null,
});

const portalDependantsRedirect = createRoute({
  getParentRoute: () => portalLayoutRoute,
  path: "/portal/dependants",
  beforeLoad: () => {
    throw redirect({ to: "/portal/coverage", search: { tab: "dependants" } });
  },
  component: () => null,
});

const portalEnrollmentRoute = createRoute({
  getParentRoute: () => portalLayoutRoute,
  path: "/portal/enrollment",
  component: PortalEnrollmentPage,
});

const portalClaimsRoute = createRoute({
  getParentRoute: () => portalLayoutRoute,
  path: "/portal/claims",
  component: PortalClaimsPage,
});

const portalClinicsRoute = createRoute({
  getParentRoute: () => portalLayoutRoute,
  path: "/portal/clinics",
  component: PortalClinicsPage,
});

const portalCardRoute = createRoute({
  getParentRoute: () => portalLayoutRoute,
  path: "/portal/card",
  component: PortalCardPage,
});

const portalNewClaimRoute = createRoute({
  getParentRoute: () => portalLayoutRoute,
  path: "/portal/claims/new",
  component: PortalNewClaimPage,
});

const portalClaimDetailRoute = createRoute({
  getParentRoute: () => portalLayoutRoute,
  path: "/portal/claims/$claimId",
  component: PortalClaimDetailPage,
});

const appLayoutRoute = createRoute({
  getParentRoute: () => rootRoute,
  id: "app-shell",
  beforeLoad: ({ location }) => {
    if (!ENTRA_ENABLED) return;
    if (PUBLIC_PATHS.has(location.pathname)) return;
    if (getActiveAccount() === null) {
      throw redirect({
        to: "/sign-in",
        search: { from: location.pathname } as Record<string, string>,
      });
    }
  },
  component: AppShell,
});

const indexRoute = createRoute({
  getParentRoute: () => appLayoutRoute,
  path: "/",
  beforeLoad: () => {
    throw redirect({ to: "/home" });
  },
  component: () => null,
});

const homeRoute = createRoute({
  getParentRoute: () => appLayoutRoute,
  path: "/home",
  component: HomePage,
});

const dashboardRoute = createRoute({
  getParentRoute: () => appLayoutRoute,
  path: "/dashboard",
  component: CompanyDashboardPage,
});

const schemaLayoutRoute = createRoute({
  getParentRoute: () => appLayoutRoute,
  path: "/schema",
  component: () => <Outlet />,
});

const schemaIndexRoute = createRoute({
  getParentRoute: () => schemaLayoutRoute,
  path: "/",
  component: SchemaPage,
});

// Legacy schema paths — attributes/products are tabs of one page now.
const schemaAttributesRedirect = createRoute({
  getParentRoute: () => schemaLayoutRoute,
  path: "/attributes",
  beforeLoad: () => {
    throw redirect({ to: "/schema", search: { tab: "attributes" } });
  },
  component: () => null,
});

const schemaProductsRedirect = createRoute({
  getParentRoute: () => schemaLayoutRoute,
  path: "/products",
  beforeLoad: () => {
    throw redirect({ to: "/schema", search: { tab: "products" } });
  },
  component: () => null,
});

const configLayoutRoute = createRoute({
  getParentRoute: () => appLayoutRoute,
  path: "/configuration",
  component: () => <Outlet />,
});

const configIndexRoute = createRoute({
  getParentRoute: () => configLayoutRoute,
  path: "/",
  // `tab` is optional deep-link state (?tab=flex) — return it as an optional
  // key so navigations to /configuration without a tab aren't forced to pass one.
  validateSearch: (search: Record<string, unknown>): { tab?: string } =>
    typeof search.tab === "string" ? { tab: search.tab } : {},
  component: ConfigurationPage,
});

const configSettingsRoute = createRoute({
  getParentRoute: () => configLayoutRoute,
  path: "/settings",
  // Tab is optional deep-link state (?tab=aliases); return it optional so bare
  // /configuration/settings navigations aren't forced to pass one.
  validateSearch: (search: Record<string, unknown>): { tab?: string } =>
    typeof search.tab === "string" ? { tab: search.tab } : {},
  component: CompanySettingsPage,
});

const configAIProviderRoute = createRoute({
  getParentRoute: () => configLayoutRoute,
  path: "/ai-provider",
  component: AIProviderPage,
});

const configPanelClinicsRoute = createRoute({
  getParentRoute: () => configLayoutRoute,
  path: "/panel-clinics",
  component: PanelClinicsPage,
});

const opsLayoutRoute = createRoute({
  getParentRoute: () => appLayoutRoute,
  path: "/operations",
  component: () => <Outlet />,
});

const opsIndexRoute = createRoute({
  getParentRoute: () => opsLayoutRoute,
  path: "/",
  beforeLoad: () => {
    throw redirect({ to: "/operations/roster" });
  },
  component: () => null,
});

const opsRosterRoute = createRoute({
  getParentRoute: () => opsLayoutRoute,
  path: "/roster",
  component: RosterPage,
});

const opsCoverageRoute = createRoute({
  getParentRoute: () => opsLayoutRoute,
  path: "/coverage",
  validateSearch: (search: Record<string, unknown>) => ({
    employee: typeof search.employee === "string" ? search.employee : undefined,
    view:
      search.view === "employee"
        ? ("employee" as const)
        : search.view === "broker"
          ? ("broker" as const)
          : undefined,
  }),
  component: EmployeeCoveragePage,
});

// Legacy operations paths — employees/dependants merged into the roster page,
// benefit-statement/employee-view merged into the coverage page.
const opsEmployeesRedirect = createRoute({
  getParentRoute: () => opsLayoutRoute,
  path: "/employees",
  beforeLoad: () => {
    throw redirect({ to: "/operations/roster", search: { tab: "employees" } });
  },
  component: () => null,
});

const opsDependantsRedirect = createRoute({
  getParentRoute: () => opsLayoutRoute,
  path: "/dependants",
  beforeLoad: () => {
    throw redirect({ to: "/operations/roster", search: { tab: "dependants" } });
  },
  component: () => null,
});

const opsBenefitStatementRedirect = createRoute({
  getParentRoute: () => opsLayoutRoute,
  path: "/benefit-statement",
  beforeLoad: () => {
    throw redirect({
      to: "/operations/coverage",
      search: { employee: undefined, view: undefined },
    });
  },
  component: () => null,
});

const opsEmployeeViewRedirect = createRoute({
  getParentRoute: () => opsLayoutRoute,
  path: "/employee-view",
  beforeLoad: ({ location }) => {
    const search = location.search as { employee?: string };
    throw redirect({
      to: "/operations/coverage",
      search: { view: "employee", employee: search.employee },
    });
  },
  component: () => null,
});

// Policy-year management moved into the Configuration page. Keep the old path
// as a redirect so bookmarks/links don't 404.
const opsActivationsRoute = createRoute({
  getParentRoute: () => opsLayoutRoute,
  path: "/activations",
  beforeLoad: () => {
    throw redirect({ to: "/configuration" });
  },
  component: () => null,
});

const opsClaimsRoute = createRoute({
  getParentRoute: () => opsLayoutRoute,
  path: "/claims",
  component: ClaimsQueuePage,
});

// Reports Center moved to a top-level company-scoped route with team tabs.
// Keep the old operations path as a redirect so bookmarks don't 404.
const opsReportsRedirect = createRoute({
  getParentRoute: () => opsLayoutRoute,
  path: "/reports",
  beforeLoad: () => {
    throw redirect({ to: "/reports", search: { tab: "pa" } });
  },
  component: () => null,
});

const reportsRoute = createRoute({
  getParentRoute: () => appLayoutRoute,
  path: "/reports",
  validateSearch: (search: Record<string, unknown>) => ({
    tab: typeof search.tab === "string" ? search.tab : undefined,
  }),
  component: ReportsPage,
});

const opsUnderwritingRoute = createRoute({
  getParentRoute: () => opsLayoutRoute,
  path: "/underwriting",
  component: UnderwritingPage,
});

const enrollmentLayoutRoute = createRoute({
  getParentRoute: () => appLayoutRoute,
  path: "/enrollment",
  component: () => <Outlet />,
});

const enrollmentIndexRoute = createRoute({
  getParentRoute: () => enrollmentLayoutRoute,
  path: "/",
  component: EnrollmentPage,
});

// Legacy enrollment paths — elections/bulk are tabs of the enrollment page.
const enrollmentElectionsRedirect = createRoute({
  getParentRoute: () => enrollmentLayoutRoute,
  path: "/elections",
  beforeLoad: ({ location }) => {
    const search = location.search as { window?: string };
    throw redirect({
      to: "/enrollment",
      search: { tab: "elections", window: search.window },
    });
  },
  component: () => null,
});

const enrollmentBulkRedirect = createRoute({
  getParentRoute: () => enrollmentLayoutRoute,
  path: "/bulk",
  beforeLoad: () => {
    throw redirect({ to: "/enrollment", search: { tab: "bulk" } });
  },
  component: () => null,
});

const adminRoute = createRoute({
  getParentRoute: () => appLayoutRoute,
  path: "/admin",
  component: AdminPage,
});

const routeTree = rootRoute.addChildren([
  authCallbackRoute,
  signInRoute,
  hrSignInRoute,
  hrSetPasswordRoute,
  hrLayoutRoute.addChildren([hrIndexRoute, hrDashboardRoute, hrSecurityRoute]),
  portalSignInRoute,
  portalSetPasswordRoute,
  portalLayoutRoute.addChildren([
    portalIndexRoute,
    portalCoverageRoute,
    portalBenefitsRedirect,
    portalUtilizationRedirect,
    portalDependantsRedirect,
    portalEnrollmentRoute,
    portalClaimsRoute,
    portalClinicsRoute,
    portalCardRoute,
    portalNewClaimRoute,
    portalClaimDetailRoute,
  ]),
  appLayoutRoute.addChildren([
    indexRoute,
    homeRoute,
    dashboardRoute,
    reportsRoute,
    schemaLayoutRoute.addChildren([
      schemaIndexRoute,
      schemaAttributesRedirect,
      schemaProductsRedirect,
    ]),
    configLayoutRoute.addChildren([
      configIndexRoute,
      configSettingsRoute,
      configAIProviderRoute,
      configPanelClinicsRoute,
    ]),
    opsLayoutRoute.addChildren([
      opsIndexRoute,
      opsRosterRoute,
      opsCoverageRoute,
      opsEmployeesRedirect,
      opsDependantsRedirect,
      opsBenefitStatementRedirect,
      opsEmployeeViewRedirect,
      opsClaimsRoute,
      opsReportsRedirect,
      opsUnderwritingRoute,
      opsActivationsRoute,
    ]),
    enrollmentLayoutRoute.addChildren([
      enrollmentIndexRoute,
      enrollmentElectionsRedirect,
      enrollmentBulkRedirect,
    ]),
    adminRoute,
  ]),
]);

export const router = createRouter({
  routeTree,
  defaultErrorComponent: GlobalErrorComponent,
  defaultNotFoundComponent: NotFoundComponent,
});

declare module "@tanstack/react-router" {
  interface Register {
    router: typeof router;
  }
}
