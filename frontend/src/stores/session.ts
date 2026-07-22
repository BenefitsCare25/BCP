import { create } from "zustand";
import { persist } from "zustand/middleware";

interface SessionState {
  currentPolicyYearId: string | null;
  // The client the persisted policy year belongs to. Stamped whenever the year
  // is set so a rehydrated year can be validated against the active client (a
  // year from another client must never be honored — it fires a cross-tenant
  // 404). null = no year, or set before a client was chosen.
  policyYearClientId: string | null;
  // null clears the selection — used when the active client has no policy years
  // so downstream pages don't render against a previous client's year.
  setPolicyYear: (id: string | null) => void;
  // Active client (tenant) the user is operating on. Sent to the API as the
  // X-Inspro-Client header. null = let the backend pick the user's default.
  activeClientId: string | null;
  setActiveClient: (id: string | null) => void;
}

export const useSession = create<SessionState>()(
  persist(
    (set) => ({
      currentPolicyYearId: null,
      policyYearClientId: null,
      setPolicyYear: (id) =>
        set((s) => ({
          currentPolicyYearId: id,
          // Stamp ownership so a persisted year can't survive into a session
          // scoped to a different client.
          policyYearClientId: id === null ? null : s.activeClientId,
        })),
      activeClientId: null,
      // Switching client invalidates the selected policy year — it belongs to
      // the previous client. TopBar repopulates it from the new client's years.
      setActiveClient: (id) =>
        set({ activeClientId: id, currentPolicyYearId: null, policyYearClientId: null }),
    }),
    {
      name: "inspro-session",
      // On rehydrate, drop a persisted policy year that doesn't belong to the
      // persisted active client (legacy state, or any desync). This prevents a
      // stale year from firing a cross-tenant "Policy year not found" request
      // before TopBar re-derives the active client's current year. When the
      // year and client agree (the normal case) the year is kept, so there's no
      // empty-state flash on reload.
      merge: (persisted, current) => {
        const p = (persisted ?? {}) as Partial<SessionState>;
        const yearBelongs =
          p.currentPolicyYearId != null &&
          p.policyYearClientId != null &&
          p.policyYearClientId === p.activeClientId;
        return {
          ...current,
          ...p,
          currentPolicyYearId: yearBelongs ? p.currentPolicyYearId! : null,
          policyYearClientId: yearBelongs ? p.policyYearClientId! : null,
        };
      },
    },
  ),
);
