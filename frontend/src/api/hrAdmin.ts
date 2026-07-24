/** Broker-side HR account provisioning + per-tenant auth policy.
 *
 * Uses the broker `api` client (Entra/mock identity + X-Inspro-Client), NOT the
 * HR surface client — these are firm-admin actions on `/hr-admin/*`.
 */
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "@/api/client";

export interface HrAccount {
  user_id: string;
  email: string;
  display_name: string | null;
  role: string;
  status: string;
  client_id: string;
  hr_login_id: string | null;
  mfa_enrolled: boolean;
  last_login_at: string | null;
}

export interface HrAccountCreated extends HrAccount {
  /** Single-use set-password token — shown once; deliver to the HR admin. */
  set_password_token: string;
}

export type LoginSource = "email" | "system_id" | "staff_id";

export interface HrAuthPolicy {
  client_id: string;
  mfa_hr_enabled: boolean;
  mfa_portal_enabled: boolean;
  hr_login_source: LoginSource;
  portal_login_source: LoginSource;
  password_min_entropy: number;
  password_rotation_days: number | null;
  session_idle_minutes: number;
  session_absolute_hours: number;
  breach_check_enabled: boolean;
}

export type HrAuthPolicyPatch = Partial<Omit<HrAuthPolicy, "client_id">>;

const accountsKey = (clientId: string) => ["hr-accounts", clientId] as const;
const policyKey = (clientId: string) => ["hr-auth-policy", clientId] as const;

export function useHrAccounts(clientId: string | null) {
  return useQuery({
    queryKey: accountsKey(clientId ?? ""),
    queryFn: () =>
      api.get<HrAccount[]>(`/hr-admin/accounts?client_id=${encodeURIComponent(clientId!)}`),
    enabled: !!clientId,
  });
}

export function useCreateHrAccount(clientId: string | null) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: { email: string; display_name?: string; role: string }) =>
      api.post<HrAccountCreated>("/hr-admin/accounts", { ...body, client_id: clientId }),
    onSuccess: () => qc.invalidateQueries({ queryKey: accountsKey(clientId ?? "") }),
  });
}

export function useResetHrPassword(clientId: string | null) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (userId: string) =>
      api.post<HrAccountCreated>(`/hr-admin/accounts/${userId}/reset-password`, {}),
    onSuccess: () => qc.invalidateQueries({ queryKey: accountsKey(clientId ?? "") }),
  });
}

export function useRegenerateHrLoginId(clientId: string | null) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (userId: string) =>
      api.post<HrAccount>(`/hr-admin/accounts/${userId}/regenerate-login-id`, {}),
    onSuccess: () => qc.invalidateQueries({ queryKey: accountsKey(clientId ?? "") }),
  });
}

export function useSetHrAccountEnabled(clientId: string | null) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ userId, enabled }: { userId: string; enabled: boolean }) =>
      api.post<HrAccount>(
        `/hr-admin/accounts/${userId}/${enabled ? "enable" : "disable"}`,
        {},
      ),
    onSuccess: () => qc.invalidateQueries({ queryKey: accountsKey(clientId ?? "") }),
  });
}

export function useHrAuthPolicy(clientId: string | null) {
  return useQuery({
    queryKey: policyKey(clientId ?? ""),
    queryFn: () => api.get<HrAuthPolicy>(`/hr-admin/clients/${clientId}/auth-policy`),
    enabled: !!clientId,
  });
}

export function useUpdateHrAuthPolicy(clientId: string | null) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (patch: HrAuthPolicyPatch) =>
      api.put<HrAuthPolicy>(`/hr-admin/clients/${clientId}/auth-policy`, patch),
    onSuccess: (data) => qc.setQueryData(policyKey(clientId ?? ""), data),
  });
}
