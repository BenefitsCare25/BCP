import { ApiError } from "@/lib/errors";
import { useBrokerSession, type BrokerSession } from "@/stores/brokerSession";

const base = import.meta.env.VITE_API_BASE_URL ?? "/api/v1";
const DEFAULT_FAILURE = "Sign-in could not be completed. Try again.";
const signedOutKey = "inspro-broker-signed-out";
let refreshing: Promise<BrokerSession | null> | null = null;

// Every request that writes the refresh cookie, including sign-in, MFA and
// logout. Locking only refresh would let confirmation restore a spent cookie.
const COOKIE_WRITERS = new Set([
  "/exchange", "/login", "/login/mfa", "/accept-invite",
  "/refresh", "/logout", "/mfa/confirm", "/mfa/verify",
]);

export async function brokerAuthRequest<T>(path: string, body?: unknown, token?: string): Promise<T> {
  const send = () => sendBrokerAuthRequest<T>(path, body, token);
  // Serialize every cookie writer across tabs.
  if (navigator.locks && COOKIE_WRITERS.has(path)) {
    return await navigator.locks.request("inspro-refresh:broker", send);
  }
  return await send();
}

/** FastAPI's `detail` as a sentence: a string, `{code, message}`, or a 422
 *  list of validation items (pydantic's "Value error, " prefix removed). */
function failureOf(payload: unknown): { message: string; code?: string } {
  const detail = payload && typeof payload === "object" && "detail" in payload
    ? (payload as { detail: unknown }).detail
    : payload;
  if (typeof detail === "string" && detail.trim()) return { message: detail };
  if (Array.isArray(detail)) {
    const message = detail
      .map((item) => (item && typeof item === "object" ? (item as { msg?: unknown }).msg : null))
      .filter((msg): msg is string => typeof msg === "string" && msg.trim() !== "")
      .map((msg) => msg.replace(/^Value error,\s*/i, ""))
      .join(" ");
    return { message: message || DEFAULT_FAILURE };
  }
  if (detail && typeof detail === "object") {
    const { code, message } = detail as { code?: unknown; message?: unknown };
    return {
      message: typeof message === "string" && message.trim() ? message : DEFAULT_FAILURE,
      ...(typeof code === "string" ? { code } : {}),
    };
  }
  return { message: DEFAULT_FAILURE };
}

async function sendBrokerAuthRequest<T>(path: string, body?: unknown, token?: string): Promise<T> {
  const response = await fetch(`${base}/broker/auth${path}`, {
    method: body === undefined ? "GET" : "POST", credentials: "same-origin",
    headers: { "Content-Type": "application/json", ...(token ? { Authorization: `Bearer ${token}` } : {}) },
    ...(body === undefined ? {} : { body: JSON.stringify(body) }),
  });
  if (!response.ok) {
    const { message, code } = failureOf(await response.json().catch(() => null));
    throw new ApiError(message, response.status, code);
  }
  return response.status === 204 ? undefined as T : await response.json() as T;
}

// ── Signed-out suppression ───────────────────────────────────────────────────

/** An explicit sign-out stops startup from restoring another tab's session.
 *  The `?signed_out=1` URL also covers browsers that refuse storage writes. */
export function restorationSuppressed(): boolean {
  if (new URLSearchParams(window.location.search).get("signed_out") === "1") return true;
  try { return sessionStorage.getItem(signedOutKey) === "1"; } catch { return false; }
}

export function setRestorationSuppressed(suppressed: boolean): void {
  try {
    if (suppressed) sessionStorage.setItem(signedOutKey, "1");
    else sessionStorage.removeItem(signedOutKey);
  } catch { /* The sign-out URL retains this tab's explicit choice. */ }
}

/** Adopt a session from any sign-in method. */
export function acceptBrokerSession(session: BrokerSession): void {
  setRestorationSuppressed(false);
  useBrokerSession.getState().set(session);
}

/** A session that may reach only the two-factor endpoints until the account
 *  enrols or verifies an authenticator on `/broker/security`. */
export function needsTwoFactor(session: BrokerSession): boolean {
  return session.mfa_required && !session.mfa_verified;
}

// ── Sign-in calls ────────────────────────────────────────────────────────────

/** Password accepted; the account's authenticator code is still needed. */
export interface BrokerLoginChallenge {
  mfa_required: true;
  challenge_token: string;
}

export function isLoginChallenge(result: BrokerSession | BrokerLoginChallenge): result is BrokerLoginChallenge {
  return !("access_token" in result) && typeof (result as BrokerLoginChallenge).challenge_token === "string";
}

/** Microsoft first factor: the Entra access token, once, plus the invitation
 *  this tab is accepting, if any. */
export function exchangeMicrosoftToken(accessToken: string, inviteToken?: string | null): Promise<BrokerSession> {
  return brokerAuthRequest<BrokerSession>("/exchange", {
    access_token: accessToken,
    ...(inviteToken ? { invite_token: inviteToken } : {}),
  });
}

export function loginWithPassword(email: string, password: string): Promise<BrokerSession | BrokerLoginChallenge> {
  return brokerAuthRequest<BrokerSession | BrokerLoginChallenge>("/login", { email, password });
}

/** The second step of a password sign-in: a TOTP or a single-use recovery code. */
export function verifyLoginCode(challengeToken: string, code: string): Promise<BrokerSession> {
  return brokerAuthRequest<BrokerSession>("/login/mfa", { challenge_token: challengeToken, code });
}

/** Set the invited account's password. The session that comes back is limited
 *  to two-factor setup. */
export function acceptInviteWithPassword(body: {
  invite_token: string;
  password: string;
  display_name?: string;
}): Promise<BrokerSession> {
  return brokerAuthRequest<BrokerSession>("/accept-invite", body);
}

// ── Session lifetime ─────────────────────────────────────────────────────────

export function refreshBrokerSession(): Promise<BrokerSession | null> {
  if (!refreshing) {
    const expected = useBrokerSession.getState().session?.user.id;
    const refresh = async () => {
      try {
        const session = await brokerAuthRequest<BrokerSession>("/refresh", {});
        if (expected && session.user.id !== expected) {
          useBrokerSession.getState().set(null);
          return null; // Never replay this tab's requests as another account.
        }
        useBrokerSession.getState().set(session);
        return session;
      } catch (error) {
        if ((error as { status?: number }).status === 401 || (error as { status?: number }).status === 403) {
          useBrokerSession.getState().set(null);
          return null;
        }
        throw error;
      }
    };
    refreshing = refresh().finally(() => { refreshing = null; });
  }
  return refreshing!;
}

export async function brokerAccessToken(): Promise<string | null> {
  let session = useBrokerSession.getState().session;
  if (session && Date.parse(session.expires_at) > Date.now() + 30_000) return session.access_token;
  session = await refreshBrokerSession();
  return session?.access_token ?? null;
}
