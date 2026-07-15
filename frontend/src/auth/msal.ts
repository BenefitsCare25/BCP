import {
  Configuration,
  InteractionRequiredAuthError,
  PublicClientApplication,
  type AccountInfo,
  type AuthenticationResult,
} from "@azure/msal-browser";

const tenantId = import.meta.env.VITE_ENTRA_TENANT_ID ?? "";
const clientId = import.meta.env.VITE_ENTRA_CLIENT_ID ?? "";
const audience = import.meta.env.VITE_ENTRA_AUDIENCE ?? `api://${clientId}`;

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
    cacheLocation: "sessionStorage",
  },
};

export const loginRequest = {
  scopes: ["openid", "profile", "email", `${audience}/access_as_user`],
};

// Singleton — instantiated once even with React strict-mode double-render.
let _msal: PublicClientApplication | null = null;
let _initialisationPromise: Promise<void> | null = null;

export function getMsal(): PublicClientApplication | null {
  if (!ENTRA_ENABLED) return null;
  if (_msal === null) {
    _msal = new PublicClientApplication(msalConfig);
  }
  return _msal;
}

/**
 * MSAL v3 requires explicit `initialize()` before any other call. Idempotent —
 * the same promise is returned across callers so concurrent boots don't double-init.
 */
export async function initializeMsal(): Promise<PublicClientApplication | null> {
  const msal = getMsal();
  if (!msal) return null;
  if (_initialisationPromise === null) {
    _initialisationPromise = (async () => {
      await msal.initialize();
      // Process the response from a redirect-flow sign-in BEFORE the app
      // renders. If we're not in the callback URL this is a no-op.
      const response = await msal.handleRedirectPromise();
      if (response?.account) {
        msal.setActiveAccount(response.account);
      } else if (msal.getActiveAccount() === null) {
        const accounts = msal.getAllAccounts();
        if (accounts.length > 0) {
          msal.setActiveAccount(accounts[0]);
        }
      }
    })().catch((err: unknown) => {
      // Reset the cached promise on failure so a retry (e.g. from the sign-in
      // page) can attempt initialization again instead of re-awaiting the
      // same rejection forever.
      _initialisationPromise = null;
      throw err;
    });
  }
  await _initialisationPromise;
  return msal;
}

export function getActiveAccount(): AccountInfo | null {
  const msal = getMsal();
  if (!msal) return null;
  return msal.getActiveAccount() ?? msal.getAllAccounts()[0] ?? null;
}

/**
 * Get an access token for the API audience. On `InteractionRequiredAuthError`
 * (most commonly: refresh-token expired, or first call after a session reset)
 * trigger a redirect-flow sign-in. The promise will not resolve in that case
 * — the browser navigates away.
 */
export async function acquireAccessToken(
  account: AccountInfo,
): Promise<string | null> {
  const msal = getMsal();
  if (!msal) return null;
  try {
    const result: AuthenticationResult = await msal.acquireTokenSilent({
      ...loginRequest,
      account,
    });
    return result.accessToken;
  } catch (err) {
    if (err instanceof InteractionRequiredAuthError) {
      // Falls through to a full-page redirect — promise won't resolve.
      await msal.acquireTokenRedirect({ ...loginRequest, account });
      return null;
    }
    // Network/transient failures fall through as null so the caller can
    // surface a 401 to the user rather than spinning.
    console.warn("acquireTokenSilent failed", err);
    return null;
  }
}

export async function signIn(): Promise<void> {
  const msal = await initializeMsal();
  if (!msal) return;
  await msal.loginRedirect(loginRequest);
}

export async function signOut(): Promise<void> {
  const msal = await initializeMsal();
  if (!msal) return;
  const account = getActiveAccount() ?? undefined;
  await msal.logoutRedirect({ account });
}
