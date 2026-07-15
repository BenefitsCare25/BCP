/** Typed calls + query hooks for the employee portal surface. */
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import type {
  ElectionIn,
  EnrollmentDetail,
  EnrollmentOptions,
  EnrollmentWindow,
} from "@/api/enrollment";
import {
  clinicSearchQuery,
  type ClinicSearch,
  type ClinicSearchParams,
} from "@/api/panelListings";
import { portalApi } from "@/api/portalClient";
import type { BenefitStatement, Dependant, Utilization } from "@/types";
import type { PortalMember } from "@/stores/portalSession";
import { usePortalSession } from "@/stores/portalSession";

export interface OtpRequestResult {
  status: string;
  /** Populated only in local dev (mock auth) so sign-in works without email. */
  debug_code: string | null;
}

export interface OtpVerifyResult {
  token: string;
  expires_at: string;
  member: PortalMember;
}

export interface PortalMe {
  member: PortalMember;
  employee: { id: string; staff_id: string; employee_name: string | null } | null;
  policy_year: {
    id: string;
    year: number;
    start_date: string;
    end_date: string;
  } | null;
  flex_eligible: boolean;
  /** True while an enrollment window is open and in-period — drives the
   * "Enrollment open" call-to-action in the portal shell. */
  enrollment_open: boolean;
}

export function useRequestOtp() {
  return useMutation({
    mutationFn: (email: string) =>
      portalApi.postPublic<OtpRequestResult>("/portal/auth/request-code", {
        email,
      }),
    meta: { localErrorHandling: true },
  });
}

export function useVerifyOtp() {
  const setSession = usePortalSession((s) => s.setSession);
  return useMutation({
    mutationFn: (input: { email: string; code: string }) =>
      portalApi.postPublic<OtpVerifyResult>("/portal/auth/verify", input),
    onSuccess: (out) => setSession(out.token, out.expires_at, out.member),
    meta: { localErrorHandling: true },
  });
}

export function usePortalMe() {
  return useQuery({
    queryKey: ["portal", "me"],
    queryFn: () => portalApi.get<PortalMe>("/portal/me"),
  });
}

export function usePortalStatement() {
  return useQuery({
    queryKey: ["portal", "benefit-statement"],
    queryFn: () => portalApi.get<BenefitStatement>("/portal/benefit-statement"),
    // "No active coverage" (404) renders as an inline empty state, not a toast.
    meta: { localErrorHandling: true },
    retry: false,
  });
}

export function usePortalUtilization() {
  return useQuery({
    queryKey: ["portal", "utilization"],
    queryFn: () => portalApi.get<Utilization>("/portal/utilization"),
    meta: { localErrorHandling: true },
    retry: false,
  });
}

export function usePortalDependants() {
  return useQuery({
    queryKey: ["portal", "dependants"],
    queryFn: () => portalApi.get<Dependant[]>("/portal/dependants"),
    meta: { localErrorHandling: true },
    retry: false,
  });
}

// ── Clinic locator ────────────────────────────────────────────────────────────

export function usePortalClinics(params: ClinicSearchParams) {
  return useQuery({
    queryKey: ["portal", "clinics", params],
    queryFn: () =>
      portalApi.get<ClinicSearch>(`/portal/clinics${clinicSearchQuery(params)}`),
    meta: { localErrorHandling: true },
    retry: false,
    // Keep the previous page while filters/pagination change so the list
    // doesn't flash empty on every keystroke.
    placeholderData: (prev) => prev,
  });
}

// ── Enrollment ────────────────────────────────────────────────────────────────

/** The member enrollment surface — all None when no window is open. The same
 * shape is served to the broker's employee-view preview. */
export interface PortalEnrollmentData {
  window: EnrollmentWindow | null;
  enrollment: EnrollmentDetail | null;
  options: EnrollmentOptions | null;
}

export function usePortalEnrollment() {
  return useQuery({
    queryKey: ["portal", "enrollment"],
    queryFn: () => portalApi.get<PortalEnrollmentData>("/portal/enrollment"),
    meta: { localErrorHandling: true },
    retry: false,
  });
}

export function useSaveMyElections() {
  const qc = usePortalQueryInvalidator();
  return useMutation({
    mutationFn: (elections: ElectionIn[]) =>
      portalApi.put<EnrollmentDetail>("/portal/enrollment/elections", {
        elections,
      }),
    onSuccess: qc,
    meta: { localErrorHandling: true },
  });
}

export function useSetMyLeave() {
  const qc = usePortalQueryInvalidator();
  return useMutation({
    mutationFn: (input: { action: string; days: number }) =>
      portalApi.put<EnrollmentDetail>("/portal/enrollment/leave", input),
    onSuccess: qc,
    meta: { localErrorHandling: true },
  });
}

export function useSubmitMyEnrollment() {
  const qc = usePortalQueryInvalidator();
  return useMutation({
    mutationFn: (acknowledgeUnpriced: boolean) =>
      portalApi.post<EnrollmentDetail>("/portal/enrollment/submit", {
        acknowledge_unpriced: acknowledgeUnpriced,
      }),
    onSuccess: qc,
    meta: { localErrorHandling: true },
  });
}

