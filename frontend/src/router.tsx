import {
  Outlet,
  createRootRoute,
  createRoute,
  createRouter,
  lazyRouteComponent,
  isRedirect,
  redirect,
} from "@tanstack/react-router";
import { AppShell } from "@/components/shell/AppShell";
import {
  GlobalErrorComponent,
  NotFoundComponent,
} from "@/components/shell/ErrorBoundary";
import { ENTRA_ENABLED, clearLocalSession, getActiveAccount } from "@/auth/msal";
import { DENIED_SEARCH, NoAccessError, SIGN_IN_PATH } from "@/api/client";
import { ensureMe } from "@/api/me";
import { PortalShell } from "@/components/portal/PortalShell";
import { hasValidPortalSession } from "@/stores/portalSession";
import { currentPortalTenantSlug } from "@/lib/tenant";
import { HrShell } from "@/components/hr/HrShell";
import { hasValidHrSession } from "@/stores/hrSession";
import { refreshHrSession } from "@/api/hrClient";

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
const PortalHomePage = lazyRouteComponent(
  () => import("@/routes/portal/home"),
  "PortalHomePage",
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
const PortalMessagesPage = lazyRouteComponent(
  () => import("@/routes/portal/messages"),
  "PortalMessagesPage",
);
const PortalCardPage = lazyRouteComponent(
  () => import("@/routes/portal/card"),
  "PortalCardPage",
);
const PortalSecurityPage = lazyRouteComponent(
  () => import("@/routes/portal/security"),
  "PortalSecurityPage",
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
const PUBLIC_PATHS = new Set(["/auth/callback", SIGN_IN_PATH]);

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
  path: SIGN_IN_PATH,
  beforeLoad: ({ search }) => {
    // `denied` means we just bounced a refused account here — never bounce it
    // back, even if clearing the local Microsoft session failed. This is what
    // makes a redirect loop structurally impossible.
    if ((search as { denied?: unknown }).denied) return;
    // If we're already signed in, skip the render flash and go straight home.
    if (ENTRA_ENABLED && getActiveAccount() !== null) {
      throw redirect({ to: "/" });
    }
  },
  component: SignInPage,
});

// ── Employee portal — a sibling surface with its OWN auth (member token, not
// MSAL). The broker shell's guard never runs for /portal/* routes.
//
// **Every portal route carries the company as `/portal/$company/…`.** The
// alias is `clients.slug`, derived from the company's name and admin-editable,
// so nothing is hardcoded and a new company routes the moment it exists. See
// `lib/tenant.ts` for why the path beat the `?company=` param it replaced.
//
// The `portalLegacyRedirect` routes below keep the OLD pathless URLs alive.
// That is not politeness: `portal_sign_in_url()` has been emailing
// `/portal/sign-in?company=cdl`, and an unopened invite is a live one-time
// password for `INVITE_TTL_DAYS`. A static segment outranks a dynamic one in
// TanStack's matcher, so `/portal/coverage` resolves here and never as a
// company called "coverage" — and `RESERVED_SLUGS` stops such a company
// existing in the first place.
const portalSignInRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/portal/$company/sign-in",
  beforeLoad: ({ params }) => {
    // Already signed in (and not following a fresh magic link) → straight in.
    if (hasValidPortalSession() && !window.location.search.includes("code=")) {
      throw redirect({
        to: "/portal/$company/coverage",
        params: { company: params.company },
      });
    }
  },
  component: PortalSignInPage,
});

const portalSetPasswordRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/portal/$company/set-password",
  component: PortalSetPasswordPage,
});

/** An old pathless `/portal/...` URL, forwarded to its company-scoped home.
 *
 * The company comes from `currentPortalTenantSlug()`, which by this point has
 * already absorbed any `?company=` on the entry URL (`captureTenantSlugFromUrl`
 * runs at boot, before the router) and otherwise falls back to the remembered
 * slug. With neither, there is genuinely nothing to resolve — the member is
 * sent to the pathless sign-in, which asks which company they belong to. Search
 * params are carried through: `/portal/set-password?token=…` is one of these,
 * and dropping the token would strand a member on a dead form. */
function portalLegacyRedirect(path: string, subpath: string) {
  return createRoute({
    getParentRoute: () => rootRoute,
    path,
    beforeLoad: ({ search }) => {
      const company = currentPortalTenantSlug();
      if (!company) return;
      throw redirect({
        // Built by concatenation, so the union of literal route paths can't be
        // inferred — the table below is the exhaustive list and every entry has
        // a matching route, which `portalLegacyRoutes` is right beside so the
        // two cannot drift apart unnoticed.
        to: `/portal/$company${subpath}` as "/portal/$company",
        params: { company },
        search,
      });
    },
    // Reached only with no resolvable company. Sign-in asks for one; every
    // other legacy path needs a session anyway, so it is the right landing.
    component: PortalSignInPage,
  });
}

