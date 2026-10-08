import {
  acquireAccessToken,
  getActiveAccount,
  clearLocalSession,
} from "@/auth/msal";
import { brokerAuthEnabled } from "@/auth/staffSignIn";
import { ApiError, errorFromText, parseErrorText, uploadRefusal } from "@/lib/errors";
import { toast } from "sonner";
import { useSession } from "@/stores/session";
import {
  CLIENT_SELECTION_CODES,
  recoverClientSelection,
} from "@/stores/clientSelection";

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

/** Read synchronously when a call STARTS, before any await. A token refresh can
 * take a while, and a company switch during it must not retarget a write the
 * user made for the previous company. */
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

/** 409 `client_selection_stale` / `client_selection_required`: the write was
 * refused because of the company selection. By the time this is thrown the
 * selection is cleared and the shell is asking for a new one, so the global
 * error reporter stays quiet; nothing retries the write. */
export class ClientSelectionError extends ApiError {
  constructor(message: string, code: string) {
    super(message, 409, code);
    this.name = "ClientSelectionError";
  }
}

const PLATFORM_READ_ONLY_MESSAGE =
  "Your access to this broker's data is read-only, so that change was not saved. Start write access under Platform → Access to make changes.";

/** 403 `platform_access_read_only`: a platform admin holding a read-only grant
 * tried to change another broker's data. The refusal is announced once, here,
 * as a toast; like a selection refusal it is never retried or re-reported. */
export class PlatformReadOnlyError extends ApiError {
  constructor(message: string) {
    super(message, 403, "platform_access_read_only");
    this.name = "PlatformReadOnlyError";
  }
}

/** A FastAPI `detail` carrying a machine-readable `code` (or the same
 * `{code, message}` sent as the whole body), or null for any other body (plain
 * text, a string detail, a gateway page). */
