/** Fetch wrapper for the employee portal — separate from the broker `api`.
 *
 * Attaches the member bearer token from the portal session store (never MSAL,
 * never `X-Inspro-Client` — a member is pinned to one client server-side).
 * A 401 clears the session and sends the member back to the portal sign-in.
 */
import { errorCode, errorFromText, uploadRefusal } from "@/lib/errors";
import { withSessionRefreshLock } from "@/lib/sessionRefresh";
import {
  isSignedOut,
  rememberSessionEndNotice,
  sessionEndingRefusal,
  sessionEndQuery,
  type SessionEndingRefusal,
} from "@/lib/surfaceSession";
import { currentPortalTenantSlug, portalPath } from "@/lib/tenant";
import { usePortalSession } from "@/stores/portalSession";
import { useNotifications } from "@/stores/notifications";
import { claimPeriodHeaders } from "@/lib/claimPeriod";
import { queryClient } from "@/lib/queryClient";

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

/** Refusals that END the session — not a permission slip a member can work
 * around, so the session goes the way it does on a 401:
 *
 * - `access_ended`: their own access ended (they left, and the run-off is over).
 * - `portal_disabled`: the company's employee portal was switched off.
 *
 * Distinguished from the other portal 403 (`coverage_ended`, an ordinary
 * refusal a signed-in member reads and works around): ending the session on
 * every capability refusal would sign a member out for tapping the panel-card
 * tab. */
const SESSION_ENDING_CODES: ReadonlySet<string> = new Set([
  "access_ended",
  "portal_disabled",
]);

/** Forget this tab's member identity: token, cached portal data and the alerts
 *  raised under it. Server-side revocation is the caller's business. */
export function clearLocalPortalSession(): void {
  usePortalSession.getState().clearSession();
  void queryClient.cancelQueries({ queryKey: ["portal"] });
  queryClient.removeQueries({ queryKey: ["portal"] });
  useNotifications.getState().clear();
}

/** The sign-in address a session-ending refusal already sent this tab to. The
 *  401 whose refresh met that refusal is handled a moment later, and the bare
 *  sign-in page it used to assign replaced the navigation — dropping the
 *  `?ended` reason, the only explanation left when tab storage is blocked. */
let sessionEndTarget: string | null = null;

/** For the route guard, whose own redirect after a failed refresh would
 *  otherwise race the same navigation. */
export function portalSessionEndTarget(): string | null {
  return sessionEndTarget;
}

/** End the session on a refusal and tell the sign-in page why.
 *
 * The server's own sentence waits out the full page load in tab storage — it
 * carries the DATE their access ended, the one fact a member needs and cannot
 * look up. The `?ended` flag (see `sessionEndQuery`) carries a generic line
 * when storage is blocked. The company segment is NOT optional: landing on the
 * pathless sign-in turns the company field back on and sends an EMPTY tenant
 * header.
 *
 * No redirect when sign-in is already showing: its own guard refreshes, and a
 * refusal there must not reload the page forever. */
function endRefusedSession(refusal: SessionEndingRefusal): void {
  const reason = refusal.code === "portal_disabled" ? "disabled" : "ended";
  clearLocalPortalSession();
  rememberSessionEndNotice("portal", reason, refusal.message);
  const target = portalPath(currentPortalTenantSlug(), "/sign-in");
  const path = window.location.pathname;
  if (path === target || path === "/portal/sign-in") return;
  sessionEndTarget = `${target}?${sessionEndQuery(reason)}`;
  window.location.assign(sessionEndTarget);
}

function handleSessionRefused(refusal: SessionEndingRefusal): never {
  endRefusedSession(refusal);
  throw new PortalUnauthorizedError(
    refusal.code === "portal_disabled"
      ? "The employee portal is switched off"
      : "Portal access has ended",
  );
}

/** **The ONE place a failed portal response becomes an error.**
 *
 * Every fetch path here — JSON, blob, upload — has to end a dead session on a
 * 401 and end it again on a session-ending 403, and the three had grown three
 * copies of that decision. They had already drifted: `blob` and `upload` threw
 * a bare `Error`, losing the typed errors (`ConflictDetailError` and friends)
 * that pages branch on, so the same backend refusal read differently depending
 * on which helper happened to fetch it.
 */
async function failed(
  res: Response,
  opts: { credential?: boolean; upload?: boolean } = {},
): Promise<never> {
  // A `credential` call is one whose BODY carried a value the member just typed,
  // where a 401 means "that value is wrong" and must reach the form.
  if (res.status === 401 && !opts.credential) return handleUnauthorized();
  const text = await res.text();
  if (res.status === 403) {
    const refusal = sessionEndingRefusal(text, SESSION_ENDING_CODES);
    if (refusal) return handleSessionRefused(refusal);
    if (errorCode(errorFromText(res.status, text, res.statusText)) === "mfa_enrollment_required") {
      usePortalSession.setState({ mfaEnrollmentRequired: true });
      const target = portalPath(currentPortalTenantSlug(), "/security");
      if (window.location.pathname !== target) window.location.assign(target);
    }
  }
  // A file too large to accept, or a busy malware scanner: their own sentence,
  // never the proxy's markup or a bare status.
  if (opts.upload) {
    const refusal = uploadRefusal(res.status, text, res.headers.get("Retry-After"));
    if (refusal) throw refusal;
  }
  throw errorFromText(res.status, text, res.statusText);
}

function handleUnauthorized(): never {
  clearLocalPortalSession();
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
    window.location.assign(sessionEndTarget ?? target);
  }
  throw new PortalUnauthorizedError();
}