/** Sign-in with NO company known — declared explicitly rather than through the
 * helper because it is the one legacy path that is also a real destination, and
 * everything that bounces an unauthenticated member needs to name it in a typed
 * `to:` (the helper builds its `path` from a variable, so those routes are not
 * in the router's literal path union). It asks which company, then moves to
 * `/portal/{slug}/sign-in`. */
const portalRootSignInRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/portal/sign-in",
  beforeLoad: ({ search }) => {
    const company = currentPortalTenantSlug();
    if (!company) return;
    throw redirect({ to: "/portal/$company/sign-in", params: { company }, search });
  },
  component: PortalSignInPage,
});

const portalLegacyRoutes = [
  ["/portal/set-password", "/set-password"],
  // Two-segment legacy paths. `$company` eats the first segment, so without
  // these `/portal/claims/new` resolves to a company called "claims" with a
  // child route `new` that does not exist, and a bookmarked claim renders the
  // BROKER-styled not-found page inside the member's world.
  ["/portal/claims/new", "/claims/new"],
  ["/portal/coverage", "/coverage"],
  ["/portal/benefits", "/benefits"],
  ["/portal/utilization", "/utilization"],
  ["/portal/dependants", "/dependants"],
  ["/portal/enrollment", "/enrollment"],
  ["/portal/claims", "/claims"],
  ["/portal/clinics", "/clinics"],
  ["/portal/card", "/card"],
  ["/portal/messages", "/messages"],
  ["/portal/security", "/security"],
  ["/portal", ""],
].map(([path, subpath]) => portalLegacyRedirect(path, subpath));

/** A bookmarked claim, `/portal/claims/{id}`. Separate from the table because
 *  it carries a param through to the new path rather than being a fixed
 *  rewrite. */
const portalLegacyClaimRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/portal/claims/$claimId",
  beforeLoad: ({ params, search }) => {
    const company = currentPortalTenantSlug();
    if (!company) return;
    throw redirect({
      to: "/portal/$company/claims/$claimId",
      params: { company, claimId: params.claimId },
      search,
    });
  },
  component: PortalSignInPage,
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
  beforeLoad: async ({ location }) => {
    if (
      location.pathname === "/hr/sign-in" ||
      location.pathname === "/hr/set-password"
    ) {
      return;
    }
    // The access token lives ~10 min; the refresh session ~12h. If the access
    // token has expired, try a silent refresh against the cookie BEFORE bouncing
    // to sign-in — otherwise navigation forces a full re-login every 10 minutes.
    if (!hasValidHrSession() && !(await refreshHrSession())) {
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
  beforeLoad: ({ params }) => {
    if (!hasValidPortalSession()) {
      // Back to THIS company's sign-in, not the pathless one — an expired
      // session must not cost the member the company their link named.
      const company = (params as { company?: string }).company;
      if (company) {
        throw redirect({ to: "/portal/$company/sign-in", params: { company } });
      }
      throw redirect({ to: "/portal/sign-in" });
    }
  },
  component: PortalShell,
});

// `/portal` is now the home mosaic rather than a redirect to Coverage. The
// mosaic answers all four questions members arrive with; Coverage's three tabs
// are reached from the tiles that summarise them.
const portalIndexRoute = createRoute({
  getParentRoute: () => portalLayoutRoute,
  path: "/portal/$company",
  component: PortalHomePage,
});

const portalCoverageRoute = createRoute({
  getParentRoute: () => portalLayoutRoute,
  path: "/portal/$company/coverage",
  component: PortalCoveragePage,
});

// Legacy portal paths — benefits/usage/dependants merged into "My coverage".
const portalBenefitsRedirect = createRoute({
  getParentRoute: () => portalLayoutRoute,
  path: "/portal/$company/benefits",
  beforeLoad: ({ params }) => {
    throw redirect({
      to: "/portal/$company/coverage",
      params: { company: params.company },
      search: { tab: "benefits" },
    });
  },
  component: () => null,
});

const portalUtilizationRedirect = createRoute({
  getParentRoute: () => portalLayoutRoute,
  path: "/portal/$company/utilization",
  beforeLoad: ({ params }) => {
    throw redirect({
      to: "/portal/$company/coverage",
      params: { company: params.company },
      search: { tab: "usage" },
    });
  },
  component: () => null,
});

const portalDependantsRedirect = createRoute({
  getParentRoute: () => portalLayoutRoute,
  path: "/portal/$company/dependants",
  beforeLoad: ({ params }) => {
    throw redirect({
      to: "/portal/$company/coverage",
      params: { company: params.company },
      search: { tab: "dependants" },
    });
  },
  component: () => null,
});

