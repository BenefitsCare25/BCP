/** Fetch wrapper for the employee portal — separate from the broker `api`.
 *
 * Attaches the member bearer token from the portal session store (never MSAL,
 * never `X-Inspro-Client` — a member is pinned to one client server-side).
 * A 401 clears the session and sends the member back to the portal sign-in.
 */
import { errorFromText, parseErrorText } from "@/lib/errors";
import { usePortalSession } from "@/stores/portalSession";

const API_BASE = import.meta.env.VITE_API_BASE_URL ?? "/api/v1";

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
  if (window.location.pathname !== "/portal/sign-in") {
    window.location.assign("/portal/sign-in");
  }
  throw new PortalUnauthorizedError();
}

async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  const res = await fetch(`${API_BASE}${path}`, {
    ...init,
    headers: {
      "Content-Type": "application/json",
      ...authHeader(),
      ...init.headers,
    },
  });
  if (res.status === 401) return handleUnauthorized();
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
  delete: <T>(path: string) => request<T>(path, { method: "DELETE" }),
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
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    if (!res.ok) {
      throw new Error(parseErrorText(await res.text(), res.statusText));
    }
    return (await res.json()) as T;
  },
};
