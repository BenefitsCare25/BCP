/** Where a signed-out visitor was heading when the app shell sent them to
 *  sign in (`/sign-in?from=…`). Kept in tab storage across the Microsoft
 *  redirect and the two-factor step, and taken once sign-in completes. Only a
 *  path on this origin inside the staff app is kept, so the parameter can
 *  never send anyone elsewhere. */
import { tabStorage } from "@/lib/tabStorage";

const RETURN_KEY = "inspro.broker.return";
const NOT_A_DESTINATION = /^\/(?:sign-in|auth|broker\/security|portal|hr)(?:[/?#]|$)/;

function safeReturnPath(raw: unknown): string | null {
  if (typeof raw !== "string" || !raw.startsWith("/")) return null;
  try {
    const url = new URL(raw, window.location.origin);
    if (url.origin !== window.location.origin) return null;
    const path = `${url.pathname}${url.search}${url.hash}`;
    return NOT_A_DESTINATION.test(path) ? null : path;
  } catch {
    return null;
  }
}

/** Record the sign-in page's `from`; a sign-in page without one clears it. */
export function rememberReturnPath(raw: unknown): void {
  const path = safeReturnPath(raw);
  if (path) tabStorage.set(RETURN_KEY, path);
  else tabStorage.remove(RETURN_KEY);
}

/** The recorded destination, once, or the home page. */
export function takeReturnPath(): string {
  const path = safeReturnPath(tabStorage.get(RETURN_KEY));
  tabStorage.remove(RETURN_KEY);
  return path ?? "/";
}
