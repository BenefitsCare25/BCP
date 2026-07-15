import {
  ENTRA_ENABLED,
  acquireAccessToken,
  getActiveAccount,
  getMsal,
  signIn,
} from "@/auth/msal";
import { errorFromText, parseResponseError } from "@/lib/errors";
import { useSession } from "@/stores/session";

// Re-exported for existing imports; the classes live in lib/errors so the
// portal fetch wrapper can throw them too.
export { ConflictDetailError, type ConflictDetail } from "@/lib/errors";

/** Active-client (tenant) header, read from the session store at call time. */
function tenantHeader(): Record<string, string> {
  const clientId = useSession.getState().activeClientId;
  return clientId ? { "X-Inspro-Client": clientId } : {};
}

// Read the base from the build env; default keeps local dev (Vite proxy) working.
const API_BASE = import.meta.env.VITE_API_BASE_URL ?? "/api/v1";

export class UnauthorizedError extends Error {
  constructor(message = "Unauthorized") {
    super(message);
    this.name = "UnauthorizedError";
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

async function uploadError(res: Response): Promise<Error> {
  const text = await res.text();
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
    if (detail !== undefined) return new Error(String(detail));
  } catch {
    // not JSON — fall through to raw text
  }
  return new Error(text || res.statusText);
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

async function responseError(res: Response): Promise<Error> {
  return errorFromText(res.status, await res.text(), res.statusText);
}

async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  const auth = await authHeader();
  const res = await fetch(`${API_BASE}${path}`, {
    ...init,
    headers: {
      "Content-Type": "application/json",
      ...auth,
      ...tenantHeader(),
      ...init.headers,
    },
  });
  if (res.status === 401) {
    return handleUnauthorized();
  }
  if (!res.ok) {
    throw await responseError(res);
  }
  if (res.status === 204) return undefined as T;
  return (await res.json()) as T;
}

export const api = {
  get: <T>(path: string) => request<T>(path),
  post: <T>(path: string, body: unknown) =>
    request<T>(path, { method: "POST", body: JSON.stringify(body) }),
  patch: <T>(path: string, body: unknown) =>
    request<T>(path, { method: "PATCH", body: JSON.stringify(body) }),
  put: <T>(path: string, body: unknown) =>
    request<T>(path, { method: "PUT", body: JSON.stringify(body) }),
  delete: <T>(path: string) => request<T>(path, { method: "DELETE" }),
  /** Fetch a binary response (e.g. an .xlsx export) as a Blob. */
  download: async (path: string): Promise<Blob> => {
    const auth = await authHeader();
    const res = await fetch(`${API_BASE}${path}`, {
      headers: { ...auth, ...tenantHeader() },
    });
    if (res.status === 401) {
      return handleUnauthorized();
    }
    if (!res.ok) {
      throw new Error(await parseResponseError(res));
    }
    return await res.blob();
  },
  /** Like `download`, but returns the raw Response so callers can read headers. */
  downloadResponse: async (path: string): Promise<Response> => {
    const auth = await authHeader();
    const res = await fetch(`${API_BASE}${path}`, {
      headers: { ...auth, ...tenantHeader() },
    });
    if (res.status === 401) {
      return handleUnauthorized();
    }
    if (!res.ok) {
      throw new Error(await parseResponseError(res));
    }
    return res;
  },
  upload: async <T>(path: string, formData: FormData): Promise<T> => {
    const auth = await authHeader();
    const res = await fetch(`${API_BASE}${path}`, {
      method: "POST",
      body: formData,
      headers: { ...auth, ...tenantHeader() },
    });
    if (res.status === 401) {
      return handleUnauthorized();
    }
    if (!res.ok) {
      throw await uploadError(res);
    }
    return (await res.json()) as T;
  },
};
