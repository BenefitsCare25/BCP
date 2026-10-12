/** Where a signed-out visitor was heading when a shell sent them to sign in
 *  (`…/sign-in?from=…`). Kept in tab storage across the Microsoft redirect and
 *  the password and two-factor steps, and taken once sign-in completes. Only a
 *  path on this origin inside the SAME surface is kept — a staff page for the
 *  staff app, an HR page for HR, a page of the company being signed in to for
 *  the employee portal — so the parameter can never send anyone elsewhere. */
import { tabStorage } from "@/lib/tabStorage";
import { portalPath } from "@/lib/tenant";

export type ReturnSurface = "broker" | "portal" | "hr";

const RETURN_KEY: Record<ReturnSurface, string> = {
  broker: "inspro.broker.return",
  portal: "inspro.portal.return",
  hr: "inspro.hr.return",
};

const IS_DESTINATION: Record<ReturnSurface, (path: string) => boolean> = {
  broker: (path) => !/^\/(?:sign-in|auth|broker\/security|portal|hr)(?:[/?#]|$)/.test(path),
  // The company segment is checked when the path is taken (`takePortalReturnPath`).
  portal: (path) =>
    /^\/portal\/[^/?#]+(?:[/?#]|$)/.test(path) &&
    !/^\/portal\/[^/?#]+\/(?:sign-in|set-password)(?:[/?#]|$)/.test(path),
  hr: (path) =>
    /^\/hr(?:[/?#]|$)/.test(path) && !/^\/hr\/(?:sign-in|set-password)(?:[/?#]|$)/.test(path),
};

function safeReturnPath(raw: unknown, surface: ReturnSurface): string | null {
  if (typeof raw !== "string" || !raw.startsWith("/")) return null;
  try {
    const url = new URL(raw, window.location.origin);
    if (url.origin !== window.location.origin) return null;
    const path = `${url.pathname}${url.search}${url.hash}`;
    return IS_DESTINATION[surface](path) ? path : null;
  } catch {
    return null;
  }
}

/** Record the sign-in page's `from`; a sign-in page without one clears it. */
export function rememberReturnPath(raw: unknown, surface: ReturnSurface = "broker"): void {
  const path = safeReturnPath(raw, surface);
  if (path) tabStorage.set(RETURN_KEY[surface], path);
  else tabStorage.remove(RETURN_KEY[surface]);
}

function take(surface: ReturnSurface): string | null {
  const path = safeReturnPath(tabStorage.get(RETURN_KEY[surface]), surface);
  tabStorage.remove(RETURN_KEY[surface]);
  return path;
}

/** The recorded staff destination, once, or the home page. */
export function takeReturnPath(): string {
  return take("broker") ?? "/";
}

/** The recorded portal page, once — only when it belongs to `company`, the
 *  company just signed in to. */
export function takePortalReturnPath(company: string): string | null {
  const path = take("portal");
  if (!path || !company) return null;
  const home = portalPath(company).toLowerCase();
  const lower = path.toLowerCase();
  return lower === home || (lower.startsWith(home) && /^[/?#]/.test(lower.slice(home.length)))
    ? path
    : null;
}

/** The recorded HR page, once. */
export function takeHrReturnPath(): string | null {
  return take("hr");
}

/** `signInPath` with `?from=` naming the page this tab is on, for a sign-in
 *  reached by a full page load. */
export function signInReturningHere(signInPath: string): string {
  const here = `${window.location.pathname}${window.location.search}`;
  return `${signInPath}${signInPath.includes("?") ? "&" : "?"}from=${encodeURIComponent(here)}`;
}