// ── Claims ────────────────────────────────────────────────────────────────────

export interface PortalClaimDocument {
  id: string;
  file_name: string;
  mime_type: string | null;
  size_bytes: number;
  sha256: string;
  created_at: string;
}

export interface PortalClaim {
  id: string;
  claim_kind: "insured" | "flex";
  product_code: string | null;
  benefit_key: string | null;
  flex_category_name: string | null;
  claim_type: string;
  incurred_date: string;
  provider_name: string | null;
  invoice_number: string | null;
  diagnosis: string | null;
  remarks: string | null;
  amount_claimed: number;
  currency: string;
  amount_converted: number | null;
  amount_approved: number | null;
  status: string;
  dependant_id: string | null;
  submitted_at: string | null;
  decided_at: string | null;
  decision_notes: string | null;
  created_at: string;
  documents: PortalClaimDocument[];
}

export interface PortalClaimList {
  total: number;
  offset: number;
  limit: number;
  items: PortalClaim[];
}

export interface ClaimCreateInput {
  claim_kind: "insured" | "flex";
  product_code?: string | null;
  benefit_key?: string | null;
  flex_category_name?: string | null;
  claim_type: string;
  incurred_date: string;
  provider_name?: string | null;
  invoice_number?: string | null;
  diagnosis?: string | null;
  remarks?: string | null;
  amount_claimed: number;
  currency?: string;
  dependant_id?: string | null;
}

export interface InsuredClaimOption {
  product_code: string;
  product_name: string | null;
  plan_code: string | null;
  annual_policy_limit: string | null;
  benefit_items: string[];
  covers_dependants: boolean;
  covered_dependant_ids: string[];
}

export interface CoverageOptions {
  policy_year_start: string;
  policy_year_end: string;
  insured: InsuredClaimOption[];
  flex: {
    currency: string | null;
    wallet_amount: number | null;
    flex_balance: number | null;
    categories: { name: string; sub_limit: number | null; note: string | null }[];
  } | null;
  dependants: { id: string; name: string | null; relationship: string | null }[];
}

export function useCoverageOptions() {
  return useQuery({
    queryKey: ["portal", "coverage-options"],
    queryFn: () => portalApi.get<CoverageOptions>("/portal/coverage-options"),
    meta: { localErrorHandling: true },
    retry: false,
  });
}

export function usePortalClaims() {
  return useQuery({
    queryKey: ["portal", "claims"],
    queryFn: () => portalApi.get<PortalClaimList>("/portal/claims"),
    meta: { localErrorHandling: true },
    retry: false,
  });
}

export function usePortalClaim(claimId: string | null) {
  return useQuery({
    queryKey: ["portal", "claims", claimId],
    queryFn: () => portalApi.get<PortalClaim>(`/portal/claims/${claimId}`),
    enabled: Boolean(claimId),
    meta: { localErrorHandling: true },
    retry: false,
  });
}

export function useCreateClaim() {
  return useMutation({
    mutationFn: (input: ClaimCreateInput) =>
      portalApi.post<PortalClaim>("/portal/claims", input),
    meta: { localErrorHandling: true },
  });
}

export function useUploadClaimDocument() {
  return useMutation({
    mutationFn: (input: { claimId: string; file: File }) => {
      const fd = new FormData();
      fd.append("file", input.file);
      return portalApi.upload<PortalClaimDocument>(
        `/portal/claims/${input.claimId}/documents`,
        fd,
      );
    },
    meta: { localErrorHandling: true },
  });
}

export function useSubmitClaim() {
  const qc = usePortalQueryInvalidator();
  return useMutation({
    mutationFn: (claimId: string) =>
      portalApi.post<PortalClaim>(`/portal/claims/${claimId}/submit`, {}),
    onSuccess: qc,
    meta: { localErrorHandling: true },
  });
}

export function useDeleteDraftClaim() {
  const qc = usePortalQueryInvalidator();
  return useMutation({
    mutationFn: (claimId: string) =>
      portalApi.delete<void>(`/portal/claims/${claimId}`),
    onSuccess: qc,
  });
}

// ── Dependant self-add ────────────────────────────────────────────────────────

export interface PortalDependantCreateInput {
  name: string;
  relationship: string;
  dob?: string | null;
  gender?: string | null;
  id_no?: string | null;
}

export function useAddDependant() {
  const qc = usePortalQueryInvalidator();
  return useMutation({
    mutationFn: (input: PortalDependantCreateInput) =>
      portalApi.post<Dependant>("/portal/dependants", input),
    onSuccess: qc,
    meta: { localErrorHandling: true },
  });
}

export function useUploadDependantProof() {
  return useMutation({
    mutationFn: (input: { dependantId: string; file: File }) => {
      const fd = new FormData();
      fd.append("file", input.file);
      return portalApi.upload<PortalClaimDocument>(
        `/portal/dependants/${input.dependantId}/documents`,
        fd,
      );
    },
    meta: { localErrorHandling: true },
  });
}

function usePortalQueryInvalidator() {
  const queryClient = useQueryClient();
  return () => {
    void queryClient.invalidateQueries({ queryKey: ["portal"] });
  };
}
