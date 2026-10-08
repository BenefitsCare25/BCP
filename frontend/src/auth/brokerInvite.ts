/** Broker staff invitations arrive as `/sign-in#invite=<token>`.
 *
 *  The token travels in the fragment so it never reaches a server log or a
 *  Referer header. It is moved into tab storage before anything else reads the
 *  address (MSAL included) and scrubbed from the address bar and this history
 *  entry. Tab storage carries it across the Microsoft redirect, so the
 *  exchange on return can name the invitation. It is cleared once accepted,
 *  refused, or when the tab signs out. */
import { useCallback, useState } from "react";
import { tabStorage } from "@/lib/tabStorage";
import { rememberSignInNotice } from "./signInNotice";

/** `SIGN_IN_PATH` in `api/client.ts`, which imports this module through MSAL. */
const SIGN_IN_PATH = "/sign-in";
const INVITE_KEY = "inspro.broker.invite";
const INVITE_PARAM = "invite";
const TOKEN_SHAPE = /^[A-Za-z0-9._~-]{8,1024}$/;

export function captureBrokerInviteFromUrl(): void {
  if (window.location.pathname !== SIGN_IN_PATH) return;
  const fragment = new URLSearchParams(window.location.hash.slice(1));
  const token = fragment.get(INVITE_PARAM);
  if (token === null) return;
  fragment.delete(INVITE_PARAM);
  const rest = fragment.toString();
  window.history.replaceState(
    window.history.state,
    "",
    `${window.location.pathname}${window.location.search}${rest ? `#${rest}` : ""}`,
  );
  const trimmed = token.trim();
  if (TOKEN_SHAPE.test(trimmed)) {
    tabStorage.set(INVITE_KEY, trimmed);
  } else {
    tabStorage.remove(INVITE_KEY);
    rememberSignInNotice({ code: "invitation_invalid" });
  }
}

export function pendingBrokerInvite(): string | null {
  return tabStorage.get(INVITE_KEY);
}

export function clearBrokerInvite(): void {
  tabStorage.remove(INVITE_KEY);
}

/** The invitation this tab is accepting, and a way to drop it once it is
 *  accepted or refused. */
export function usePendingBrokerInvite(): [string | null, () => void] {
  const [token, setToken] = useState(pendingBrokerInvite);
  const dismiss = useCallback(() => {
    clearBrokerInvite();
    setToken(null);
  }, []);
  return [token, dismiss];
}
