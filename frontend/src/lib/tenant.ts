/** Which tenant an HR / portal request is for, resolved on the client.
 *
 * Two deployment shapes, mirroring the backend's `INSPRO_TENANT_MODE`:
 *
 * - **subdomain** (default) — the surfaces live on `{slug}.hr.<base>` /
 *   `{slug}.portal.<base>` and the slug comes from the hostname. Requires real
 *   per-tenant DNS + a wildcard cert.
 * - **header** — the whole platform is served from ONE hostname (no custom
 *   domain, e.g. the App Service default `*.azurewebsites.net`, where tenant
 *   subdomains cannot exist). The hostname says nothing, so the tenant is
 *   carried explicitly: `?company=<slug>` on the entry link, remembered in
 *   localStorage, and sent as `X-Inspro-Tenant-Slug`.
 *
 * The slug only SELECTS a tenant; the backend still enforces that the session
 * token's `cid` matches it, so remembering it client-side grants nothing.
 */

const STORAGE_KEY = "inspro.tenantSlug";
/** Entry-link param naming the company. "company" reads better than "slug" in
 *  an invite email, which is where these links are pasted. */
export const TENANT_QUERY_PARAM = "company";

type Surface = "hr" | "portal";

function isHeaderMode(): boolean {
  return (
    ((import.meta.env.VITE_TENANT_MODE as string | undefined) ?? "subdomain")
      .trim()
      .toLowerCase() === "header"
  );
}

export function tenantSlugFromHost(surface: Surface): string | null {
  const host = window.location.hostname.toLowerCase();
  const m = host.match(new RegExp(`^([a-z0-9-]+)\\.${surface}\\.`));
  return m ? m[1] : null;
}

/** Same DNS-label rule the backend enforces — reject junk before storing it. */
function validSlug(raw: string | null | undefined): string | null {
  const slug = (raw ?? "").trim().toLowerCase();
  if (!slug || slug.length > 63) return null;
  return /^(?!-)(?!.*--)[a-z0-9-]+(?<!-)$/.test(slug) ? slug : null;
}

function storedSlug(): string | null {
  try {
    return validSlug(window.localStorage.getItem(STORAGE_KEY));
  } catch {
    // Private-mode / disabled storage — fall through to the other sources
    // rather than breaking sign-in entirely.
    return null;
  }
}

export function rememberTenantSlug(slug: string): boolean {
  const ok = validSlug(slug);
  if (!ok) return false;
  try {
    window.localStorage.setItem(STORAGE_KEY, ok);
  } catch {
    // Non-fatal: the in-URL param still drives this page load.
  }
  return true;
}

export function forgetTenantSlug(): void {
  try {
    window.localStorage.removeItem(STORAGE_KEY);
  } catch {
    /* nothing to clean up */
  }
}

/**
 * Capture `?company=<slug>` from the entry URL into storage, then strip it so
 * the slug doesn't linger in the address bar (and in any pasted/shared link)
 * for the rest of the session. Call once at boot, before the router renders.
 */
export function captureTenantSlugFromUrl(): void {
  if (!isHeaderMode()) return;
  const url = new URL(window.location.href);
  const raw = url.searchParams.get(TENANT_QUERY_PARAM);
  if (!raw) return;
  if (rememberTenantSlug(raw)) {
    url.searchParams.delete(TENANT_QUERY_PARAM);
    window.history.replaceState({}, "", url.toString());
  }
}

/** The slug to send, or "" when the user must still tell us their company. */
function currentTenantSlug(surface: Surface): string {
  if (!isHeaderMode()) {
    return (
      tenantSlugFromHost(surface) ??
      (import.meta.env.VITE_TENANT_DEV_SLUG as string | undefined) ??
      "demo"
    );
  }
  // Single-host: the hostname can't tell us, so an explicitly chosen slug is
  // the only honest answer. Empty makes the backend 400 with a clear message
  // instead of silently signing the user into someone else's tenant.
  return storedSlug() ?? "";
}

export function currentHrTenantSlug(): string {
  return currentTenantSlug("hr");
}

export function currentPortalTenantSlug(): string {
  return currentTenantSlug("portal");
}

/** True when the UI must ask the user which company they belong to. */
export function needsTenantSelection(): boolean {
  return isHeaderMode() && storedSlug() === null;
}

/**
 * An ABSOLUTE url for a page on a tenant surface.
 *
 * Needed because a set-password link is generated on the broker app but must
 * open on the member's/HR's surface. Emitting a bare path produced something
 * unclickable when pasted into an email — and the token is revealed once, so
 * recovering meant re-issuing it.
 *
 * In subdomain mode that's `{slug}.{surface}.<base>{path}`. In header mode
 * every surface shares one origin, so the tenant rides as `?company=<slug>`
 * (picked up by `captureTenantSlugFromUrl`) — without it the recipient would
 * land on a page that doesn't know which company they belong to.
 */
export function tenantSurfaceUrl(
  surface: Surface,
  slug: string | null | undefined,
  path: string,
): string {
  if (isHeaderMode()) {
    const url = new URL(path, window.location.origin);
    if (slug) url.searchParams.set(TENANT_QUERY_PARAM, slug);
    return url.toString();
  }
  const base = (import.meta.env.VITE_TENANT_BASE_DOMAIN as string | undefined)?.trim();
  if (!base || !slug) return new URL(path, window.location.origin).toString();
  const protocol = window.location.protocol === "http:" ? "http:" : "https:";
  return `${protocol}//${slug}.${surface}.${base}${path}`;
}
