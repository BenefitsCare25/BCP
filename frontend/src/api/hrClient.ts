/** Fetch wrapper for the HR credential surface — separate from broker `api`
 * and `portalApi`.
 *
 * - Sends the short-lived HR access token as a bearer header.
 * - Sends `X-Inspro-Tenant-Slug` so the surface is usable on localhost (the
 *   backend ignores it in production, where the subdomain is authoritative).
 * - `credentials: "include"` so the host-only refresh cookie rides along.
 * - On 401, transparently tries ONE silent refresh (the whole point of a short
 *   access token); if that fails, clears the session and returns to sign-in.
 */
import { errorFromText } from "@/lib/errors";
import { currentHrTenantSlug } from "@/lib/tenant";
import { useHrSession } from "@/stores/hrSession";

const API_BASE = import.meta.env.VITE_API_BASE_URL ?? "/api/v1";

export class HrUnauthorizedError extends Error {
  constructor(message = "HR session expired") {
    super(message);
    this.name = "HrUnauthorizedError";
  }
}

function tenantHeader(): Record<string, string> {
  return { "X-Inspro-Tenant-Slug": currentHrTenantSlug() };
}

function authHeader(): Record<string, string> {
  const token = useHrSession.getState().token;
  return token ? { Authorization: `Bearer ${token}` } : {};
}

function handleUnauthorized(): never {
  useHrSession.getState().clearSession();
  if (window.location.pathname !== "/hr/sign-in") {
    window.location.assign("/hr/sign-in");
  }
  throw new HrUnauthorizedError();
}

let refreshInFlight: Promise<boolean> | null = null;

/** Attempt one silent HR access-token refresh against the rotating refresh
 * cookie. Exported so the router guard can refresh on navigation (not just the
 * API layer on a 401), otherwise an expired 10-min access token bounces the
 * user to sign-in despite a valid 12h session. Concurrent callers de-dupe onto
 * a single in-flight request. */
export async function refreshHrSession(): Promise<boolean> {
  if (!refreshInFlight) {
    refreshInFlight = (async () => {
      try {
        const res = await fetch(`${API_BASE}/hr/auth/refresh`, {
          method: "POST",
          credentials: "include",
          headers: tenantHeader(),
        });
        if (!res.ok) return false;
        const data = (await res.json()) as {
          access_token: string;
          expires_at: string;
          me: import("@/stores/hrSession").HrMe;
        };
        useHrSession.getState().setSession(data.access_token, data.expires_at, data.me);
        return true;
      } catch {
        return false;
      } finally {
        refreshInFlight = null;
      }
    })();
  }
  return refreshInFlight;
}

async function request<T>(
  path: string,
  init: RequestInit = {},
  retried = false,
): Promise<T> {
  const res = await fetch(`${API_BASE}${path}`, {
    ...init,
    credentials: "include",
    headers: {
      "Content-Type": "application/json",
      ...tenantHeader(),
      ...authHeader(),
      ...init.headers,
    },
  });
  if (res.status === 401) {
    // Never auto-refresh the auth endpoints themselves — a 401 there is an
    // inline credential error, not a session expiry.
    if (!retried && !path.startsWith("/hr/auth/")) {
      if (await refreshHrSession()) return request<T>(path, init, true);
    }
    return handleUnauthorized();
  }
  if (!res.ok) {
    throw errorFromText(res.status, await res.text(), res.statusText);
  }
  if (res.status === 204) return undefined as T;
  return (await res.json()) as T;
}

export const hrApi = {
  get: <T>(path: string) => request<T>(path),
  post: <T>(path: string, body: unknown) =>
    request<T>(path, { method: "POST", body: JSON.stringify(body) }),
  put: <T>(path: string, body: unknown) =>
    request<T>(path, { method: "PUT", body: JSON.stringify(body) }),
  /** Public auth call (login / mfa / set-password): a 401/4xx is surfaced to
   * the form inline — no refresh, no redirect. Cookie still included so the
   * server can set the rotating refresh token. */
  postPublic: async <T>(path: string, body: unknown): Promise<T> => {
    const res = await fetch(`${API_BASE}${path}`, {
      method: "POST",
      credentials: "include",
      headers: { "Content-Type": "application/json", ...tenantHeader() },
      body: JSON.stringify(body),
    });
    if (!res.ok) {
      throw errorFromText(res.status, await res.text(), res.statusText);
    }
    return (await res.json()) as T;
  },
  logout: async (): Promise<void> => {
    try {
      await fetch(`${API_BASE}/hr/auth/logout`, {
        method: "POST",
        credentials: "include",
        headers: tenantHeader(),
      });
    } catch {
      // Best-effort; the local session is cleared regardless.
    }
  },
};
