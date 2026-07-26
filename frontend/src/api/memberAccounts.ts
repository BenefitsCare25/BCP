/** Broker-side member-account (employee portal access) provisioning hooks. */
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "@/api/client";
import { useSession } from "@/stores/session";

export interface MemberAccount {
  id: string;
  client_id: string;
  email: string | null;
  staff_id: string;
  display_name: string | null;
  status: "invited" | "active" | "disabled";
  invited_by: string | null;
  last_sign_in_at: string | null;
  created_at: string;
  /** Broker-generated alternate username; null until allocated. */
  system_login_id: string | null;
  /** True once the member has set a password (credential login enabled). */
  has_password: boolean;
  /** Invite/resend responses only: whether the invite email actually sent.
   *  Absent on older backends — treat undefined as "assumed sent". */
  mail_sent?: boolean;
  /** Set-password-link responses only: a single-use token for the member to
   *  choose their own password on the portal. */
  set_password_token?: string | null;
  tenant_slug?: string | null;
}

export interface MemberAccountListResult {
  total: number;
  items: MemberAccount[];
}

export interface BulkInviteResult {
  invited: number;
  skipped_existing: number;
  skipped_no_email: number;
  /** Number of invite emails that failed to send (absent on older backends). */
  mail_failed?: number;
}

export function useMemberAccounts() {
  const cid = useSession((s) => s.activeClientId);
  return useQuery({
    queryKey: ["member-accounts", cid],
    queryFn: () => api.get<MemberAccountListResult>("/member-accounts"),
  });
}

export function useCreateMemberAccount() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (input: { employeeId: string; email?: string }) =>
      api.post<MemberAccount>(`/employees/${input.employeeId}/member-account`, {
        email: input.email ?? null,
      }),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ["member-accounts"] });
    },
    meta: { localErrorHandling: true },
  });
}

export function useResendMemberInvite() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (accountId: string) =>
      api.post<MemberAccount>(`/member-accounts/${accountId}/resend-invite`, {}),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ["member-accounts"] });
    },
  });
}

export function useSetMemberAccountStatus() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (input: { accountId: string; status: "active" | "disabled" }) =>
      api.patch<MemberAccount>(`/member-accounts/${input.accountId}`, {
        status: input.status,
      }),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ["member-accounts"] });
    },
  });
}

/** Mint a single-use set-password link the member redeems on the portal
 *  (allocates a system login id if one wasn't assigned yet). */
export function useMemberPasswordSetupLink() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (accountId: string) =>
      api.post<MemberAccount>(`/member-accounts/${accountId}/password-setup`, {}),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ["member-accounts"] });
    },
    meta: { localErrorHandling: true },
  });
}

/** Broker sets a member's password directly (email-less members). */
export function useSetMemberPassword() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (input: { accountId: string; password: string }) =>
      api.post<MemberAccount>(
        `/member-accounts/${input.accountId}/set-password`,
        { password: input.password },
      ),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ["member-accounts"] });
    },
    meta: { localErrorHandling: true },
  });
}

export function useRegenerateMemberLoginId() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (accountId: string) =>
      api.post<MemberAccount>(
        `/member-accounts/${accountId}/regenerate-login-id`,
        {},
      ),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ["member-accounts"] });
    },
    meta: { localErrorHandling: true },
  });
}

export function useBulkInviteMembers() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (policyYearId: string) =>
      api.post<BulkInviteResult>("/member-accounts/bulk-invite", {
        policy_year_id: policyYearId,
      }),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ["member-accounts"] });
    },
  });
}