function codedDetail(text: string): { code: string; message: string | null } | null {
  try {
    const body = JSON.parse(text) as { detail?: unknown } | null;
    const detail = body && typeof body === "object" && "detail" in body ? body.detail : body;
    if (!detail || typeof detail !== "object") return null;
    const { code, message } = detail as { code?: unknown; message?: unknown };
    if (typeof code !== "string") return null;
    return { code, message: typeof message === "string" && message ? message : null };
  } catch {
    return null;
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

function uploadError(text: string, res: Response): Error {
  const refusal = uploadRefusal(res.status, text, res.headers.get("Retry-After"));
  if (refusal) return refusal;
  try {
    const detail = (JSON.parse(text) as { detail?: unknown }).detail;
    if (
      res.status === 409 &&
      detail &&
      typeof detail === "object" &&
      (detail as { code?: unknown }).code === "period_mismatch"
    ) {
      return new PeriodMismatchError(detail as PeriodMismatchDetail);
    }
  } catch {
    // not JSON — errorFromText keeps the raw text
  }
  // Keeps the status and any coded `detail` ({code, message}) so callers can
  // branch on errorCode(), and never renders an object detail as "[object Object]".
  return errorFromText(res.status, text, res.statusText);
}

async function authHeader(): Promise<Record<string, string>> {
  if (!brokerAuthEnabled()) return {};
  const account = getActiveAccount();
  if (!account) return {};
  const token = await acquireAccessToken(account);
  return token ? { Authorization: `Bearer ${token}` } : {};
}

async function handleUnauthorized(): Promise<never> {
  // End the local session after a rejected token. The user starts sign-in
  // explicitly, so Microsoft SSO cannot silently undo platform idle expiry.
  const authEnabled = brokerAuthEnabled();
  if (authEnabled) {
    await clearLocalSession();
    window.location.assign("/sign-in");
  }
  throw new UnauthorizedError(
    authEnabled
      ? "Session expired — redirecting to sign-in"
      : "Authentication required",
  );
}

/**
 * Single exit for every non-OK response. 401 → sign-in redirect; an
 * identity-level 403 → `NoAccessError` (the app bounces to the refused
 * sign-in page and suppresses the notification); a company-selection 409 →
 * the selection is reset and `ClientSelectionError`; a read-only platform
 * grant's 403 → one toast and `PlatformReadOnlyError`; anything else → the
 * caller's error shape. `read` marks a GET/HEAD: the server refuses a read for
 * its company selection only when a platform admin has no company at all to
 * fall back on — see `recoverClientSelection`.
 * Always throws.
 */
async function fail(
  res: Response,
  toError: (text: string) => Error = (text) =>
    errorFromText(res.status, text, res.statusText),
  read = true,
): Promise<never> {
  if (res.status === 401) return handleUnauthorized();
  const text = await res.text();
  const coded = codedDetail(text);
  if (res.status === 403 && coded?.code === "broker_mfa_required") {
    window.location.assign("/broker/security");
    throw new UnauthorizedError("Two-factor verification required");
  }
  if (res.status === 403 && coded && NO_ACCESS_CODES.has(coded.code)) {
    throw new NoAccessError(
      coded.message ?? "User has no access — contact your administrator.",
      coded.code,
    );
  }
  if (res.status === 403 && coded?.code === "platform_access_read_only") {
    const message = coded.message ?? PLATFORM_READ_ONLY_MESSAGE;
    // One toast however many in-flight writes were refused together.
    toast.error(message, { id: "platform-access-read-only" });
    throw new PlatformReadOnlyError(message);
  }
  if (res.status === 409 && coded && CLIENT_SELECTION_CODES.has(coded.code)) {
    recoverClientSelection(coded.code, { read });
    throw new ClientSelectionError(
      coded.message ?? "Choose a company to continue.",
      coded.code,
    );
  }
  throw toError(text);
}

async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  const scope = scopeHeaders();
  const auth = await authHeader();
  const res = await fetch(`${API_BASE}${path}`, {
    ...init,
    headers: {
      "Content-Type": "application/json",
      ...auth,
      ...scope,
      ...init.headers,
    },
  });
  if (!res.ok) {
    const method = (init.method ?? "GET").toUpperCase();
    return fail(res, undefined, method === "GET" || method === "HEAD");
  }
  if (res.status === 204) return undefined as T;
  return (await res.json()) as T;
}

export const api = {
  get: <T>(path: string, init: RequestInit = {}) => request<T>(path, init),
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
  put: <T>(path: string, body: unknown, init: RequestInit = {}) =>
    request<T>(path, { ...init, method: "PUT", body: JSON.stringify(body) }),
  delete: <T>(path: string, init: RequestInit = {}) => request<T>(path, { ...init, method: "DELETE" }),
  /** Fetch a binary response (e.g. an .xlsx export) as a Blob. */
  download: async (path: string, headers: Record<string, string> = {}): Promise<Blob> => {
    const scope = scopeHeaders();
    const auth = await authHeader();
    const res = await fetch(`${API_BASE}${path}`, {
      headers: { ...auth, ...scope, ...headers },
    });
    if (!res.ok) {
      return fail(res, (text) => new Error(parseErrorText(text, res.statusText)));
    }
    return await res.blob();
  },
  /** Like `download`, but returns the raw Response so callers can read headers. */
  downloadResponse: async (path: string): Promise<Response> => {
    const scope = scopeHeaders();
    const auth = await authHeader();
    const res = await fetch(`${API_BASE}${path}`, {
      headers: { ...auth, ...scope },
    });
    if (!res.ok) {
      return fail(res, (text) => new Error(parseErrorText(text, res.statusText)));
    }
    return res;
  },
  upload: async <T>(path: string, formData: FormData, headers: Record<string, string> = {}): Promise<T> => {
    const scope = scopeHeaders();
    const auth = await authHeader();
    const res = await fetch(`${API_BASE}${path}`, {
      method: "POST",
      body: formData,
      headers: { ...auth, ...scope, ...headers },
    });
    if (!res.ok) {
      return fail(res, (text) => uploadError(text, res), false);
    }
    return (await res.json()) as T;
  },
};
