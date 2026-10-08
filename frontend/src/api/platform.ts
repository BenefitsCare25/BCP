/** Platform console API — the master admin's broker firms, their web
 *  addresses, time-limited access grants and the platform audit trail.
 *
 *  Served only to a `system_admin` on a platform host; every other caller gets
 *  403/404. None of it is company-scoped, so no key carries the active company.
 *  Shared tenancy types (`TenantDomain`, `AccessGrant`) also back the firm
 *  console in `api/firm.ts`. */
import {
  useInfiniteQuery,
  useMutation,
  useQuery,
  useQueryClient,
} from "@tanstack/react-query";
import { api } from "./client";

export type FirmStatus = "active" | "suspended";
export type DomainSurface = "all" | "staff" | "client";
export type DomainStatus = "pending" | "active" | "disabled";
export type GrantScope = "read" | "write";

export interface PlatformFirm {
  id: string;
  name: string;
  /** DNS label used in the firm's web addresses. */
  slug: string;
  status: FirmStatus;
  is_platform_owner: boolean;
  /** Entitlement: the firm may hide "Powered by Inspro". */
  allow_hide_attribution: boolean;
  client_count: number;
  domain_count: number;
  created_at: string;
}

/** A firm's saved email From address and when the platform verified it. */
export interface FirmEmailFrom {
  address: string | null;
  verified_at: string | null;
}

export interface TenantDomain {
  id: string;
  hostname: string;
  surface: DomainSurface;
  is_primary: boolean;
  status: DomainStatus;
  verified_at: string | null;
  created_at: string;
}

export interface AccessGrant {
  id: string;
  user_id: string;
  user_email: string | null;
  broker_firm_id: string;
  firm_name: string;
  reason: string;
  scope: GrantScope;
  expires_at: string;
  revoked_at: string | null;
  created_at: string;
}

export interface PlatformAuditEntry {
  id: string;
  occurred_at: string;
  actor_user_id: string | null;
  actor_email: string | null;
  action: string;
  entity_type: string | null;
  entity_id: string | null;
  broker_firm_id: string | null;
  client_id: string | null;
  detail: unknown;
}

/** How a firm's staff sign in. At least one method stays enabled; the server
 *  answers 422 otherwise, or when the directory ID is not a GUID. Shared by
 *  the firm console (`/firm/sign-in-methods`) and the platform console. */
export interface SignInMethods {
  entra: {
    enabled: boolean;
    /** The firm's Microsoft Entra directory (tenant) ID. */
    tenant_id: string | null;
    /** Also ask for the Inspro authenticator after Microsoft sign-in. */
    require_platform_mfa: boolean;
  };
  /** Email and password; two-factor verification is always required. */
  local: { enabled: boolean };
  /** Where the firm's Microsoft administrator approves Inspro once; null until
   *  a directory ID is saved. */
  admin_consent_url: string | null;
}

export type SignInMethodsUpdate = Pick<SignInMethods, "entra" | "local">;

export const AUDIT_PAGE_SIZE = 50;

const firmsKey = ["platform", "firms"] as const;
const domainsKey = (firmId: string) => ["platform", "firms", firmId, "domains"] as const;
const signInMethodsKey = (firmId: string) =>
  ["platform", "firms", firmId, "sign-in-methods"] as const;
const grantsKey = ["platform", "access-grants"] as const;
const auditKey = ["platform", "audit"] as const;

// ── Firms ────────────────────────────────────────────────────────────────────

export function usePlatformFirms(enabled = true) {
  return useQuery({
    queryKey: firmsKey,
    queryFn: () => api.get<PlatformFirm[]>("/platform/firms"),
    enabled,
  });
}

/** Firm lists elsewhere (FirmPicker, the company picker's firm labels) read
 *  these, so a firm change refreshes all of them. */
