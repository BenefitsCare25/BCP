/** Delegated-claim contract for the company HR surface. */
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useRef } from "react";
import { hrApi } from "@/api/hrClient";
import type {
  ClaimCreateInput,
  CoverageOptions,
  DocSlot,
  FxQuote,
} from "@/api/portal";

export interface HrEmployee {
  id: string;
  name: string | null;
  staff_id: string;
  period: string;
}

export interface HrEmployeeList {
  items: HrEmployee[];
  total: number;
}

export interface HrClaimDocument {
  id: string;
  file_name: string;
  doc_type: string | null;
  /** HR may take this file back: anything on a draft; once sent, only a file
   *  this HR user uploaded, before a decision, that is not the last copy of
   *  required evidence. */
  can_remove: boolean;
}

/** What HR typed into a draft, served only while it is a draft so the edit
 *  form can open on it. */
export interface HrClaimDraft {
  revision: number;
  staff_id: string | null;
  product_code: string | null;
  flex_category_name: string | null;
  sub_type: string | null;
  visit_type: string | null;
  admission_date: string | null;
  discharge_date: string | null;
  doctor_name: string | null;
  diagnosis: string | null;
  remarks: string | null;
  related_claim_id: string | null;
  referral: { id: string; file_name: string } | null;
}

export interface HrClaim {
  id: string;
  employee_id: string;
  employee_name: string | null;
  policy_year_id: string;
  claim_ref: string | null;
  claim_kind: "insured" | "flex";
  claim_type: string;
  provider_name: string | null;
  invoice_number: string | null;
  status: string;
  incurred_date: string;
  amount_claimed: number;
  currency: string;
  created_by_user_id: string;
  submitted_by_name: string | null;
  submitted_by_email: string | null;
  submission_channel: "hr";
  can_add_evidence: boolean;
  can_submit: boolean;
  /** A draft: HR may still discard it or take a file back off it. */
  can_discard: boolean;
  /** A draft: HR may still correct its details. */
  can_edit: boolean;
  /** What the broker asked for, served only while the claim is `needs_info`. */
  decision_notes: string | null;
  created_at: string;
  submitted_at: string | null;
  doc_slots: DocSlot[];
  documents: HrClaimDocument[];
  draft: HrClaimDraft | null;
}

export type HrClaimCreateInput = ClaimCreateInput & { employee_id: string };

/** A correction to an HR draft: the create fields HR states, plus the
 *  revision the form opened on (a mismatch 409s `claim_amended`). */
export type HrClaimAmendInput = Partial<
  Omit<ClaimCreateInput, "intake_id" | "intake_claim_index">
> & { expected_revision: number };

export function useHrEmployees(query: string) {
  return useQuery({
    queryKey: ["hr", "claim-employees", query.trim()],
    queryFn: () =>
      hrApi.get<HrEmployeeList>(
        `/hr/claims/employees?q=${encodeURIComponent(query.trim())}`,
      ),
    staleTime: 30_000,
    placeholderData: (previous) => previous,
    meta: { localErrorHandling: true },
  });
}

export function useHrClaims({
  query = "",
  offset = 0,
  limit = 20,
}: {
  query?: string;
  offset?: number;
  limit?: number;
} = {}) {
  const term = query.trim();
  return useQuery({
    queryKey: ["hr", "claims", { term, offset, limit }],
    queryFn: () => {
      const params = new URLSearchParams({
        q: term,
        offset: String(offset),
        limit: String(limit),
      });
      return hrApi.get<{ items: HrClaim[]; total: number }>(
        `/hr/claims?${params.toString()}`,
      );
    },
    placeholderData: (previous) => previous,
    meta: { localErrorHandling: true },
    retry: false,
  });
}

export function useHrFxQuote(
  currency: string,
  policyCurrency: string,
  amount: number | null,
  on: string,
) {
  const enabled =
    Boolean(currency) && currency !== policyCurrency && Boolean(on) && amount !== null;
  return useQuery({
    queryKey: ["hr", "fx-quote", currency, amount, on],
    queryFn: () => {
      const params = new URLSearchParams({
        currency,
        amount: String(amount),
        on,
      });
      return hrApi.get<FxQuote>(`/hr/claims/fx-quote?${params.toString()}`);
    },
    enabled,
    staleTime: 5 * 60 * 1000,
    retry: false,
    meta: { localErrorHandling: true },
  });
}

export function useHrClaim(claimId: string) {
  return useQuery({
    queryKey: ["hr", "claims", claimId],
    queryFn: () => hrApi.get<HrClaim>(`/hr/claims/${claimId}`),
    enabled: !!claimId,
    meta: { localErrorHandling: true },
    retry: false,
  });
}

