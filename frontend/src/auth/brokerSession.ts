import { useBrokerSession, type BrokerSession } from "@/stores/brokerSession";

const base = import.meta.env.VITE_API_BASE_URL ?? "/api/v1";
let refreshing: Promise<BrokerSession | null> | null = null;

export async function brokerAuthRequest<T>(path: string, body?: unknown, token?: string): Promise<T> {
  const send = () => sendBrokerAuthRequest<T>(path, body, token);
  // Serialize every cookie writer across tabs, including MFA and logout.
  // Locking only refresh would let confirmation restore an already-spent cookie.
  if (navigator.locks && ["/exchange", "/refresh", "/logout", "/mfa/confirm", "/mfa/verify"].includes(path)) {
    return await navigator.locks.request("inspro-refresh:broker", send);
  }
  return await send();
}

async function sendBrokerAuthRequest<T>(path: string, body?: unknown, token?: string): Promise<T> {
  const response = await fetch(`${base}/broker/auth${path}`, {
    method: body === undefined ? "GET" : "POST", credentials: "same-origin",
    headers: { "Content-Type": "application/json", ...(token ? { Authorization: `Bearer ${token}` } : {}) },
    ...(body === undefined ? {} : { body: JSON.stringify(body) }),
  });
  if (!response.ok) {
    let message = "Sign-in could not be completed. Try again.";
    const error = await response.json().catch(() => ({})) as { detail?: string | { message?: string; code?: string } };
    if (typeof error.detail === "string") message = error.detail;
    else if (error.detail?.message) message = error.detail.message;
    throw Object.assign(new Error(message), { status: response.status,
      code: typeof error.detail === "object" ? error.detail.code : undefined });
  }
  return response.status === 204 ? undefined as T : await response.json() as T;
}

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
