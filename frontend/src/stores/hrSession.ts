import { create } from "zustand";

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
  mfa_required?: boolean;
}

interface HrSessionState {
  /** Short-lived HR access token (HS256 `typ:"hr"`). The refresh token lives in
   * a host-only httpOnly cookie the browser manages — never in JS. */
  token: string | null;
  expiresAt: string | null;
  me: HrMe | null;
  mfaEnrollmentRequired: boolean;
  mfaRecoveryPending: boolean;
  setSession: (token: string, expiresAt: string, me: HrMe, mfaEnrollmentRequired?: boolean) => void;
  clearSession: () => void;
}

try { localStorage.removeItem("inspro-hr-session"); } catch { /* Storage may be blocked. */ }

export const useHrSession = create<HrSessionState>()((set) => ({
  token: null,
  expiresAt: null,
  me: null,
  mfaEnrollmentRequired: false,
  mfaRecoveryPending: false,
  setSession: (token, expiresAt, me, mfaEnrollmentRequired = false) =>
    set(state => {
      const mfaRecoveryPending = state.me?.user_id === me.user_id &&
        state.me?.client_id === me.client_id && state.mfaRecoveryPending;
      return { token, expiresAt, me, mfaRecoveryPending,
        mfaEnrollmentRequired: mfaEnrollmentRequired || mfaRecoveryPending };
    }),
  clearSession: () =>
    set({ token: null, expiresAt: null, me: null, mfaEnrollmentRequired: false, mfaRecoveryPending: false }),
}));

export function hasValidHrSession(): boolean {
  const { token, expiresAt } = useHrSession.getState();
  if (!token || !expiresAt) return false;
  return new Date(expiresAt).getTime() > Date.now();
}