let refreshInFlight: Promise<boolean> | null = null;

/** Never after an explicit sign-out in this tab: the refresh cookie may by then
 *  belong to whoever signed in at this company in another tab. */
export async function refreshPortalSession(passive = false): Promise<boolean> {
  if (isSignedOut("portal")) return false;
  if (!refreshInFlight) {
    const expectedMember = usePortalSession.getState().member?.id ?? null;
    refreshInFlight = withSessionRefreshLock("portal", currentPortalTenantSlug(), async () => {
      try {
        const res = await fetch(`${API_BASE}/portal/auth/refresh`, {
          method: "POST", credentials: "include", headers: { ...tenantHeader(), ...(passive ? { "X-Inspro-Session-Activity": "passive" } : {}) },
        });
        if (!res.ok) {
          if (res.status === 403) {
            const refusal = sessionEndingRefusal(await res.text(), SESSION_ENDING_CODES);
            if (refusal) endRefusedSession(refusal);
          }
          return false;
        }
        const data = await res.json() as import("@/api/portal").MemberTokenResult;
        const currentMember = usePortalSession.getState().member?.id ?? null;
        if (currentMember !== expectedMember || (expectedMember !== null && data.member.id !== expectedMember)) {
          // Do not adopt another tab's account or replay this tab's work.
          // Leave its valid refresh cookie alone; only clear this tab.
          return handleUnauthorized();
        }
        usePortalSession.getState().setSession(data.token, data.expires_at, data.member, data.mfa_enrollment_required);
        return true;
      } catch { return false; }
      finally { refreshInFlight = null; }
    });
  }
  return refreshInFlight;
}

async function authenticatedFetch(path: string, init: RequestInit = {}, credential = false): Promise<Response> {
  const send = () => fetch(`${API_BASE}${path}`, {
    ...init, credentials: "include",
    headers: { ...init.headers, ...tenantHeader(), ...authHeader(), ...claimPeriodHeaders(path) },
  });
  let res = await send();
  if (res.status === 401 && !credential) {
    if (await refreshPortalSession(new Headers(init.headers).get("X-Inspro-Session-Activity") === "passive")) res = await send();
  }
  return res;
}

async function request<T>(
  path: string,
  init: RequestInit = {},
  /** Set when the request BODY carries a credential the member just typed —
   * see `portalApi.verify`. */
  opts: { credential?: boolean } = {},
): Promise<T> {
  if (opts.credential && Date.parse(usePortalSession.getState().expiresAt ?? "") <= Date.now()) {
    if (!(await refreshPortalSession())) return handleUnauthorized();
  }
  const res = await authenticatedFetch(path, {
    ...init,
    headers: {
      "Content-Type": "application/json",
      ...tenantHeader(),
      ...authHeader(),
      ...claimPeriodHeaders(path),
      ...init.headers,
    },
  }, !!opts.credential);
  // Coded 409s (e.g. unpriced_elections / flex_overdrawn on enrollment submit)
  // surface as ConflictDetailError so pages can offer a choice — see `failed`.
  if (!res.ok) return failed(res, opts);
  if (res.status === 204) return undefined as T;
  return (await res.json()) as T;
}

export const portalApi = {
  passiveGet: <T>(path: string) => request<T>(path, { headers: { "X-Inspro-Session-Activity": "passive" } }),
  get: <T>(path: string) => request<T>(path),
  getWithTimeout: <T>(path: string, milliseconds: number) =>
    request<T>(path, { signal: AbortSignal.timeout(milliseconds) }),
  post: <T>(path: string, body: unknown) =>
    request<T>(path, { method: "POST", body: JSON.stringify(body) }),
  postWithHeaders: <T>(
    path: string,
    body: unknown,
    headers: Record<string, string>,
  ) => request<T>(path, { method: "POST", body: JSON.stringify(body), headers }),
  put: <T>(path: string, body: unknown) =>
    request<T>(path, { method: "PUT", body: JSON.stringify(body) }),
  /** A PARTIAL update — the body carries only what changed. Used by the claim
   * edit sheet, where sending the whole object would let one edited field blank
   * every other one the member never touched. */
  patch: <T>(path: string, body: unknown) =>
    request<T>(path, { method: "PATCH", body: JSON.stringify(body) }),
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
    const res = await authenticatedFetch(path, {
      headers: { ...tenantHeader(), ...authHeader(), ...claimPeriodHeaders(path) },
    });
    if (!res.ok) return failed(res);
    return await res.blob();
  },
  /** Multipart upload — no Content-Type so the browser sets the boundary. */
  upload: async <T>(path: string, formData: FormData): Promise<T> => {
    const res = await authenticatedFetch(path, {
      method: "POST",
      body: formData,
      headers: { ...tenantHeader(), ...authHeader(), ...claimPeriodHeaders(path) },
    });
    if (!res.ok) return failed(res, { upload: true });
    return (await res.json()) as T;
  },
  /** Unauthenticated call for the sign-in flow — a 401 here is a wrong
   * credential or code the sign-in form handles inline, not a session expiry. */
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
    const res = await fetch(`${API_BASE}/portal/auth/logout`, {
      method: "POST", credentials: "include", headers: { ...tenantHeader(), ...authHeader() },
    });
    if (res.ok) return;
    const text = await res.text();
    // A refusal that ends sessions (the portal switched off) means there is no
    // server session left to end; only this tab's local state remains.
    if (res.status === 403 && sessionEndingRefusal(text, SESSION_ENDING_CODES)) return;
    throw errorFromText(res.status, text, res.statusText);
  },
};
