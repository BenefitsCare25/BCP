/** Which tenant an HR / portal request is for, resolved on the client.
 *
 * Two deployment shapes, mirroring the backend's `INSPRO_TENANT_MODE`:
 *
 * - **subdomain** — the surfaces live on `{slug}.hr.<base>` /
 *   `{slug}.portal.<base>` and the slug comes from the hostname. Requires real
 *   per-tenant DNS + a wildcard cert.
 * - **header** (what prod runs) — the whole platform is served from ONE
 *   hostname, because the App Service default `*.azurewebsites.net` cannot have
 *   tenant subdomains at all. The hostname says nothing, so the tenant is
 *   carried explicitly and sent as `X-Inspro-Tenant-Slug`.
 *
 * ## In header mode the PORTAL carries its tenant in the PATH
 *
 * `/portal/cdl/coverage`. The alias is `clients.slug` — derived from the
 * company's name on create and admin-overridable — so nothing here is
 * hardcoded and a new company is routable the moment it exists.
 *
 * It replaced a `?company=<slug>` entry link that was captured into
 * localStorage and then STRIPPED from the address bar. That worked and read as
 * broken: the URL named no company, so a bookmark was useless to anyone but the
 * browser that first opened the invite, clearing site data stranded the member
 * on "type your company code", and two members of different companies could not
 * share a machine. A path segment fixes all three by being the thing it was
 * hiding.
 *
 * **The slug SELECTS, it never AUTHORISES.** Tenancy is resolved server-side
 * from the member token's own `client_id` (`get_current_member`), so a hand-
 * edited path grants nothing — it can only ever name the wrong company, which
 * `PortalMe.company.slug` lets the shell detect and correct.
 *
 * The HR surface is deliberately untouched and still uses the stored slug: it
 * has its own auth (a host-only `SameSite=Strict` refresh cookie) and none of
 * the shareable-link problems above.
 */

const STORAGE_KEY = "inspro.tenantSlug";

/** The portal's path root. The company alias is the segment straight after it. */
export const PORTAL_BASE = "/portal";

/** Segments that sit directly under `/portal` and are therefore NOT a company.
 *
 * Every one of these is a LEGACY path kept alive by a redirect in `router.tsx`
 * — the pre-path-tenancy URLs, which are still sitting in members' mailboxes as
 * live invite links. Without this set `/portal/sign-in` reads as a company
 * called "sign-in", since `sign-in` is a perfectly valid DNS label.
 *
 * The backend reserves these too (`core/tenancy_host.RESERVED_SLUGS`), so no
 * company can ever be given one as its alias. Both lists are needed: that one
 * stops the collision being CREATED, this one stops it being MISREAD by a
 * browser that already has the old link. */
const PORTAL_RESERVED_SEGMENTS: ReadonlySet<string> = new Set([
  "sign-in",
  "set-password",
  "coverage",
  "benefits",
  "utilization",
  "dependants",
  "enrollment",
  "claims",
  "clinics",
  "card",
  "messages",
  "security",
]);
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

/** The company alias in `/portal/<slug>/…`, or null when the path names none.
 *
 * Reads `window.location` rather than the router, because the one caller that
 * matters is `portalClient`'s header builder — a plain fetch wrapper with no
 * React context to read a route param from. That also makes it correct during
 * the very first request of a cold load, before the router has resolved
 * anything. */
export function tenantSlugFromPath(): string | null {
  const [, base, segment] = window.location.pathname.split("/");
  if (`/${base ?? ""}` !== PORTAL_BASE || !segment) return null;
  let decoded = segment;
  try {
    decoded = decodeURIComponent(segment);
  } catch {
    // A malformed escape is not a slug; fall through to the raw segment, which
    // `validSlug` then rejects.
  }
  if (PORTAL_RESERVED_SEGMENTS.has(decoded.toLowerCase())) return null;
  return validSlug(decoded);
}

/** Build a portal URL for one company. The single place the shape is written,
 *  so moving it later is one edit rather than 87. */
