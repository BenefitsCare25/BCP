import {
  ENTRA_ENABLED,
  acquireAccessToken,
  getActiveAccount,
  getMsal,
  signIn,
} from "@/auth/msal";
import { errorFromText, parseErrorText } from "@/lib/errors";
import { useSession } from "@/stores/session";

// Re-exported for existing imports; the classes live in lib/errors so the
// portal fetch wrapper can throw them too.
export { ConflictDetailError, type ConflictDetail } from "@/lib/errors";

/** Active-client (tenant) header, read from the session store at call time. */
function tenantHeader(): Record<string, string> {
  const clientId = useSession.getState().activeClientId;
  return clientId ? { "X-Inspro-Client": clientId } : {};
}

/** Selected benefit year, used to reject stale detail requests server-side. */
function policyYearHeader(): Record<string, string> {
  const policyYearId = useSession.getState().currentPolicyYearId;
  return policyYearId ? { "X-Inspro-Policy-Year-ID": policyYearId } : {};
}

function scopeHeaders(): Record<string, string> {
  return { ...tenantHeader(), ...policyYearHeader() };
}

// Read the base from the build env; default keeps local dev (Vite proxy) working.
const API_BASE = import.meta.env.VITE_API_BASE_URL ?? "/api/v1";

export class UnauthorizedError extends Error {
  constructor(message = "Unauthorized") {
    super(message);
    this.name = "UnauthorizedError";
  }
}

export const SIGN_IN_PATH = "/sign-in";

/** Search flag that makes the sign-in page explain why the user is back on it.
 * Without it a refused account is silently dumped at the login screen right
 * after a successful Microsoft sign-in, and just tries again.
 * Boolean, not "1": the router JSON-encodes search values, so a string would
 * reach the address bar as the noisy `?denied=%221%22`. */
export const DENIED_SEARCH = { denied: true } as const;

/** True when the current URL is already the "access refused" sign-in page —
 * the guard against redirecting to where we already are. */
export function isDeniedSignInUrl(): boolean {
  return (
    window.location.pathname === SIGN_IN_PATH &&
    new URLSearchParams(window.location.search).has("denied")
  );
}

/** Identity-level 403 codes: the caller authenticated with Microsoft but the
 * platform grants them nothing. Distinct from a permission 403 on a single
 * endpoint (e.g. "Only admins can edit global defaults"), which must NOT end
 * the session — hence the code check rather than a bare status check. */
const NO_ACCESS_CODES = new Set(["no_access", "invitation_expired"]);

export class NoAccessError extends Error {
  code: string;
  constructor(message: string, code: string) {
    super(message);
    this.name = "NoAccessError";
    this.code = code;
  }
}

/** The coded detail of an identity-level 403, or null for any other body. */
function noAccessDetail(text: string): { code: string; message: string } | null {
  try {
    const detail = (JSON.parse(text) as { detail?: unknown }).detail;
    if (!detail || typeof detail !== "object") return null;
    const { code, message } = detail as { code?: unknown; message?: unknown };
    if (typeof code !== "string" || !NO_ACCESS_CODES.has(code)) return null;
    return {
      code,
      message:
        typeof message === "string" && message
          ? message
          : "User has no access — contact your administrator.",
    };
  } catch {
    return null; // not JSON — an ordinary 403
  }
}

export interface PeriodMismatchDetail {
  code: "period_mismatch";
  detected_period: string | null;
  slip_start: string;
  slip_end: string;
  policy_year_start: string;
  policy_year_end: string;
  matching_policy_year_id: string | null;
}

/** 409 from slip upload — the slip's period of insurance differs from the
 * target policy year. Carries the structured detail so the UI can offer a
 * switch/acknowledge choice instead of a flat error toast. */
export class PeriodMismatchError extends Error {
  detail: PeriodMismatchDetail;
  constructor(detail: PeriodMismatchDetail) {
    super("Placement slip period doesn't match the selected policy year");
    this.name = "PeriodMismatchError";
    this.detail = detail;
  }
}

