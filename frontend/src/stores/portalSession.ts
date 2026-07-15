import { create } from "zustand";
import { persist } from "zustand/middleware";

export interface PortalMember {
  id: string;
  email: string;
  staff_id: string;
  display_name: string | null;
}

interface PortalSessionState {
  /** Member bearer token (HS256 JWT from /portal/auth/verify). */
  token: string | null;
  /** ISO expiry of the token — checked by the route guard. */
  expiresAt: string | null;
  member: PortalMember | null;
  setSession: (token: string, expiresAt: string, member: PortalMember) => void;
  clearSession: () => void;
}

export const usePortalSession = create<PortalSessionState>()(
  persist(
    (set) => ({
      token: null,
      expiresAt: null,
      member: null,
      setSession: (token, expiresAt, member) => set({ token, expiresAt, member }),
      clearSession: () => set({ token: null, expiresAt: null, member: null }),
    }),
    // Separate storage key from the broker session — the two sign-in surfaces
    // must never clobber each other in the same browser.
    { name: "inspro-portal-session" },
  ),
);

export function hasValidPortalSession(): boolean {
  const { token, expiresAt } = usePortalSession.getState();
  if (!token || !expiresAt) return false;
  return new Date(expiresAt).getTime() > Date.now();
}
