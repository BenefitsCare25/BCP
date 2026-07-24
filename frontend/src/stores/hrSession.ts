import { create } from "zustand";
import { persist } from "zustand/middleware";

export interface HrMe {
  user_id: string;
  email: string;
  display_name: string | null;
  role: string;
  client_id: string;
  company_name: string | null;
  /** TOTP enrolment: "none" | "pending" | "confirmed". */
  mfa_status?: string;
  /** Whether the broker has enabled 2FA for the HR surface. */
  mfa_available?: boolean;
}

interface HrSessionState {
  /** Short-lived HR access token (HS256 `typ:"hr"`). The refresh token lives in
   * a host-only httpOnly cookie the browser manages — never in JS. */
  token: string | null;
  expiresAt: string | null;
  me: HrMe | null;
  mfaEnrollmentRequired: boolean;
  setSession: (
    token: string,
    expiresAt: string,
    me: HrMe,
    mfaEnrollmentRequired?: boolean,
  ) => void;
  clearSession: () => void;
}

export const useHrSession = create<HrSessionState>()(
  persist(
    (set) => ({
      token: null,
      expiresAt: null,
      me: null,
      mfaEnrollmentRequired: false,
      setSession: (token, expiresAt, me, mfaEnrollmentRequired = false) =>
        set({ token, expiresAt, me, mfaEnrollmentRequired }),
      clearSession: () =>
        set({ token: null, expiresAt: null, me: null, mfaEnrollmentRequired: false }),
    }),
    // Distinct key from the broker + portal sessions — three surfaces, one
    // browser, must never clobber each other.
    { name: "inspro-hr-session" },
  ),
);

export function hasValidHrSession(): boolean {
  const { token, expiresAt } = useHrSession.getState();
  if (!token || !expiresAt) return false;
  return new Date(expiresAt).getTime() > Date.now();
}