function uploadError(text: string, statusText: string, status: number): Error {
  try {
    const detail = (JSON.parse(text) as { detail?: unknown }).detail;
    if (
      status === 409 &&
      detail &&
      typeof detail === "object" &&
      (detail as { code?: unknown }).code === "period_mismatch"
    ) {
      return new PeriodMismatchError(detail as PeriodMismatchDetail);
    }
    if (detail !== undefined) return new Error(String(detail));
  } catch {
    // not JSON — fall through to raw text
  }
  return new Error(text || statusText);
}

async function authHeader(): Promise<Record<string, string>> {
  if (!ENTRA_ENABLED) return {};
  const msal = getMsal();
  if (!msal) return {};
  const account = getActiveAccount();
  if (!account) return {};
  const token = await acquireAccessToken(account);
  return token ? { Authorization: `Bearer ${token}` } : {};
}

async function handleUnauthorized(): Promise<never> {
  // If Entra is wired, a 401 means our token has been rejected — kick the
  // user back through sign-in. The promise from `signIn` does not resolve
  // (the page navigates) but we still throw so callers don't proceed.
  if (ENTRA_ENABLED) {
    await signIn();
  }
  throw new UnauthorizedError(
    ENTRA_ENABLED
      ? "Session expired — redirecting to sign-in"
      : "Authentication required",
  );
}

/**
 * Single exit for every non-OK response. 401 → sign-in redirect; an
 * identity-level 403 → `NoAccessError` (the app bounces to the refused
 * sign-in page and suppresses the notification); anything else → the caller's
 * error shape.
 * Always throws.
 */
async function fail(
  res: Response,
  toError: (text: string) => Error = (text) =>
    errorFromText(res.status, text, res.statusText),
): Promise<never> {
  if (res.status === 401) return handleUnauthorized();
  const text = await res.text();
  if (res.status === 403) {
    const denied = noAccessDetail(text);
    if (denied) throw new NoAccessError(denied.message, denied.code);
  }
  throw toError(text);
}

async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  const auth = await authHeader();
  const res = await fetch(`${API_BASE}${path}`, {
    ...init,
    headers: {
      "Content-Type": "application/json",
      ...auth,
      ...scopeHeaders(),
      ...init.headers,
    },
  });
  if (!res.ok) {
    return fail(res);
  }
  if (res.status === 204) return undefined as T;
  return (await res.json()) as T;
}

export const api = {
  get: <T>(path: string) => request<T>(path),
  post: <T>(path: string, body: unknown, init: RequestInit = {}) =>
    request<T>(path, {
      ...init,
      method: "POST",
      body: JSON.stringify(body),
    }),
  patch: <T>(path: string, body: unknown, init: RequestInit = {}) =>
    request<T>(path, {
      ...init,
      method: "PATCH",
      body: JSON.stringify(body),
    }),
  put: <T>(path: string, body: unknown) =>
    request<T>(path, { method: "PUT", body: JSON.stringify(body) }),
  delete: <T>(path: string) => request<T>(path, { method: "DELETE" }),
  /** Fetch a binary response (e.g. an .xlsx export) as a Blob. */
  download: async (path: string): Promise<Blob> => {
    const auth = await authHeader();
    const res = await fetch(`${API_BASE}${path}`, {
      headers: { ...auth, ...scopeHeaders() },
    });
    if (!res.ok) {
      return fail(res, (text) => new Error(parseErrorText(text, res.statusText)));
    }
    return await res.blob();
  },
  /** Like `download`, but returns the raw Response so callers can read headers. */
  downloadResponse: async (path: string): Promise<Response> => {
    const auth = await authHeader();
    const res = await fetch(`${API_BASE}${path}`, {
      headers: { ...auth, ...scopeHeaders() },
    });
    if (!res.ok) {
      return fail(res, (text) => new Error(parseErrorText(text, res.statusText)));
    }
    return res;
  },
  upload: async <T>(path: string, formData: FormData): Promise<T> => {
    const auth = await authHeader();
    const res = await fetch(`${API_BASE}${path}`, {
      method: "POST",
      body: formData,
      headers: { ...auth, ...scopeHeaders() },
    });
    if (!res.ok) {
      return fail(res, (text) => uploadError(text, res.statusText, res.status));
    }
    return (await res.json()) as T;
  },
};
