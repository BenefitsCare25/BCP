/** Tenant-per-subdomain resolution on the client.
 *
 * In production the HR/portal surfaces live on `{slug}.hr.<base>` /
 * `{slug}.portal.<base>`, so the slug comes from the hostname. Locally there is
 * no subdomain, so we fall back to `VITE_HR_DEV_SLUG` (default "demo") and send
 * it as `X-Inspro-Tenant-Slug` — a header the backend honours only in non-prod.
 */
export function tenantSlugFromHost(surface: "hr" | "portal"): string | null {
  const host = window.location.hostname.toLowerCase();
  const m = host.match(new RegExp(`^([a-z0-9-]+)\\.${surface}\\.`));
  return m ? m[1] : null;
}

export function currentHrTenantSlug(): string {
  return (
    tenantSlugFromHost("hr") ??
    (import.meta.env.VITE_HR_DEV_SLUG as string | undefined) ??
    "demo"
  );
}

export function currentPortalTenantSlug(): string {
  return (
    tenantSlugFromHost("portal") ??
    (import.meta.env.VITE_PORTAL_DEV_SLUG as string | undefined) ??
    "demo"
  );
}
