import { create } from "zustand";
import { persist } from "zustand/middleware";

interface SessionState {
  currentPolicyYearId: string | null;
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
      setPolicyYear: (id) => set({ currentPolicyYearId: id }),
      activeClientId: null,
      // Switching client invalidates the selected policy year — it belongs to
      // the previous client. TopBar repopulates it from the new client's years.
      setActiveClient: (id) => set({ activeClientId: id, currentPolicyYearId: null }),
    }),
    { name: "inspro-session" },
  ),
);