const portalEnrollmentRoute = createRoute({
  getParentRoute: () => portalLayoutRoute,
  path: "/portal/$company/enrollment",
  component: PortalEnrollmentPage,
});

const portalClaimsRoute = createRoute({
  getParentRoute: () => portalLayoutRoute,
  path: "/portal/$company/claims",
  component: PortalClaimsPage,
});

const portalClinicsRoute = createRoute({
  getParentRoute: () => portalLayoutRoute,
  path: "/portal/$company/clinics",
  component: PortalClinicsPage,
});

const portalCardRoute = createRoute({
  getParentRoute: () => portalLayoutRoute,
  path: "/portal/$company/card",
  component: PortalCardPage,
});

// Deliberately NOT a nav destination. The home's Messages tile is the way in
// (and its unread badge is on Home in the dock) — a seventh pill would not fit
// the one-row desktop bar, and the phone dock is settled at five.
const portalMessagesRoute = createRoute({
  getParentRoute: () => portalLayoutRoute,
  path: "/portal/$company/messages",
  component: PortalMessagesPage,
});

const portalSecurityRoute = createRoute({
  getParentRoute: () => portalLayoutRoute,
  path: "/portal/$company/security",
  component: PortalSecurityPage,
});

const portalNewClaimRoute = createRoute({
  getParentRoute: () => portalLayoutRoute,
  path: "/portal/$company/claims/new",
  component: PortalNewClaimPage,
});

const portalClaimDetailRoute = createRoute({
  getParentRoute: () => portalLayoutRoute,
  path: "/portal/$company/claims/$claimId",
  component: PortalClaimDetailPage,
});

