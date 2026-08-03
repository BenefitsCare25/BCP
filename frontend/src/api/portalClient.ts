/** Fetch wrapper for the employee portal — separate from the broker `api`.
 *
 * Attaches the member bearer token from the portal session store (never MSAL,
 * never `X-Inspro-Client` — a member is pinned to one client server-side).
 * A 401 clears the session and sends the member back to the portal sign-in.
 */
import { errorFromText, parseErrorText } from "@/lib/errors";
import { currentPortalTenantSlug, portalPath } from "@/lib/tenant";
import { usePortalSession } from "@/stores/portalSession";

const API_BASE = import.meta.env.VITE_API_BASE_URL ?? "/api/v1";

function tenantHeader(): Record<string, string> {
  return { "X-Inspro-Tenant-Slug": currentPortalTenantSlug() };
}

export class PortalUnauthorizedError extends Error {
  constructor(message = "Portal session expired") {
    super(message);
    this.name = "PortalUnauthorizedError";
  }
}

function authHeader(): Record<string, string> {
  const token = usePortalSession.getState().token;
  return token ? { Authorization: `Bearer ${token}` } : {};
}

function handleUnauthorized(): never {
  usePortalSession.getState().clearSession();
  // Back to THIS company's sign-in. Dropping the segment on a routine session
  // expiry sent the member to the pathless page, which turns the company field
  // back on and sends an EMPTY tenant header — so their re-sign-in 400s until
  // they type a code they were never given.
  const target = portalPath(currentPortalTenantSlug(), "/sign-in");
  // Both forms are checked, so the guard still stops a redirect loop when the
  // slug cannot be resolved and `target` collapses to the pathless path.
  if (
    window.location.pathname !== target &&
    window.location.pathname !== "/portal/sign-in"
  ) {
    window.location.assign(target);
  }
  throw new PortalUnauthorizedError();
}

async function request<T>(
  path: string,
  init: RequestInit = {},
  /** Set when the request BODY carries a credential the member just typed —
   * see `portalApi.verify`. */
  opts: { credential?: boolean } = {},
): Promise<T> {
  const res = await fetch(`${API_BASE}${path}`, {
    ...init,
    headers: {
      "Content-Type": "application/json",
      ...tenantHeader(),
      ...authHeader(),
      ...init.headers,
    },
  });
  if (res.status === 401 && !opts.credential) return handleUnauthorized();
  if (!res.ok) {
    // Coded 409s (e.g. unpriced_elections / flex_overdrawn on enrollment
    // submit) surface as ConflictDetailError so pages can offer a choice.
    throw errorFromText(res.status, await res.text(), res.statusText);
  }
  if (res.status === 204) return undefined as T;
  return (await res.json()) as T;
}

export const portalApi = {
  get: <T>(path: string) => request<T>(path),
  post: <T>(path: string, body: unknown) =>
    request<T>(path, { method: "POST", body: JSON.stringify(body) }),
  put: <T>(path: string, body: unknown) =>
    request<T>(path, { method: "PUT", body: JSON.stringify(body) }),
  /** A POST whose BODY carries a credential the member just typed — the 6-digit
   * code confirming 2FA enrolment, the password confirming they may turn it
   * off. Here a 401 means "that value is wrong", not "your session expired",
   * so it must surface to the form.
   *
   * Routing these through `post` signed the member OUT for mistyping their own
   * setup code: `handleUnauthorized` cleared the session and navigated away
   * before the form's onError could render a word. */
  verify: <T>(path: string, body: unknown) =>
    request<T>(
      path,
      { method: "POST", body: JSON.stringify(body) },
      { credential: true },
    ),
  delete: <T>(path: string) => request<T>(path, { method: "DELETE" }),
  /** Binary fetch (card artwork). The member token rides an Authorization
   * header, so images can't be loaded via a plain <img src> — callers turn
   * the blob into an object URL. */
  blob: async (path: string): Promise<Blob> => {
    const res = await fetch(`${API_BASE}${path}`, { headers: authHeader() });
    if (res.status === 401) return handleUnauthorized();
    if (!res.ok) {
      throw new Error(parseErrorText(await res.text(), res.statusText));
    }
    return await res.blob();
  },
  /** Multipart upload — no Content-Type so the browser sets the boundary. */
  upload: async <T>(path: string, formData: FormData): Promise<T> => {
    const res = await fetch(`${API_BASE}${path}`, {
      method: "POST",
      body: formData,
      headers: authHeader(),
    });
    if (res.status === 401) return handleUnauthorized();
    if (!res.ok) {
      throw new Error(parseErrorText(await res.text(), res.statusText));
    }
    return (await res.json()) as T;
  },
  /** Unauthenticated call for the OTP flow — a 401 here is a wrong/expired
   * code the sign-in form handles inline, not a session expiry. */
  postPublic: async <T>(path: string, body: unknown): Promise<T> => {
    const res = await fetch(`${API_BASE}${path}`, {
      method: "POST",
      headers: { "Content-Type": "application/json", ...tenantHeader() },
      body: JSON.stringify(body),
    });
    if (!res.ok) {
      throw errorFromText(res.status, await res.text(), res.statusText);
    }
    return (await res.json()) as T;
  },
};
