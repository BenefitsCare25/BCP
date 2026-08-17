/** Fetch wrapper for the employee portal — separate from the broker `api`.
 *
 * Attaches the member bearer token from the portal session store (never MSAL,
 * never `X-Inspro-Client` — a member is pinned to one client server-side).
 * A 401 clears the session and sends the member back to the portal sign-in.
 */
import { errorFromText } from "@/lib/errors";
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

/** Their access has ENDED — not a permission slip they can work around, so the
 * session goes the way it does on a 401. Distinguished from the other portal
 * 403 (`coverage_ended`, an ordinary refusal a signed-in member reads and works
 * around): ending the session on every capability refusal would sign a member
 * out for tapping the panel-card tab.
 *
 * Returns the server's own sentence, which carries the DATE their access ended
 * — the one fact a member needs and cannot look up. `""` when the code matches
 * but no message came with it; `null` when this is some other 403. */
function accessEndedMessage(text: string): string | null {
  try {
    const detail = (JSON.parse(text) as { detail?: unknown }).detail;
    if (!detail || typeof detail !== "object") return null;
    const { code, message } = detail as { code?: unknown; message?: unknown };
    if (code !== "access_ended") return null;
    return typeof message === "string" ? message : "";
  } catch {
    return null;
  }
}

/** Where the refusal's sentence waits out the full page load below.
 *
 * `sessionStorage`, because the redirect is a `window.location.assign` and
 * nothing in memory survives it. Not the query string: this is a whole sentence
 * naming a date, and a URL is a bad place to put prose the member will read. */
const ENDED_MESSAGE_KEY = "inspro.portal.access-ended-message";

/** Read once per page load, so a second call in the same load still answers.
 *  `undefined` = not yet read; `null` = read, and there was nothing. */
let consumed: string | null | undefined;

/** The refusal the member was last redirected on. Clears the store, but keeps
 *  answering for the rest of THIS page load.
 *
 *  Not a bare read-and-delete: the sign-in page takes this in a `useState`
 *  initialiser, and StrictMode invokes those twice in development — a strict
 *  one-shot handed the second call `null`, so the whole point of this (the
 *  server's dated sentence) showed up only in production builds. */
export function takeAccessEndedMessage(): string | null {
  if (consumed !== undefined) return consumed;
  try {
    consumed = sessionStorage.getItem(ENDED_MESSAGE_KEY);
    sessionStorage.removeItem(ENDED_MESSAGE_KEY);
  } catch {
    consumed = null; // storage blocked (private mode) — generic line still shows
  }
  return consumed || null;
}

function handleAccessEnded(message: string): never {
  usePortalSession.getState().clearSession();
  // Carried across the reload so the sign-in page can say "your access ended on
  // 30 June" rather than the undated line it used to hardcode — the server has
  // already worded this, including the date, and discarding it made the member
  // ask their HR team a question the screen could have answered.
  if (message) {
    try {
      sessionStorage.setItem(ENDED_MESSAGE_KEY, message);
      consumed = undefined; // a new refusal supersedes anything already read
    } catch {
      /* storage blocked — fall back to the generic line */
    }
  }
  // `?ended` so the sign-in page can say why they are back here instead of
  // showing an empty form that will refuse them again. Appended to a raw URL
  // rather than routed through `navigate({search})` — the router JSON-encodes
  // search values, which is how a `"1"` reaches the address bar as `%221%22`.
  // The company segment is NOT optional: landing on the pathless sign-in turns
  // the company field back on and sends an EMPTY tenant header.
  const target = portalPath(currentPortalTenantSlug(), "/sign-in");
  window.location.assign(`${target}?ended`);
  throw new PortalUnauthorizedError("Portal access has ended");
}

/** **The ONE place a failed portal response becomes an error.**
 *
 * Every fetch path here — JSON, blob, upload — has to end a dead session on a
 * 401 and end it again on an `access_ended` 403, and the three had grown three
 * copies of that decision. They had already drifted: `blob` and `upload` threw
 * a bare `Error`, losing the typed errors (`ConflictDetailError` and friends)
 * that pages branch on, so the same backend refusal read differently depending
 * on which helper happened to fetch it.
 */
async function failed(
  res: Response,
  opts: { credential?: boolean } = {},
): Promise<never> {
  // A `credential` call is one whose BODY carried a value the member just typed,
  // where a 401 means "that value is wrong" and must reach the form.
  if (res.status === 401 && !opts.credential) return handleUnauthorized();
  const text = await res.text();
  if (res.status === 403) {
    const ended = accessEndedMessage(text);
    if (ended !== null) return handleAccessEnded(ended);
  }
  throw errorFromText(res.status, text, res.statusText);
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
  // Coded 409s (e.g. unpriced_elections / flex_overdrawn on enrollment submit)
  // surface as ConflictDetailError so pages can offer a choice — see `failed`.
  if (!res.ok) return failed(res, opts);
  if (res.status === 204) return undefined as T;
  return (await res.json()) as T;
}

export const portalApi = {
  get: <T>(path: string) => request<T>(path),
  post: <T>(path: string, body: unknown) =>
    request<T>(path, { method: "POST", body: JSON.stringify(body) }),
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
    const res = await fetch(`${API_BASE}${path}`, {
      headers: { ...tenantHeader(), ...authHeader() },
    });
    if (!res.ok) return failed(res);
    return await res.blob();
  },
  /** Multipart upload — no Content-Type so the browser sets the boundary. */
  upload: async <T>(path: string, formData: FormData): Promise<T> => {
    const res = await fetch(`${API_BASE}${path}`, {
      method: "POST",
      body: formData,
      headers: { ...tenantHeader(), ...authHeader() },
    });
    if (!res.ok) return failed(res);
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
