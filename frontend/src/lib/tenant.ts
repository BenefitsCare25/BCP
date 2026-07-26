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

/**
 * An ABSOLUTE url for a page on a tenant surface.
 *
 * Needed because the broker app is served from its own host: a set-password
 * link generated there points at `{slug}.hr.<base>` / `{slug}.portal.<base>`,
 * not at the broker origin. Emitting a bare path ("/hr/set-password?token=…")
 * produced something unclickable when pasted into an email — and the token is
 * revealed once, so recovering meant re-issuing it.
 *
 * `VITE_TENANT_BASE_DOMAIN` is the apex the subdomains hang off (mirrors the
 * backend's `base_domain`). Without it — local dev, where every surface is on
 * one origin — fall back to the current origin so the link still works.
 */
export function tenantSurfaceUrl(
  surface: "hr" | "portal",
  slug: string | null | undefined,
  path: string,
): string {
  const base = (import.meta.env.VITE_TENANT_BASE_DOMAIN as string | undefined)?.trim();
  if (!base || !slug) return new URL(path, window.location.origin).toString();
  const protocol = window.location.protocol === "http:" ? "http:" : "https:";
  return `${protocol}//${slug}.${surface}.${base}${path}`;
}
