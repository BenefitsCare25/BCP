/** Tab-scoped sign-in state for the HR and employee portals.
 *
 * Both pieces must outlive the full-page loads these surfaces use to leave a
 * dead session, so they live in `tabStorage` rather than in a store. */
import { useEffect, useState } from "react";
import { tabStorage } from "@/lib/tabStorage";

export type CredentialSurface = "hr" | "portal";

// ── Explicit sign-out ────────────────────────────────────────────────────────

const SIGNED_OUT_KEY: Record<CredentialSurface, string> = {
  hr: "inspro-hr-signed-out",
  portal: "inspro-portal-signed-out",
};

/** Set when the user signs out on purpose. The refresh cookie is shared by
 *  every tab on this company, so another tab may since have signed in as
 *  someone else; while this is set, this tab never restores a session from that
 *  cookie and waits for the user's own credentials instead. */
export function markSignedOut(surface: CredentialSurface): void {
  tabStorage.set(SIGNED_OUT_KEY[surface], "1");
}

/** Cleared once credentials the user submitted produce a session. */
export function clearSignedOut(surface: CredentialSurface): void {
  tabStorage.remove(SIGNED_OUT_KEY[surface]);
}

export function isSignedOut(surface: CredentialSurface): boolean {
  return tabStorage.get(SIGNED_OUT_KEY[surface]) === "1";
}

// ── Why a session ended ──────────────────────────────────────────────────────

/** `ended`: the person's own access ended. `disabled`: the company's portal
 *  was switched off. */
export type SessionEndReason = "ended" | "disabled";

/** The portal key predates the HR one; keeping it lets a refusal stored by an
 *  older open page still reach the sign-in page after an upgrade. */
const NOTICE_KEY: Record<CredentialSurface, string> = {
  hr: "inspro.hr.access-ended-message",
  portal: "inspro.portal.access-ended-message",
};

/** Shown when the refusal carried no sentence of its own, or when storage was
 *  blocked across the redirect and only the query flag survived. */
const FALLBACK_NOTICE: Record<CredentialSurface, Record<SessionEndReason, string>> = {
  portal: {
    ended:
      "Your access to this portal has ended. Contact your HR team if you still need something from your record.",
    disabled:
      "The employee portal is switched off for your company. Contact your HR team if you need help.",
  },
  hr: {
    ended: "Your access to the HR portal has ended. Contact your broker if you still need it.",
    disabled:
      "The HR portal is switched off for your company. Contact your broker if you need access.",
  },
};

const ENDED_PARAM = "ended";
const DISABLED_VALUE = "disabled";

/** Query appended to the sign-in URL by a refusal redirect: `ended` or
 *  `ended=disabled`. Written onto a raw URL, never through the router, which
 *  would JSON-encode the value. */
export function sessionEndQuery(reason: SessionEndReason): string {
  return reason === "disabled" ? `${ENDED_PARAM}=${DISABLED_VALUE}` : ENDED_PARAM;
}

export interface SessionEndingRefusal {
  code: string;
  /** The server's sentence, or "" when the refusal carried none. */
  message: string;
}

/** A coded 403 that ends the session outright, as opposed to an ordinary
 *  refusal of one action that a signed-in user reads and works around. */
export function sessionEndingRefusal(
  text: string,
  codes: ReadonlySet<string>,
): SessionEndingRefusal | null {
  try {
    const detail = (JSON.parse(text) as { detail?: unknown }).detail;
    if (!detail || typeof detail !== "object") return null;
    const { code, message } = detail as { code?: unknown; message?: unknown };
    if (typeof code !== "string" || !codes.has(code)) return null;
    return { code, message: typeof message === "string" ? message : "" };
  } catch {
    return null;
  }
}

/** Keep the sentence for the sign-in page. The server's own wording wins: it
 *  carries facts, such as the date access ended, that a generic line cannot. */
export function rememberSessionEndNotice(
  surface: CredentialSurface,
  reason: SessionEndReason,
  message: string,
): void {
  tabStorage.set(NOTICE_KEY[surface], message || FALLBACK_NOTICE[surface][reason]);
}

/** The sentence a sign-in page opens with after a refusal, or null.
 *
 *  Read once at mount and cleared after it, so it is shown for the refusal it
 *  belongs to and never again after a later, ordinary sign-out. The read sits
 *  in a state initialiser and the clear in an effect, which keeps StrictMode's
 *  double render from swallowing it. */
export function useSessionEndNotice(surface: CredentialSurface): string | null {
  const [notice] = useState(() => {
    const stored = tabStorage.get(NOTICE_KEY[surface]);
    if (stored) return stored;
    const flag = new URLSearchParams(window.location.search).get(ENDED_PARAM);
    if (flag === null) return null;
    return FALLBACK_NOTICE[surface][flag === DISABLED_VALUE ? "disabled" : "ended"];
  });
  useEffect(() => {
    tabStorage.remove(NOTICE_KEY[surface]);
  }, [surface]);
  return notice;
}