const appLayoutRoute = createRoute({
  getParentRoute: () => rootRoute,
  id: "app-shell",
  beforeLoad: async ({ location }) => {
    if (!ENTRA_ENABLED) return;
    if (PUBLIC_PATHS.has(location.pathname)) return;
    if (getActiveAccount() === null) {
      throw redirect({
        to: "/sign-in",
        search: { from: location.pathname } as Record<string, string>,
      });
    }
    // A Microsoft account is not access. The platform grants access from its
    // OWN user list, so resolve /me before rendering anything — otherwise an
    // unprovisioned account lands in the shell and every request 403s.
    let denied = false;
    try {
      await ensureMe();
    } catch (err) {
      // `redirect` throws — never swallow another route's redirect here.
      if (isRedirect(err)) throw err;
      if (err instanceof NoAccessError) denied = true;
      // Backend down / transient: render the app and let the page surface it,
      // rather than locking out a legitimate user on a blip.
    }
    if (denied) {
      // Drop the local Microsoft session so the sign-in page renders instead of
      // bouncing them back in, then say why they're there.
      await clearLocalSession();
      throw redirect({ to: SIGN_IN_PATH, search: DENIED_SEARCH });
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

// Broker routes are grouped by the SIDEBAR GROUP that owns them, so a URL and
// the nav entry that reaches it always agree (`/policy-admin/member-listing` is
// Policy Admin → Member Listing). `components/shell/nav.ts` is the single source
// of the labels; these paths mirror its groups. There are deliberately NO legacy
// redirects — the old `/operations/*` and `/configuration/*` paths are gone.

// ── Client Relations ─────────────────────────────────────────────────────────
const crLayoutRoute = createRoute({
  getParentRoute: () => appLayoutRoute,
  path: "/client-relations",
  component: () => <Outlet />,
});

const crIndexRoute = createRoute({
  getParentRoute: () => crLayoutRoute,
  path: "/",
  beforeLoad: () => {
    throw redirect({ to: "/client-relations/company-benefits" });
  },
  component: () => null,
});

const crCompanyBenefitsRoute = createRoute({
  getParentRoute: () => crLayoutRoute,
  path: "/company-benefits",
  // `tab` is optional deep-link state (?tab=flex) — return it as an optional key
  // so navigations without a tab aren't forced to pass one.
  validateSearch: (search: Record<string, unknown>): { tab?: string } =>
    typeof search.tab === "string" ? { tab: search.tab } : {},
  component: ConfigurationPage,
});

const crEnrollmentRoute = createRoute({
  getParentRoute: () => crLayoutRoute,
  path: "/enrollment",
  component: EnrollmentPage,
});

// ── Policy Admin ─────────────────────────────────────────────────────────────
const paLayoutRoute = createRoute({
  getParentRoute: () => appLayoutRoute,
  path: "/policy-admin",
  component: () => <Outlet />,
});

const paIndexRoute = createRoute({
  getParentRoute: () => paLayoutRoute,
  path: "/",
  beforeLoad: () => {
    throw redirect({ to: "/policy-admin/member-listing" });
  },
  component: () => null,
});

const paMemberListingRoute = createRoute({
  getParentRoute: () => paLayoutRoute,
  path: "/member-listing",
  component: RosterPage,
});

const paCoverageRoute = createRoute({
  getParentRoute: () => paLayoutRoute,
  path: "/coverage-members",
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

const paPanelClinicsRoute = createRoute({
  getParentRoute: () => paLayoutRoute,
  path: "/panel-clinics",
  component: PanelClinicsPage,
});

const paUnderwritingRoute = createRoute({
  getParentRoute: () => paLayoutRoute,
  path: "/underwriting",
  component: UnderwritingPage,
});

// ── Claims ───────────────────────────────────────────────────────────────────
const claimsLayoutRoute = createRoute({
  getParentRoute: () => appLayoutRoute,
  path: "/claims",
  component: () => <Outlet />,
});

const claimsIndexRoute = createRoute({
  getParentRoute: () => claimsLayoutRoute,
  path: "/",
  beforeLoad: () => {
    throw redirect({ to: "/claims/review" });
  },
  component: () => null,
});

const claimsReviewRoute = createRoute({
  getParentRoute: () => claimsLayoutRoute,
  path: "/review",
  component: ClaimsQueuePage,
});

// Reports Center spans every team (Policy Admin / Claims / Flex tabs); it lives
// under /claims because that is the sidebar group it is listed in.
const claimsReportsRoute = createRoute({
  getParentRoute: () => claimsLayoutRoute,
  path: "/reports",
  validateSearch: (search: Record<string, unknown>) => ({
    tab: typeof search.tab === "string" ? search.tab : undefined,
  }),
  component: ReportsPage,
});

// ── Settings ─────────────────────────────────────────────────────────────────
const settingsLayoutRoute = createRoute({
  getParentRoute: () => appLayoutRoute,
  path: "/settings",
  component: () => <Outlet />,
});

const settingsIndexRoute = createRoute({
  getParentRoute: () => settingsLayoutRoute,
  path: "/",
  beforeLoad: () => {
    throw redirect({ to: "/settings/company" });
  },
  component: () => null,
});

const settingsCompanyRoute = createRoute({
  getParentRoute: () => settingsLayoutRoute,
  path: "/company",
  // Tab is optional deep-link state (?tab=aliases).
  validateSearch: (search: Record<string, unknown>): { tab?: string } =>
    typeof search.tab === "string" ? { tab: search.tab } : {},
  component: CompanySettingsPage,
});

const settingsAIRoute = createRoute({
  getParentRoute: () => settingsLayoutRoute,
  path: "/ai",
  component: AIProviderPage,
});

// ── Firm-wide ────────────────────────────────────────────────────────────────
const firmLayoutRoute = createRoute({
  getParentRoute: () => appLayoutRoute,
  path: "/firm",
  component: () => <Outlet />,
});

const firmIndexRoute = createRoute({
  getParentRoute: () => firmLayoutRoute,
  path: "/",
  beforeLoad: () => {
    throw redirect({ to: "/firm/schema" });
  },
  component: () => null,
});

const firmSchemaRoute = createRoute({
  getParentRoute: () => firmLayoutRoute,
  path: "/schema",
  component: SchemaPage,
});

const firmAccessRoute = createRoute({
  getParentRoute: () => firmLayoutRoute,
  path: "/access",
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
  portalRootSignInRoute,
  ...portalLegacyRoutes,
  portalLegacyClaimRoute,
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
    portalMessagesRoute,
    portalSecurityRoute,
    portalNewClaimRoute,
    portalClaimDetailRoute,
  ]),
  appLayoutRoute.addChildren([
    indexRoute,
    homeRoute,
    dashboardRoute,
    crLayoutRoute.addChildren([
      crIndexRoute,
      crCompanyBenefitsRoute,
      crEnrollmentRoute,
    ]),
    paLayoutRoute.addChildren([
      paIndexRoute,
      paMemberListingRoute,
      paCoverageRoute,
      paPanelClinicsRoute,
      paUnderwritingRoute,
    ]),
    claimsLayoutRoute.addChildren([
      claimsIndexRoute,
      claimsReviewRoute,
      claimsReportsRoute,
    ]),
    settingsLayoutRoute.addChildren([
      settingsIndexRoute,
      settingsCompanyRoute,
      settingsAIRoute,
    ]),
    firmLayoutRoute.addChildren([
      firmIndexRoute,
      firmSchemaRoute,
      firmAccessRoute,
    ]),
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
