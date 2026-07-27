import { api } from "./client";
import { queryClient } from "@/lib/queryClient";
import { useSession } from "@/stores/session";

export interface AccessibleClient {
  id: string;
  name: string;
}

export interface MeResponse {
  user_id: string;
  email: string | null;
  display_name: string | null;
  role: string;
  broker_firm_id: string | null;
  active_client_id: string | null;
  accessible_clients: AccessibleClient[];
}

/** Keyed by the active client so a switch refetches /me (active_client_id +
 * any client-dependent fields). The header itself is attached in client.ts.
 * Lives here so the route guard and `useMe` can't drift onto different keys. */
export function meQueryKey(activeClientId: string | null) {
  return ["me", activeClientId] as const;
}

export function fetchMe(): Promise<MeResponse> {
  return api.get<MeResponse>("/me");
}

/**
 * Resolve the caller's identity BEFORE the app shell renders. Rejects with
 * `NoAccessError` when the platform doesn't recognise the signed-in account —
 * an Entra sign-in alone is not access, so the router guard awaits this.
 */
export function ensureMe(): Promise<MeResponse> {
  const activeClientId = useSession.getState().activeClientId;
  return queryClient.ensureQueryData({
    queryKey: meQueryKey(activeClientId),
    queryFn: fetchMe,
    staleTime: 60_000,
  });
}
