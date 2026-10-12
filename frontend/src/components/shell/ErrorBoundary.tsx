import { Link, useRouter, useRouterState } from "@tanstack/react-router";
import { AlertTriangle, Home } from "lucide-react";
import { Button } from "@/components/ui/button";
import { formatError } from "@/lib/errors";
import { usePortalTranslation } from "@/i18n/portal";
import { currentPortalTenantSlug, tenantSlugFromPath } from "@/lib/tenant";

/** Home for the surface on screen. These pages are the router-wide defaults, so
 *  a member or HR user reaches them too, and the broker root sends anyone
 *  without a staff session to the STAFF sign-in. */
function BackHome() {
  const pt = usePortalTranslation();
  const pathname = useRouterState({ select: (state) => state.location.pathname });
  const label = (
    <>
      <Home className="size-4" />
      {pt("Back to home")}
    </>
  );
  if (/^\/hr(?:\/|$)/.test(pathname)) {
    return <Button asChild><Link to="/hr/dashboard">{label}</Link></Button>;
  }
  if (/^\/portal(?:\/|$)/.test(pathname)) {
    const company = tenantSlugFromPath() ?? currentPortalTenantSlug();
    return company ? (
      <Button asChild><Link to="/portal/$company" params={{ company }}>{label}</Link></Button>
    ) : (
      <Button asChild><Link to="/portal/sign-in">{label}</Link></Button>
    );
  }
  return <Button asChild><Link to="/home">{label}</Link></Button>;
}

export function GlobalErrorComponent({
  error,
  reset,
}: {
  error: unknown;
  reset?: () => void;
}) {
  const pt = usePortalTranslation();
  const router = useRouter();
  // Re-run the route's guard and loaders, then clear the boundary. Resetting
  // alone only clears React state: a match whose guard threw (a session that
  // could not be confirmed) stays in error and the page would just come back.
  const retry = () => {
    void router.invalidate();
    reset?.();
  };
  return (
    <div className="flex h-screen w-screen items-center justify-center bg-background p-6">
      <div className="flex max-w-md flex-col items-start gap-4 rounded-xl border border-border bg-card p-6 shadow-sm">
        <div className="flex items-center gap-2 text-error">
          <AlertTriangle className="size-5" />
          <span className="text-base font-semibold">{pt("Something went wrong")}</span>
        </div>
        <p className="text-sm text-muted-foreground">{pt(formatError(error))}</p>
        <div className="flex gap-2">
          <Button variant="outline" onClick={retry}>
            {pt("Try again")}
          </Button>
          <BackHome />
        </div>
      </div>
    </div>
  );
}

export function NotFoundComponent() {
  const pt = usePortalTranslation();
  return (
    <div className="flex h-full w-full items-center justify-center p-6">
      <div className="flex max-w-md flex-col items-start gap-4 rounded-xl border border-border bg-card p-6 shadow-sm">
        <div className="text-base font-semibold text-foreground">{pt("Page not found")}</div>
        <p className="text-sm text-muted-foreground">
          {pt("The URL you requested doesn't match any route in this workspace.")}
        </p>
        <BackHome />
      </div>
    </div>
  );
}