export function portalPath(
  slug: string | null | undefined,
  subpath = "",
): string {
  const tail = !subpath || subpath === "/" ? "" : subpath.startsWith("/") ? subpath : `/${subpath}`;
  // No slug is a real state, not a bug: a member arriving with nothing to go on
  // lands on bare `/portal`, which asks which company they belong to.
  return slug ? `${PORTAL_BASE}/${slug}${tail}` : `${PORTAL_BASE}${tail}`;
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
 * Capture `?company=<slug>` from the entry URL. Call once at boot, before the
 * router renders.
 *
 * On a PORTAL path the slug is promoted into the path (`/portal/sign-in
 * ?company=cdl` → `/portal/cdl/sign-in`) rather than merely stored. Those old
 * links are not hypothetical: `portal_sign_in_url()` has been emailing them,
 * and an unopened invite is a live one-time password with an `INVITE_TTL_DAYS`
 * life, so they must keep resolving. Rewriting rather than storing also means
 * the member gets the durable address in their bar from the first frame, and it
 * survives a browser with storage disabled — the one case the strip-and-remember
 * design could not handle at all.
 *
 * Elsewhere (HR) it is stored and stripped, as before.
 */
export function captureTenantSlugFromUrl(): void {
  if (!isHeaderMode()) return;
  const url = new URL(window.location.href);
  const raw = url.searchParams.get(TENANT_QUERY_PARAM);
  if (!raw) return;
  const slug = validSlug(raw);
  if (!slug) return;
  // Remembered either way: it is still the fallback for a bare `/portal`.
  rememberTenantSlug(slug);
  url.searchParams.delete(TENANT_QUERY_PARAM);
  if (url.pathname === PORTAL_BASE || url.pathname.startsWith(`${PORTAL_BASE}/`)) {
    // Only when the path does not already name one — an explicit path alias
    // outranks a stale query param, same precedence as `currentPortalTenantSlug`.
    if (tenantSlugFromPath() === null) {
      url.pathname = portalPath(slug, url.pathname.slice(PORTAL_BASE.length));
    }
  }
  window.history.replaceState({}, "", url.toString());
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

/** **The PATH wins over storage**, and that ordering is the whole point.
 *
 * Storage is one value for the whole browser, so with it in front, opening a
 * colleague's `/portal/acme` link on a machine that had signed into `cdl` would
 * send `cdl` while the address bar said `acme` — the request succeeds against
 * the wrong company and the URL is a lie. The path is per-tab, per-link and
 * visible; it is the more specific answer and it goes first.
 *
 * Storage stays as the fallback so a member who bookmarked bare `/portal`, or
 * who is mid-sign-in before any company path exists, still resolves. */
export function currentPortalTenantSlug(): string {
  if (!isHeaderMode()) return currentTenantSlug("portal");
  return tenantSlugFromPath() ?? storedSlug() ?? "";
}

/** True when the UI must ask the user which company they belong to. */
export function needsTenantSelection(): boolean {
  return (
    isHeaderMode() && tenantSlugFromPath() === null && storedSlug() === null
  );
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
 * every surface shares one origin, so the tenant has to ride in the URL itself
 * — without it the recipient lands on a page that doesn't know which company
 * they belong to.
 *
 * **Portal links carry it in the path** (`/portal/cdl/sign-in`), so what the
 * member receives is the same address they will use forever after. HR keeps the
 * `?company=` form, which `captureTenantSlugFromUrl` stores and strips.
 *
 * `path` is the surface-rooted path as it is written everywhere else
 * (`/portal/sign-in`); the alias is inserted, never appended by the caller.
 */
export function tenantSurfaceUrl(
  surface: Surface,
  slug: string | null | undefined,
  path: string,
): string {
  if (isHeaderMode()) {
    if (surface === "portal") {
      const subpath = path.startsWith(PORTAL_BASE)
        ? path.slice(PORTAL_BASE.length)
        : path;
      return new URL(portalPath(slug, subpath), window.location.origin).toString();
    }
    const url = new URL(path, window.location.origin);
    if (slug) url.searchParams.set(TENANT_QUERY_PARAM, slug);
    return url.toString();
  }
  const base = (import.meta.env.VITE_TENANT_BASE_DOMAIN as string | undefined)?.trim();
  if (!base || !slug) return new URL(path, window.location.origin).toString();
  const protocol = window.location.protocol === "http:" ? "http:" : "https:";
  return `${protocol}//${slug}.${surface}.${base}${path}`;
}
