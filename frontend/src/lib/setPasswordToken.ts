/** Set-password tokens, kept out of anything a server, proxy or log can see.
 *
 * A token in the query string is sent with the page request, written to access
 * logs and history, and leaked in the Referer header. Links therefore carry it
 * in the URL fragment, and the sign-in page hands its rotation challenge over
 * through tab storage instead of the address bar. */
import { useEffect, useState } from "react";
import type { CredentialSurface } from "@/lib/surfaceSession";
import { tabStorage } from "@/lib/tabStorage";

const TOKEN_PARAM = "token";

const HANDOVER_KEY: Record<CredentialSurface, string> = {
  hr: "inspro.hr.set-password-challenge",
  portal: "inspro.portal.set-password-challenge",
};

/** `path#token=…` for a set-password link. */
export function setPasswordLinkPath(path: string, token: string): string {
  return `${path}#${TOKEN_PARAM}=${encodeURIComponent(token)}`;
}

/** Give the set-password page the challenge from a rotation-due sign-in. The
 *  caller navigates within the app, so the memory fallback still holds the
 *  token when storage is blocked. */
export function handOverSetPasswordChallenge(
  surface: CredentialSurface,
  token: string,
): void {
  tabStorage.set(HANDOVER_KEY[surface], token);
}

/** Fragment first (current links), then `?token=` (links already issued). */
function tokenFromAddress(): string {
  const fragment = new URLSearchParams(window.location.hash.slice(1)).get(TOKEN_PARAM);
  if (fragment) return fragment;
  return new URLSearchParams(window.location.search).get(TOKEN_PARAM) ?? "";
}

/** Drop the token from the address bar and this history entry, keeping every
 *  other parameter (the HR company, a claim deep link). The router's history
 *  state is passed through untouched. */
function scrubTokenFromAddress(): void {
  const url = new URL(window.location.href);
  const fragment = new URLSearchParams(url.hash.slice(1));
  if (!url.searchParams.has(TOKEN_PARAM) && !fragment.has(TOKEN_PARAM)) return;
  url.searchParams.delete(TOKEN_PARAM);
  fragment.delete(TOKEN_PARAM);
  const rest = fragment.toString();
  url.hash = rest ? `#${rest}` : "";
  window.history.replaceState(window.history.state, "", `${url.pathname}${url.search}${url.hash}`);
}

/** The token a set-password page redeems: from the link, else from a sign-in
 *  hand-over. Read once at mount; the address bar and the hand-over are
 *  cleared after it, so the token exists only in this page's memory. */
export function useSetPasswordToken(surface: CredentialSurface): string {
  const [token] = useState(
    () => tokenFromAddress() || tabStorage.get(HANDOVER_KEY[surface]) || "",
  );
  useEffect(() => {
    scrubTokenFromAddress();
    tabStorage.remove(HANDOVER_KEY[surface]);
  }, [surface]);
  return token;
}