function invalidateFirmLists(qc: ReturnType<typeof useQueryClient>) {
  void qc.invalidateQueries({ queryKey: firmsKey });
  void qc.invalidateQueries({ queryKey: ["admin", "broker-firms"] });
  void qc.invalidateQueries({ queryKey: auditKey });
}

export function useCreatePlatformFirm() {
  const qc = useQueryClient();
  return useMutation({
    /** `slug` omitted → the server derives it from the name. */
    mutationFn: (body: { name: string; slug?: string }) =>
      api.post<PlatformFirm>("/platform/firms", body),
    onSuccess: () => invalidateFirmLists(qc),
    meta: { localErrorHandling: true },
  });
}

export function usePatchPlatformFirm() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({
      id,
      ...body
    }: {
      id: string;
      name?: string;
      slug?: string;
      status?: FirmStatus;
      allow_hide_attribution?: boolean;
    }) => api.patch<PlatformFirm>(`/platform/firms/${id}`, body),
    onSuccess: (firm) => {
      qc.setQueryData<PlatformFirm[]>(firmsKey, (list) =>
        list?.map((f) => (f.id === firm.id ? firm : f)),
      );
      invalidateFirmLists(qc);
      void qc.invalidateQueries({ queryKey: ["me"] });
    },
  });
}

// ── Web addresses ────────────────────────────────────────────────────────────

export function usePlatformFirmDomains(firmId: string | undefined) {
  return useQuery({
    queryKey: domainsKey(firmId ?? ""),
    queryFn: () => api.get<TenantDomain[]>(`/platform/firms/${firmId}/domains`),
    enabled: Boolean(firmId),
  });
}

/** Domain writes can move `is_primary` off a sibling and change the firm's
 *  domain count, so the whole firm's list and the firm list both refresh. */
function invalidateDomains(qc: ReturnType<typeof useQueryClient>, firmId: string) {
  void qc.invalidateQueries({ queryKey: domainsKey(firmId) });
  void qc.invalidateQueries({ queryKey: firmsKey });
  void qc.invalidateQueries({ queryKey: auditKey });
}

export function useAddPlatformDomain(firmId: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: { hostname: string; surface: DomainSurface; is_primary?: boolean }) =>
      api.post<TenantDomain>(`/platform/firms/${firmId}/domains`, body),
    onSuccess: () => invalidateDomains(qc, firmId),
    meta: { localErrorHandling: true },
  });
}

export function usePatchPlatformDomain(firmId: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({
      id,
      ...body
    }: {
      id: string;
      status?: DomainStatus;
      surface?: DomainSurface;
      is_primary?: boolean;
    }) => api.patch<TenantDomain>(`/platform/domains/${id}`, body),
    onSuccess: () => invalidateDomains(qc, firmId),
  });
}

export function useDeletePlatformDomain(firmId: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => api.delete<void>(`/platform/domains/${id}`),
    onSuccess: () => invalidateDomains(qc, firmId),
  });
}

// ── Sign-in methods ──────────────────────────────────────────────────────────

export function usePlatformFirmSignInMethods(firmId: string) {
  return useQuery({
    queryKey: signInMethodsKey(firmId),
    queryFn: () => api.get<SignInMethods>(`/platform/firms/${firmId}/sign-in-methods`),
    enabled: Boolean(firmId),
  });
}

export function useUpdatePlatformFirmSignInMethods(firmId: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: SignInMethodsUpdate) =>
      api.put<SignInMethods>(`/platform/firms/${firmId}/sign-in-methods`, body),
    onSuccess: (saved) => {
      qc.setQueryData(signInMethodsKey(firmId), saved);
      void qc.invalidateQueries({ queryKey: auditKey });
      // The firm console's view of the same firm, when the admin is also
      // working in one of its companies.
      void qc.invalidateQueries({ queryKey: ["firm", "sign-in-methods"] });
    },
    meta: { localErrorHandling: true },
  });
}

// ── Email sender verification ────────────────────────────────────────────────

