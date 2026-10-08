import {
  PublicClientApplication,
  type AccountInfo,
  type AuthenticationResult,
  type Configuration,
} from "@azure/msal-browser";
import { errorCode } from "@/lib/errors";
import { brokerAccount, useBrokerSession } from "@/stores/brokerSession";
import { useNotifications } from "@/stores/notifications";
import { useSession } from "@/stores/session";
import { clearBrokerInvite, pendingBrokerInvite } from "./brokerInvite";
import {
  acceptBrokerSession,
  brokerAccessToken,
  brokerAuthRequest,
  exchangeMicrosoftToken,
  refreshBrokerSession,
  restorationSuppressed,
  setRestorationSuppressed,
} from "./brokerSession";
import { entraConfig, type EntraClientConfig } from "./staffSignIn";

/** MSAL is configured at runtime from the firm's directory (`/public/site`),
 *  falling back to the build's `VITE_ENTRA_*` values; see `staffSignIn.ts`.
 *  Microsoft tokens are exchanged once for an Inspro broker session and never
 *  reach data APIs. */
function msalConfiguration(entra: EntraClientConfig): Configuration {
  return {
    auth: {
      clientId: entra.clientId,
      authority: entra.authority,
      redirectUri: window.location.origin + "/auth/callback",
      postLogoutRedirectUri: window.location.origin + "/",
    },
    cache: {
      // MSAL requires this for redirects. Clear the first-factor credentials
      // immediately on return; platform sessions never use browser storage.
      cacheLocation: "sessionStorage",
    },
  };
}

// One instance per directory configuration, so React strict-mode double
// renders and repeated callers share it.
let _msal: PublicClientApplication | null = null;
let _msalKey = "";
let _initialisationPromise: Promise<void> | null = null;
const prepared = new WeakMap<PublicClientApplication, Promise<AuthenticationResult | null>>();

function onOtherSurface(): boolean {
  const path = window.location.pathname;
  return path.startsWith("/portal/") || path.startsWith("/hr/");
}

function removeLegacyBrokerTokens(clientId: string): void {
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

function getMsal(): { msal: PublicClientApplication; entra: EntraClientConfig } | null {
  const entra = entraConfig();
  if (!entra) return null;
  const key = `${entra.clientId}|${entra.authority}`;
  if (_msal === null || _msalKey !== key) {
    _msal = new PublicClientApplication(msalConfiguration(entra));
    _msalKey = key;
  }
  return { msal: _msal, entra };
}

/** MSAL requires `initialize()` before any other call, and the redirect
 *  response must be read before a new interaction starts. Once per instance. */
function prepare(msal: PublicClientApplication, clientId: string): Promise<AuthenticationResult | null> {
  let ready = prepared.get(msal);
  if (!ready) {
    ready = (async () => {
      await msal.initialize();
      removeLegacyBrokerTokens(clientId);
      return await msal.handleRedirectPromise();
    })().catch((err: unknown) => {
      // Never reuse an instance whose start-up failed: the next attempt
      // builds a fresh one instead of awaiting this rejection forever.
      prepared.delete(msal);
      if (_msal === msal) {
        _msal = null;
        _msalKey = "";
      }
      throw err;
    });
    prepared.set(msal, ready);
  }
  return ready;
}

async function completeMicrosoftSignIn(accessToken: string): Promise<void> {
  const inviteToken = pendingBrokerInvite();
  try {
    const session = await exchangeMicrosoftToken(accessToken, inviteToken);
    if (inviteToken) clearBrokerInvite();
    acceptBrokerSession(session);
  } catch (error) {
    // A refused invitation cannot succeed on retry; anything else (an outage,
    // another account chosen by mistake) keeps it for the next attempt.
    if (inviteToken && errorCode(error) === "invitation_invalid") clearBrokerInvite();
    throw error;
  }
}

/**
 * Finish a Microsoft redirect (exchanging its token for a broker session) or
 * restore this tab's session from the refresh cookie, whichever applies. Runs
 * at boot before the router renders. Idempotent: concurrent callers share one
 * promise, and a failure clears it so a retry starts afresh.
 */
export async function initializeBrokerSignIn(): Promise<void> {
  if (_initialisationPromise === null) {
    _initialisationPromise = (async () => {
      const client = getMsal();
      const response = client ? await prepare(client.msal, client.entra.clientId) : null;
      if (client && response?.account) {
        await client.msal.clearCache();
        await completeMicrosoftSignIn(response.accessToken);
      } else if (!restorationSuppressed() && !onOtherSurface()) {
        await refreshBrokerSession();
      }
    })().catch((err: unknown) => {
      _initialisationPromise = null;
      // MSAL memoizes redirect results, including failures. A fresh instance
      // lets a refused identity choose another account without re-exchanging
      // the earlier callback or awaiting its cached error.
      _msal = null;
      _msalKey = "";
      throw err;
    });
  }
  await _initialisationPromise;
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
   * SAME rejected account straight back in and the user could never switch —
   * and when accepting an invitation, which may be for another account. */
  selectAccount?: boolean;
}): Promise<void> {
  const client = getMsal();
  if (!client) throw new Error("Microsoft sign-in is not available for this organisation.");
  await prepare(client.msal, client.entra.clientId);
  const request = { scopes: client.entra.scopes };
  await client.msal.loginRedirect(
    options?.selectAccount ? { ...request, prompt: "select_account" } : request,
  );
}

/**
 * Clear platform memory, cached data and any remaining Microsoft credentials.
 * Server revocation belongs to signOut(); access refusals use this local cleanup.
 * The company/benefit-year selection is persisted and the alerts are global, so
 * both are reset too: the next account in this browser must not start in the
 * previous one's company or read its alerts.
 */
export async function clearLocalSession(): Promise<void> {
  useBrokerSession.getState().set(null);
  useSession.getState().setActiveClient(null);
  useNotifications.getState().clear();
  const { queryClient } = await import("@/lib/queryClient");
  queryClient.clear();
  const msal = _msal;
  if (!msal) return;
  try {
    await msal.clearCache();
    msal.setActiveAccount(null);
  } catch {
    console.warn("Could not clear the Microsoft sign-in cache.");
  }
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
  clearBrokerInvite();
  await clearLocalSession();
  window.location.replace("/sign-in?signed_out=1");
}
