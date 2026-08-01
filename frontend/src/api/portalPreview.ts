/** Broker-side "employee view" preview — read-only mirrors of the /portal/*
 * data, fetched with the BROKER client (MSAL + X-Inspro-Client), never a
 * member token. Same response shapes as the portal hooks in api/portal.ts. */
import { useQuery } from "@tanstack/react-query";
import { api } from "@/api/client";
import type { PortalClaimList, PortalEnrollmentData } from "@/api/portal";
import type { ClaimMessage, ClaimMessageList } from "@/api/portalMessages";
import type { MemberAccount } from "@/api/memberAccounts";
import type { MemberCards } from "@/api/panelCards";
import {
  clinicSearchQuery,
  type ClinicSearch,
  type ClinicSearchParams,
} from "@/api/panelListings";
import { useSession } from "@/stores/session";
import type { BenefitStatement, Dependant, Utilization } from "@/types";

export interface PortalPreviewContext {
  employee: { id: string; staff_id: string; employee_name: string | null };
  policy_year: {
    id: string;
    year: number;
    start_date: string;
    end_date: string;
  } | null;
  flex_eligible: boolean;
  /** False when previewing a draft/closed year — the live portal only ever
   * shows the client's active policy year. */
  is_active_policy_year: boolean;
  member_account: MemberAccount | null;
  /** Mirrors PortalMe.enrollment_open for the previewed employee. */
  enrollment_open: boolean;
}

function usePreviewQuery<T>(
  employeeId: string | null,
  suffix: string,
  key: string,
) {
  const cid = useSession((s) => s.activeClientId);
  return useQuery({
    queryKey: ["portal-preview", key, employeeId, cid],
    queryFn: () =>
      api.get<T>(`/employees/${employeeId}/portal-preview${suffix}`),
    enabled: Boolean(employeeId),
    // "No coverage" style 404s render as inline empty states, not toasts.
    meta: { localErrorHandling: true },
    retry: false,
  });
}

export function usePortalPreviewContext(employeeId: string | null) {
  return usePreviewQuery<PortalPreviewContext>(employeeId, "", "context");
}

export function usePreviewStatement(employeeId: string | null) {
  return usePreviewQuery<BenefitStatement>(
    employeeId,
    "/benefit-statement",
    "statement",
  );
}

export function usePreviewUtilization(employeeId: string | null) {
  return usePreviewQuery<Utilization>(employeeId, "/utilization", "utilization");
}

export function usePreviewDependants(employeeId: string | null) {
  return usePreviewQuery<Dependant[]>(employeeId, "/dependants", "dependants");
}

export function usePreviewClaims(employeeId: string | null) {
  return usePreviewQuery<PortalClaimList>(employeeId, "/claims", "claims");
}

/** The member's inbox as the MEMBER sees it — the preview endpoint runs the
 * member serializer, so a broker's name never appears here either. */
export function usePreviewMessages(employeeId: string | null) {
  return usePreviewQuery<ClaimMessageList>(employeeId, "/messages", "messages");
}

export function usePreviewClaimMessages(
  employeeId: string | null,
  claimId: string | null,
) {
  const cid = useSession((s) => s.activeClientId);
  return useQuery({
    queryKey: ["portal-preview", "claim-messages", employeeId, claimId, cid],
    queryFn: () =>
      api.get<ClaimMessage[]>(
        `/employees/${employeeId}/portal-preview/claims/${claimId}/messages`,
      ),
    enabled: Boolean(employeeId && claimId),
    meta: { localErrorHandling: true },
    retry: false,
  });
}

export function usePreviewEnrollment(employeeId: string | null) {
  return usePreviewQuery<PortalEnrollmentData>(
    employeeId,
    "/enrollment",
    "enrollment",
  );
}

export function usePreviewCards(employeeId: string | null) {
  return usePreviewQuery<MemberCards>(employeeId, "/cards", "cards");
}

export function usePreviewClinics(
  employeeId: string | null,
  params: ClinicSearchParams,
) {
  const cid = useSession((s) => s.activeClientId);
  return useQuery({
    queryKey: ["portal-preview", "clinics", employeeId, params, cid],
    queryFn: () =>
      api.get<ClinicSearch>(
        `/employees/${employeeId}/portal-preview/clinics${clinicSearchQuery(params)}`,
      ),
    enabled: Boolean(employeeId),
    meta: { localErrorHandling: true },
    retry: false,
    placeholderData: (prev) => prev,
  });
}