/** Recording a verification means the firm's From address passed SPF/DKIM at
 *  its domain; system emails then go from it. Clearing reverts them to the
 *  platform sender. */
function senderPath(firmId: string) {
  return `/platform/firms/${firmId}/brand/sender-verification`;
}

const senderKey = (firmId: string) => ["platform", "firms", firmId, "sender"] as const;

export function useFirmSender(firmId: string) {
  return useQuery({
    queryKey: senderKey(firmId),
    queryFn: () => api.get<FirmEmailFrom>(senderPath(firmId)),
    enabled: Boolean(firmId),
  });
}

function senderSaved(qc: ReturnType<typeof useQueryClient>, firmId: string, saved: FirmEmailFrom) {
  qc.setQueryData(senderKey(firmId), saved);
  void qc.invalidateQueries({ queryKey: senderKey(firmId) });
  void qc.invalidateQueries({ queryKey: auditKey });
  // The firm console's view, when the admin also works in that firm.
  void qc.invalidateQueries({ queryKey: ["firm", "brand"] });
}

/** `address` must be the firm's current From address (409
 *  `sender_address_mismatch` otherwise). */
export function useVerifyFirmSender(firmId: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (address: string) => api.post<FirmEmailFrom>(senderPath(firmId), { address }),
    onSuccess: (saved) => senderSaved(qc, firmId, saved),
    meta: { localErrorHandling: true },
  });
}

export function useClearFirmSender(firmId: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: () => api.delete<FirmEmailFrom>(senderPath(firmId)),
    onSuccess: (saved) => senderSaved(qc, firmId, saved),
    meta: { localErrorHandling: true },
  });
}

// ── Access grants ────────────────────────────────────────────────────────────

/** `active` = unexpired and unrevoked; otherwise the ended history. Active
 *  grants are polled so a lapsed grant leaves the list without a reload. */
export function useAccessGrants(active: boolean, enabled = true) {
  return useQuery({
    queryKey: [...grantsKey, active ? "active" : "history"],
    queryFn: () =>
      api.get<AccessGrant[]>(`/platform/access-grants?active=${active ? "true" : "false"}`),
    refetchInterval: active ? 60_000 : false,
    enabled,
  });
}

/** A grant opens (or closes) another firm's companies to this admin, so the
 *  company picker (`/me`) is refreshed with the grant lists. */
function invalidateGrants(qc: ReturnType<typeof useQueryClient>) {
  void qc.invalidateQueries({ queryKey: grantsKey });
  void qc.invalidateQueries({ queryKey: auditKey });
  void qc.invalidateQueries({ queryKey: ["me"] });
}

export function useCreateAccessGrant() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: {
      broker_firm_id: string;
      reason: string;
      scope: GrantScope;
      hours: number;
    }) => api.post<AccessGrant>("/platform/access-grants", body),
    onSuccess: () => invalidateGrants(qc),
    meta: { localErrorHandling: true },
  });
}

export function useRevokeAccessGrant() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: string) =>
      api.post<AccessGrant>(`/platform/access-grants/${id}/revoke`, {}),
    onSuccess: () => invalidateGrants(qc),
  });
}

// ── Audit trail ──────────────────────────────────────────────────────────────

/** Newest first, `AUDIT_PAGE_SIZE` per page; each further page starts before
 *  the oldest entry already loaded. A short page is the end of the trail. */
export function usePlatformAudit() {
  return useInfiniteQuery({
    queryKey: auditKey,
    initialPageParam: null as string | null,
    queryFn: ({ pageParam }) => {
      const params = new URLSearchParams({ limit: String(AUDIT_PAGE_SIZE) });
      if (pageParam) params.set("before", pageParam);
      return api.get<PlatformAuditEntry[]>(`/platform/audit?${params.toString()}`);
    },
    getNextPageParam: (lastPage) =>
      lastPage.length < AUDIT_PAGE_SIZE ? null : lastPage[lastPage.length - 1].occurred_at,
  });
}