export function useHrCoverageOptions(employeeId: string | null) {
  return useQuery({
    queryKey: ["hr", "claim-options", employeeId],
    queryFn: () =>
      hrApi.get<CoverageOptions>(`/hr/claims/employees/${employeeId}/options`),
    enabled: !!employeeId,
    meta: { localErrorHandling: true },
    retry: false,
  });
}

export function useCreateHrClaim() {
  const key = useRef(crypto.randomUUID());
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (input: HrClaimCreateInput) =>
      hrApi.postWithHeaders<HrClaim>("/hr/claims", input, {
        "Idempotency-Key": key.current,
      }),
    onSuccess: (claim) => {
      key.current = crypto.randomUUID();
      queryClient.setQueryData(["hr", "claims", claim.id], claim);
      void queryClient.invalidateQueries({ queryKey: ["hr", "claims"] });
    },
    meta: { localErrorHandling: true },
  });
}

export interface HrReferral {
  id: string;
  file_name: string;
}

export function useUploadHrReferral() {
  return useMutation({
    mutationFn: (input: {
      employeeId: string;
      file: File;
      issuedOn: string | null;
    }) => {
      const body = new FormData();
      body.append("file", input.file);
      if (input.issuedOn) body.append("issued_on", input.issuedOn);
      return hrApi.upload<HrReferral>(
        `/hr/claims/employees/${input.employeeId}/referrals`,
        body,
      );
    },
    meta: { localErrorHandling: true },
  });
}

export function useUploadHrClaimDocument() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (input: { claimId: string; file: File; docType: string }) => {
      const body = new FormData();
      body.append("file", input.file);
      body.append("doc_type", input.docType);
      return hrApi.upload<HrClaim>(
        `/hr/claims/${input.claimId}/documents`,
        body,
      );
    },
    onSuccess: (claim) => {
      queryClient.setQueryData(["hr", "claims", claim.id], claim);
      void queryClient.invalidateQueries({ queryKey: ["hr", "claims"] });
    },
    meta: { localErrorHandling: true },
  });
}

function useStoreHrClaim() {
  const queryClient = useQueryClient();
  return (claim: HrClaim) => {
    queryClient.setQueryData(["hr", "claims", claim.id], claim);
    void queryClient.invalidateQueries({ queryKey: ["hr", "claims"] });
  };
}

export function useAmendHrClaim() {
  const store = useStoreHrClaim();
  return useMutation({
    mutationFn: (input: { claimId: string; patch: HrClaimAmendInput }) =>
      hrApi.patch<HrClaim>(`/hr/claims/${input.claimId}`, input.patch),
    onSuccess: store,
    meta: { localErrorHandling: true },
  });
}

export function useRemoveHrClaimDocument() {
  const store = useStoreHrClaim();
  return useMutation({
    mutationFn: (input: { claimId: string; documentId: string }) =>
      hrApi.delete<HrClaim>(
        `/hr/claims/${input.claimId}/documents/${input.documentId}`,
      ),
    onSuccess: store,
    meta: { localErrorHandling: true },
  });
}

export function useDiscardHrClaim() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (claimId: string) => hrApi.delete<void>(`/hr/claims/${claimId}`),
    onSuccess: (_, claimId) => {
      queryClient.removeQueries({ queryKey: ["hr", "claims", claimId], exact: true });
      void queryClient.invalidateQueries({ queryKey: ["hr", "claims"] });
    },
    meta: { localErrorHandling: true },
  });
}

/** Accept the converted figure submit asked about (409
 *  `fx_confirmation_required`). The server stamps it only if its own figure
 *  still matches the one HR was shown. */
export function useConfirmHrConversion() {
  const store = useStoreHrClaim();
  return useMutation({
    mutationFn: (input: { claimId: string; convertedAmount: number }) =>
      hrApi.post<HrClaim>(`/hr/claims/${input.claimId}/confirm-conversion`, {
        converted_amount: input.convertedAmount,
      }),
    onSuccess: store,
    meta: { localErrorHandling: true },
  });
}

export function useSubmitHrClaim() {
  const keys = useRef(new Map<string, string>());
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (claimId: string) => {
      const key = keys.current.get(claimId) ?? crypto.randomUUID();
      keys.current.set(claimId, key);
      return hrApi.postWithHeaders<HrClaim>(
        `/hr/claims/${claimId}/submit`,
        {},
        { "Idempotency-Key": key },
      );
    },
    onSuccess: (claim) => {
      keys.current.delete(claim.id);
      queryClient.setQueryData(["hr", "claims", claim.id], claim);
      void queryClient.invalidateQueries({ queryKey: ["hr", "claims"] });
    },
    meta: { localErrorHandling: true },
  });
}
