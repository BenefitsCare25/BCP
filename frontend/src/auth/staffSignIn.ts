/** How broker staff sign in on this host, resolved at runtime.
 *
 *  Each broker firm chooses Microsoft 365 (its own Entra directory), email and
 *  password, or both; `/public/site` reports the choice for the host the page
 *  was loaded from. A build carrying `VITE_ENTRA_*` values (local development
 *  against a real tenant) still offers Microsoft when the site names no
 *  directory, or could not be read. */
import { useMemo } from "react";
import { loadPublicSite, loadedPublicSite, usePublicSite, type PublicSite } from "@/api/public";

export interface EntraClientConfig {
  clientId: string;
  authority: string;
  scopes: string[];
}

export interface StaffSignIn {
  entra: EntraClientConfig | null;
  local: boolean;
}

const IDENTITY_SCOPES = ["openid", "profile", "email"];

function withIdentityScopes(scopes: string[]): string[] {
  return [...new Set([...IDENTITY_SCOPES, ...scopes])];
}

function buildTimeEntra(): EntraClientConfig | null {
  const tenantId = import.meta.env.VITE_ENTRA_TENANT_ID ?? "";
  const clientId = import.meta.env.VITE_ENTRA_CLIENT_ID ?? "";
  if (!tenantId || !clientId) return null;
  const audience = import.meta.env.VITE_ENTRA_AUDIENCE || `api://${clientId}`;
  return {
    clientId,
    authority: `https://login.microsoftonline.com/${tenantId}`,
    scopes: withIdentityScopes([`${audience}/access_as_user`]),
  };
}

const BUILD_ENTRA = buildTimeEntra();

export function resolveStaffSignIn(site: PublicSite | null): StaffSignIn {
  const offered = site?.staff_sign_in.entra;
  return {
    entra: offered
      ? {
          clientId: offered.client_id,
          authority: offered.authority,
          scopes: withIdentityScopes(offered.scopes),
        }
      : BUILD_ENTRA,
    local: site?.staff_sign_in.local ?? false,
  };
}

/** The methods known right now (the site read at boot, or the build values). */
export function staffSignIn(): StaffSignIn {
  return resolveStaffSignIn(loadedPublicSite());
}

export function entraConfig(): EntraClientConfig | null {
  return staffSignIn().entra;
}

/** Whether the broker app requires a broker session.
 *
 *  Any offered method does. With nothing offered, a development build runs
 *  against the backend's mock identity, as builds without Entra values always
 *  have. A production build never runs unauthenticated: the sign-in page
 *  explains that no method is set up, or offers a retry when the site could
 *  not be read. */
export function brokerAuthEnabled(): boolean {
  const { entra, local } = staffSignIn();
  return entra !== null || local || !import.meta.env.DEV;
}

/** Read the site configuration before the router renders. Not fatal: guards
 *  then fall back to the build values (or fail closed in production) and the
 *  sign-in page offers a retry. */
export async function loadStaffSignIn(): Promise<void> {
  try {
    await loadPublicSite();
  } catch {
    console.warn("Sign-in options could not be loaded; the sign-in page will retry.");
  }
}

/** The site query plus the methods it offers, for the sign-in page. */
export function useStaffSignIn() {
  const site = usePublicSite();
  const methods = useMemo(() => resolveStaffSignIn(site.data ?? null), [site.data]);
  return { site, methods };
}
