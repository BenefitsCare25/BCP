/** Broker-side "employee view" preview — read-only mirrors of the /portal/*
 * data, fetched with the BROKER client (MSAL + X-Inspro-Client), never a
 * member token. Same response shapes as the portal hooks in api/portal.ts. */
import { useQuery } from "@tanstack/react-query";
import { api } from "@/api/client";
import type {
  PortalAccess,
  PortalClaim,
  PortalClaimList,
  PortalEnrollmentData,
} from "@/api/portal";
import type { ClaimMessage, ConversationList } from "@/api/portalMessages";
import type { Enquiry } from "@/api/portalEnquiries";
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
  /** Mirrors PortalMe.access. The preview endpoints are deliberately UNGATED —
   *  a broker settling a leaver's last claim must be able to read their
   *  screens — so this exists to render the same banner and hide the same
   *  tabs. Without consuming it, a broker previewing a leaver sees Card,
   *  Clinics and Enrolment while the member's own shell has hidden all three,
   *  which is the divergence the parity rule exists to prevent. */
  access: PortalAccess | null;
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

/** `limit` mirrors `usePortalClaims` — see the note there. The preview must
 * show the member's screen, and a broker reading a truncated ledger would be
 * reading a different one. */
export function usePreviewClaims(employeeId: string | null) {
  return usePreviewQuery<PortalClaimList>(
    employeeId,
    "/claims?limit=200",
    "claims",
  );
}

/** ONE claim, as its own claimant reads it — the mirror of `usePortalClaim`.
 *
 * The broker's own claim record (`api/claims.ts`) is a DIFFERENT shape carrying
 * assessor fields, so it can never be substituted here: the frame's job is to
 * show the member's screen, and half of what makes that screen the member's is
 * what it leaves out. */
export function usePreviewClaim(
  employeeId: string | null,
  claimId: string | null,
) {
  const cid = useSession((s) => s.activeClientId);
  return useQuery({
    queryKey: ["portal-preview", "claim", employeeId, claimId, cid],
    queryFn: () =>
      api.get<PortalClaim>(
        `/employees/${employeeId}/portal-preview/claims/${claimId}`,
      ),
    enabled: Boolean(employeeId && claimId),
    meta: { localErrorHandling: true },
    retry: false,
  });
}

/** The member's conversations as the MEMBER sees them — the preview endpoint
 * runs the member serializer over the same projection, so a broker's name never
 * appears here and neither does a thread the member cannot see. */
export function usePreviewConversations(employeeId: string | null) {
  return usePreviewQuery<ConversationList>(
    employeeId,
    "/conversations",
    "conversations",
  );
}

/** ONE question, and its thread, as the MEMBER reads them — the preview
 * endpoints run the member serializer over the same loaders, so a broker's name
 * never appears and another employee's question 404s. */
export function usePreviewEnquiry(
  employeeId: string | null,
  enquiryId: string | null,
) {
  const cid = useSession((s) => s.activeClientId);
  return useQuery({
    queryKey: ["portal-preview", "enquiry", employeeId, enquiryId, cid],
    queryFn: () =>
      api.get<Enquiry>(
        `/employees/${employeeId}/portal-preview/enquiries/${enquiryId}`,
      ),
    enabled: Boolean(employeeId && enquiryId),
    meta: { localErrorHandling: true },
    retry: false,
  });
}

export function usePreviewEnquiryMessages(
  employeeId: string | null,
  enquiryId: string | null,
) {
  const cid = useSession((s) => s.activeClientId);
  return useQuery({
    queryKey: ["portal-preview", "enquiry-messages", employeeId, enquiryId, cid],
    queryFn: () =>
      api.get<ClaimMessage[]>(
        `/employees/${employeeId}/portal-preview/enquiries/${enquiryId}/messages`,
      ),
    enabled: Boolean(employeeId && enquiryId),
    meta: { localErrorHandling: true },
    retry: false,
  });
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
