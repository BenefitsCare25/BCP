/** Firm console API — a broker firm's own profile, web addresses, staff
 *  sign-in methods and the log of platform access to its data.
 *
 *  Served to the firm's `firm_admin`, and to a `system_admin` for the firm of
 *  the selected company — so every key carries the active company: switching
 *  company can switch the firm a platform admin is looking at. */
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useSession } from "@/stores/session";
import { api } from "./client";
import type {
  AccessGrant,
  DomainSurface,
  FirmStatus,
  SignInMethods,
  SignInMethodsUpdate,
  TenantDomain,
} from "./platform";

export interface FirmProfile {
  id: string;
  name: string;
  slug: string;
  status: FirmStatus;
  is_platform_owner: boolean;
  allow_hide_attribution: boolean;
}

export function useFirmProfile(enabled = true) {
  const cid = useSession((s) => s.activeClientId);
  return useQuery({
    queryKey: ["firm", "profile", cid],
    queryFn: () => api.get<FirmProfile>("/firm/profile"),
    enabled,
  });
}

export function useFirmDomains(enabled = true) {
  const cid = useSession((s) => s.activeClientId);
  return useQuery({
    queryKey: ["firm", "domains", cid],
    queryFn: () => api.get<TenantDomain[]>("/firm/domains"),
    enabled,
  });
}

/** A request is stored as `pending`; the platform activates it once the
 *  broker's DNS validates. */
export function useRequestFirmDomain() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: { hostname: string; surface: DomainSurface }) =>
      api.post<TenantDomain>("/firm/domains", body),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ["firm", "domains"] });
      // The platform console's view of the same firm, when this is a platform
      // admin acting in the firm.
      void qc.invalidateQueries({ queryKey: ["platform", "firms"] });
    },
    meta: { localErrorHandling: true },
  });
}

/** How the firm's staff sign in: Microsoft 365 and/or email and password. */
export function useFirmSignInMethods(enabled = true) {
  const cid = useSession((s) => s.activeClientId);
  return useQuery({
    queryKey: ["firm", "sign-in-methods", cid],
    queryFn: () => api.get<SignInMethods>("/firm/sign-in-methods"),
    enabled,
  });
}

export function useUpdateFirmSignInMethods() {
  const qc = useQueryClient();
  const cid = useSession((s) => s.activeClientId);
  return useMutation({
    mutationFn: (body: SignInMethodsUpdate) =>
      api.put<SignInMethods>("/firm/sign-in-methods", body),
    onSuccess: (saved) => {
      qc.setQueryData(["firm", "sign-in-methods", cid], saved);
      // The platform console's copy, when a platform admin made the change.
      void qc.invalidateQueries({ queryKey: ["platform", "firms"] });
    },
    meta: { localErrorHandling: true },
  });
}

/** Who from the platform opened this firm's data, why, and for how long. */
export function useFirmPlatformAccess(enabled = true) {
  const cid = useSession((s) => s.activeClientId);
  return useQuery({
    queryKey: ["firm", "platform-access", cid],
    queryFn: () => api.get<AccessGrant[]>("/firm/platform-access"),
    enabled,
  });
}
