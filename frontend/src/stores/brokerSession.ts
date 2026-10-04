import { create } from "zustand";
import type { AccountInfo } from "@azure/msal-browser";

export interface BrokerSession {
  access_token: string;
  expires_at: string;
  user: { id: string; email: string; display_name: string | null };
  mfa_verified: boolean;
  mfa_required: boolean;
}

export const useBrokerSession = create<{
  session: BrokerSession | null;
  set: (session: BrokerSession | null) => void;
}>((set) => ({ session: null, set: (session) => set({ session }) }));

export function brokerAccount(): AccountInfo | null {
  const session = useBrokerSession.getState().session;
  if (!session) return null;
  return { homeAccountId: session.user.id, localAccountId: session.user.id,
    environment: "inspro", tenantId: "", username: session.user.email,
    name: session.user.display_name ?? session.user.email };
}
