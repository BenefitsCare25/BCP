/** Why the last sign-in attempt in this tab was refused, handed from the boot
 *  sequence (which completes the Microsoft redirect before the router renders)
 *  to the sign-in page. Kept in tab storage, never the address bar. */
import { useEffect, useState } from "react";
import { tabStorage } from "@/lib/tabStorage";

const NOTICE_KEY = "inspro.broker.sign-in-notice";

export interface SignInNotice {
  code: string;
  message?: string;
}

export function rememberSignInNotice(notice: SignInNotice): void {
  tabStorage.set(NOTICE_KEY, JSON.stringify(notice));
}

function readNotice(): SignInNotice | null {
  const raw = tabStorage.get(NOTICE_KEY);
  if (!raw) return null;
  try {
    const parsed = JSON.parse(raw) as { code?: unknown; message?: unknown };
    if (typeof parsed.code !== "string") return null;
    return {
      code: parsed.code,
      ...(typeof parsed.message === "string" && parsed.message ? { message: parsed.message } : {}),
    };
  } catch {
    return null;
  }
}

/** Read once when the page mounts, then cleared, so a reload does not repeat
 *  an old refusal. */
export function useSignInNotice(): SignInNotice | null {
  const [notice] = useState(readNotice);
  useEffect(() => {
    tabStorage.remove(NOTICE_KEY);
  }, []);
  return notice;
}
