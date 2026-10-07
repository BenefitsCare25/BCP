import { create } from "zustand";

export interface PortalMember {
  id: string;
  email: string;
  staff_id: string;
  display_name: string | null;
}

interface PortalSessionState {
  /** Member bearer token (HS256 JWT from /portal/auth/login, /mfa or /set-password). */
  token: string | null;
  /** ISO expiry of the token — checked by the route guard. */
  expiresAt: string | null;
  member: PortalMember | null;
  mfaEnrollmentRequired: boolean;
  mfaRecoveryPending: boolean;
  setSession: (token: string, expiresAt: string, member: PortalMember, mfaEnrollmentRequired?: boolean) => void;
  clearSession: () => void;
}

try { localStorage.removeItem("inspro-portal-session"); } catch { /* Storage may be blocked. */ }

export const usePortalSession = create<PortalSessionState>()((set) => ({
  token: null,
  expiresAt: null,
  member: null,
  mfaEnrollmentRequired: false,
  mfaRecoveryPending: false,
  setSession: (token, expiresAt, member, mfaEnrollmentRequired = false) =>
    set(state => {
      const mfaRecoveryPending = state.member?.id === member.id && state.mfaRecoveryPending;
      return { token, expiresAt, member, mfaRecoveryPending,
        mfaEnrollmentRequired: mfaEnrollmentRequired || mfaRecoveryPending };
    }),
  clearSession: () =>
    set({ token: null, expiresAt: null, member: null, mfaEnrollmentRequired: false, mfaRecoveryPending: false }),
}));

export function hasValidPortalSession(): boolean {
  const { token, expiresAt } = usePortalSession.getState();
  if (!token || !expiresAt) return false;
  return new Date(expiresAt).getTime() > Date.now();
}
