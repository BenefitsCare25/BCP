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
  /** True once a password exists — either a mailed one-time value or one the
   *  member set. NOT a sign of being onboarded; read `status` for that. */
  has_password: boolean;
  /** When an invite email was confirmed delivered. null = never received one,
   *  which is exactly what the bulk send targets. */
  invite_sent_at: string | null;
  /** Deadline on a mailed one-time password that hasn't been used yet. */
  invite_expires_at: string | null;
  /** Whether this member's ROSTER row still lets them in — a different axis
   *  from `status`. `status` is the broker's manual switch; this is derived
   *  from the employee record and moves on its own as a leaver's run-off
   *  expires, so an account can be `active` and still let nobody in.
   *  `null` on older payloads. */
  access_state:
    | "active"
    | "run_off"
    | "settling"
    | "ended"
    | "unknown"
    | null;
  access_ends_on: string | null;
  /** The stated last day of service, when the roster gives one. A member
   *  terminated with a last day still AHEAD is `active` today and loses
   *  everything on a known date — this is the only warning of that. */
  last_day: string | null;
  /** What THIS member should be told to type, resolved server-side from the
   *  company's "Login username" setting. Never re-derive it here: the setting
   *  lives behind a firm-admin-only endpoint this page can't call. */
  login_username: string | null;
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
  /** Server-owned rules the UI states and pre-validates. Served, not mirrored —
   *  a TypeScript copy drifts silently the moment the real value moves. */
  password_min_length: number;
  set_password_ttl_hours: number;
}

export interface BulkInviteResult {
  /** Invites dispatched for delivery (sending runs in the background). */
  queued: number;
  accounts_created: number;
  no_email: number;
  duplicate: number;
  already_invited: number;
  skipped_disabled: number;
  /** A run was already in flight; this request did nothing. */
  already_sending: boolean;
}

export interface PortalRolloutMember {
  employee_id: string;
  staff_id: string;
  employee_name: string | null;
  /** Why the send couldn't reach them — the two need different fixes. */
  reason: "no_email" | "duplicate";
  email: string | null;
}

/** Portal-access state of the whole roster. `invite_pending` is both what the
 *  send button counts and what the endpoint acts on — one server-side
 *  classification, so the label can't promise more than the send delivers. */
export interface PortalRollout {
  employees_total: number;
  invite_pending: number;
  invited: number;
  signed_in: number;
  no_email: number;
  /** Roster rows sharing an email (or staff id) with another employee — not
   *  provisioned, because one mailbox must not receive two members' logins. */
  duplicate: number;
  disabled: number;
  /** False when the configured mailer can't even be built (e.g. SMTP mode with
   *  no host) — pressing send would queue hundreds and deliver none. */
  mail_deliverable: boolean;
  /** "log" writes invites to the application log rather than emailing them —
   *  the dev/staging default. Warned about, not blocked. */
  mail_mode: string;
  /** A delivery run is working through the roster right now. */
  sending: boolean;
  needs_attention: PortalRolloutMember[];
  needs_attention_truncated: boolean;
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
      // Every one of these moves a member between rollout buckets.
      void qc.invalidateQueries({ queryKey: ["portal-rollout"] });
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
      // Every one of these moves a member between rollout buckets.
      void qc.invalidateQueries({ queryKey: ["portal-rollout"] });
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
      // Every one of these moves a member between rollout buckets.
      void qc.invalidateQueries({ queryKey: ["portal-rollout"] });
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
      // Every one of these moves a member between rollout buckets.
      void qc.invalidateQueries({ queryKey: ["portal-rollout"] });
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
      // Every one of these moves a member between rollout buckets.
      void qc.invalidateQueries({ queryKey: ["portal-rollout"] });
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
      // Every one of these moves a member between rollout buckets.
      void qc.invalidateQueries({ queryKey: ["portal-rollout"] });
    },
    meta: { localErrorHandling: true },
  });
}

export function usePortalRollout(policyYearId: string | null) {
  const cid = useSession((s) => s.activeClientId);
  return useQuery({
    queryKey: ["portal-rollout", cid, policyYearId],
    queryFn: () =>
      api.get<PortalRollout>(
        `/member-accounts/rollout?policy_year_id=${policyYearId}`,
      ),
    enabled: Boolean(policyYearId),
    // Delivery is a background run that takes minutes on a full roster, so the
    // counts move while the page sits open. Poll only WHILE it runs — a static
    // card that silently goes stale is what makes an operator press send again.
    refetchInterval: (q) => (q.state.data?.sending ? 3000 : false),
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
      void qc.invalidateQueries({ queryKey: ["portal-rollout"] });
    },
  });
}
