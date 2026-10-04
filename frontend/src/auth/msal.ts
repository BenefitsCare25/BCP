import {
  Configuration,
  PublicClientApplication,
  type AccountInfo,
} from "@azure/msal-browser";
import { brokerAccount, useBrokerSession, type BrokerSession } from "@/stores/brokerSession";
import { brokerAccessToken, brokerAuthRequest, refreshBrokerSession } from "./brokerSession";

const tenantId = import.meta.env.VITE_ENTRA_TENANT_ID ?? "";
const clientId = import.meta.env.VITE_ENTRA_CLIENT_ID ?? "";
const audience = import.meta.env.VITE_ENTRA_AUDIENCE || `api://${clientId}`;

export const ENTRA_ENABLED = Boolean(tenantId && clientId);

export const msalConfig: Configuration = {
  auth: {
    clientId,
    authority: tenantId ? `https://login.microsoftonline.com/${tenantId}` : undefined,
    redirectUri:
      typeof window !== "undefined"
        ? window.location.origin + "/auth/callback"
        : "/auth/callback",
    postLogoutRedirectUri:
      typeof window !== "undefined" ? window.location.origin + "/" : "/",
  },
  cache: {
    // MSAL requires this for redirects. Clear the first-factor credentials
    // immediately on return; platform sessions never use browser storage.
    cacheLocation: "sessionStorage",
  },
};

export const loginRequest = {
  scopes: ["openid", "profile", "email", `${audience}/access_as_user`],
};

// Singleton — instantiated once even with React strict-mode double-render.
let _msal: PublicClientApplication | null = null;
let _initialisationPromise: Promise<void> | null = null;
const signedOutKey = "inspro-broker-signed-out";

function restorationSuppressed(): boolean {
  // The URL also covers browsers that refuse sessionStorage writes.
  if (new URLSearchParams(window.location.search).get("signed_out") === "1") return true;
  try { return sessionStorage.getItem(signedOutKey) === "1"; } catch { return false; }
}

function setRestorationSuppressed(suppressed: boolean): void {
  try {
    if (suppressed) sessionStorage.setItem(signedOutKey, "1");
    else sessionStorage.removeItem(signedOutKey);
  } catch { /* The sign-out URL retains this tab's explicit choice. */ }
}

function removeLegacyBrokerTokens(): void {
  for (const name of ["sessionStorage", "localStorage"] as const) {
    try {
      const storage = window[name];
      for (const key of Object.keys(storage)) {
        let value: { clientId?: string; credentialType?: string };
        try { value = JSON.parse(storage.getItem(key) ?? "null"); } catch { continue; }
        if (value?.clientId === clientId && /token/i.test(value.credentialType ?? "")) storage.removeItem(key);
      }
    } catch { /* Redirect state requires browser storage; no bearer tokens are persisted. */ }
  }
}

export function getMsal(): PublicClientApplication | null {
  if (!ENTRA_ENABLED) return null;
  if (_msal === null) {
    _msal = new PublicClientApplication(msalConfig);
  }
  return _msal;
}

/**
 * MSAL requires explicit `initialize()` before any other call. Idempotent —
 * the same promise is returned across callers so concurrent boots don't double-init.
 */
export async function initializeMsal(): Promise<PublicClientApplication | null> {
  const msal = getMsal();
  if (!msal) return null;
  if (_initialisationPromise === null) {
    _initialisationPromise = (async () => {
      await msal.initialize();
      removeLegacyBrokerTokens();
      // Process the response from a redirect-flow sign-in BEFORE the app
      // renders. If we're not in the callback URL this is a no-op.
      const response = await msal.handleRedirectPromise();
      if (response?.account) {
        await msal.clearCache();
        const session = await brokerAuthRequest<BrokerSession>("/exchange", { access_token: response.accessToken });
        setRestorationSuppressed(false);
        useBrokerSession.getState().set(session);
      } else if (!restorationSuppressed() && !window.location.pathname.startsWith("/portal/") && !window.location.pathname.startsWith("/hr/")) {
        await refreshBrokerSession();
      }
    })().catch((err: unknown) => {
      // Reset the cached promise on failure so a retry (e.g. from the sign-in
      // page) can attempt initialization again instead of re-awaiting the
      // same rejection forever.
      _initialisationPromise = null;
      // MSAL memoizes redirect results, including failures. A fresh instance
      // lets a refused identity choose another account without re-exchanging
      // the earlier callback or awaiting its cached error.
      _msal = null;
      throw err;
    });
  }
  await _initialisationPromise;
  return msal;
}

export function getActiveAccount(): AccountInfo | null {
  return brokerAccount();
}

/**
 * Return a platform access token; Microsoft tokens are never used by data APIs.
 * Expired platform sessions require an explicit sign-in.
 */
export async function acquireAccessToken(
  _account: AccountInfo,
): Promise<string | null> {
  return brokerAccessToken();
}

export async function signIn(options?: {
  /** Force the Microsoft account picker. Used after an access refusal — the
   * browser still holds a Microsoft session, so the default flow would sign the
   * SAME rejected account straight back in and the user could never switch. */
  selectAccount?: boolean;
}): Promise<void> {
  const msal = await initializeMsal();
  if (!msal) return;
  await msal.loginRedirect(
    options?.selectAccount
      ? { ...loginRequest, prompt: "select_account" }
      : loginRequest,
  );
}

/**
 * Clear platform memory, cached data and any remaining Microsoft credentials.
 * Server revocation belongs to signOut(); access refusals use this local cleanup.
 */
export async function clearLocalSession(): Promise<void> {
  useBrokerSession.getState().set(null);
  const { queryClient } = await import("@/lib/queryClient");
  queryClient.clear();
  const msal = getMsal();
  if (!msal) return;
  try {
    await msal.clearCache();
  } catch {
    console.warn("Could not clear the Microsoft sign-in cache.");
  }
  msal.setActiveAccount(null);
}

export async function signOut(): Promise<void> {
  const token = useBrokerSession.getState().session?.access_token;
  if (token) {
    try { await brokerAuthRequest<void>("/logout", {}, token); }
    catch (error) {
      // An already-ended session still permits local sign-out.
      if ((error as { status?: number }).status !== 401) throw error;
    }
  }
  setRestorationSuppressed(true);
  await clearLocalSession();
  window.location.replace("/sign-in?signed_out=1");
}
