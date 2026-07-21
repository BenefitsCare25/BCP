/** Broker claim-review queue hooks (member claim hooks live in api/portal.ts). */
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "@/api/client";
import { isNotFoundError } from "@/lib/errors";
import { useSession } from "@/stores/session";
import type { Utilization } from "@/types";

export interface StoredDocumentMeta {
  id: string;
  file_name: string;
  mime_type: string | null;
  size_bytes: number;
  sha256: string;
  created_at: string;
}

export interface ClaimAIReviewSummary {
  id: string;
  status: "pending" | "complete" | "error";
  verdict: "clean" | "flagged" | null;
  confidence: number | null;
  summary: string | null;
  created_at: string;
}

export interface FieldComparison {
  field_name: string;
  claim_value: string | null;
  document_value: string | null;
  status: "MATCH" | "MISMATCH" | "MISSING_IN_PDF" | "MISSING_ON_PAGE" | "UNCERTAIN";
  confidence: number;
  notes?: string | null;
  vision_verified?: boolean;
}

export interface RuleResult {
  rule: string;
  status: "pass" | "fail" | "warning" | "not_applicable";
  source: "deterministic" | "ai";
  evidence: string;
}

export interface VisionCheck {
  field_name: string;
  question: string;
  document_id: string;
  file_name: string;
  verdict: "CONFIRMED" | "REFUTED" | "UNCERTAIN";
  explanation: string;
}

export interface ClaimAIReview extends ClaimAIReviewSummary {
  extractions: { document_id: string; file_name: string; document_type: string }[] | null;
  field_comparisons: FieldComparison[] | null;
  rule_results: RuleResult[] | null;
  vision_checks: VisionCheck[] | null;
  model: string | null;
  input_tokens: number | null;
  output_tokens: number | null;
  cost_estimate_usd: number | null;
  error_detail: string | null;
  superseded: boolean;
}

export interface BrokerClaim {
  id: string;
  client_id: string;
  policy_year_id: string;
  employee_id: string;
  staff_id: string | null;
  employee_name: string | null;
  claim_kind: "insured" | "flex";
  product_code: string | null;
  /** Legacy claims only — the Benefit picker was removed from the form. */
  benefit_key: string | null;
  flex_category_name: string | null;
  claim_type: string;
  sub_type: string | null;
  referral_document_id: string | null;
  referral_document: StoredDocumentMeta | null;
  referral_not_applicable: boolean;
  incurred_date: string;
  provider_name: string | null;
  invoice_number: string | null;
  diagnosis: string | null;
  remarks: string | null;
  amount_claimed: number;
  currency: string;
  /** Claimed amount converted to the policy currency (null = same currency). */
  amount_converted?: number | null;
  amount_approved: number | null;
  status: string;
  dependant_id: string | null;
  dependant_name: string | null;
  submitted_at: string | null;
  decided_at: string | null;
  decision_notes: string | null;
  created_at: string;
  documents: StoredDocumentMeta[];
  ai_review: ClaimAIReviewSummary | null;
  /** Remaining amount in the claim's tightest utilization bucket — detail
   *  endpoint only (null = no numeric limit known / list payload). */
  remaining_limit?: number | null;
}

export interface BrokerClaimList {
  total: number;
  offset: number;
  limit: number;
  items: BrokerClaim[];
}

export function useBrokerClaims(
  policyYearId: string | undefined,
  status: string,
  offset: number,
  limit: number,
) {
  const cid = useSession((s) => s.activeClientId);
  return useQuery({
    queryKey: ["claims", cid, policyYearId, status, offset, limit],
    queryFn: () => {
      const params = new URLSearchParams({
        policy_year_id: policyYearId!,
        offset: String(offset),
        limit: String(limit),
      });
      if (status) params.set("status", status);
      return api.get<BrokerClaimList>(`/claims?${params.toString()}`);
    },
    enabled: !!policyYearId,
  });
}

/** Latest AI review of a claim; resolves to null when none exists (404).
 * Other failures REJECT so the panel can show a real error state instead of
 * a misleading "no review yet". `refetchInterval` lets the panel poll while a
 * review is running. */
/** Single-claim detail — carries `remaining_limit`, which the list omits. */
export function useBrokerClaimDetail(claimId: string | null) {
  const cid = useSession((s) => s.activeClientId);
  return useQuery({
    queryKey: ["claim-detail", cid, claimId],
    queryFn: () => api.get<BrokerClaim>(`/claims/${claimId}`),
    enabled: !!claimId,
    meta: { localErrorHandling: true },
  });
}

export function useClaimReview(
  claimId: string | null,
  refetchInterval?: number | false,
) {
  const cid = useSession((s) => s.activeClientId);
  return useQuery({
    queryKey: ["claim-review", cid, claimId],
    queryFn: async () => {
      try {
        return await api.get<ClaimAIReview>(`/claims/${claimId}/review`);
      } catch (e) {
        if (isNotFoundError(e)) return null; // no review yet — empty state
        throw e;
      }
    },
    enabled: !!claimId,
    retry: false,
    refetchInterval: refetchInterval ?? false,
    meta: { localErrorHandling: true },
  });
}

/** Broker view of one employee's claim usage vs limits. */
export function useEmployeeUtilization(employeeId: string | null) {
  const cid = useSession((s) => s.activeClientId);
  return useQuery({
    queryKey: ["employee-utilization", cid, employeeId],
    queryFn: () => api.get<Utilization>(`/employees/${employeeId}/utilization`),
    enabled: !!employeeId,
  });
}

export interface ClaimDecisionInput {
  claimId: string;
  action: "approve" | "reject" | "needs_info";
  note?: string;
  approvedAmount?: number;
  /** Approve past the remaining limit (after a 409 `limit_exceeded`). */
  acknowledge?: boolean;
}

export function useDecideClaim() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (input: ClaimDecisionInput) =>
      api.post<BrokerClaim>(`/claims/${input.claimId}/decision`, {
        action: input.action,
        note: input.note ?? null,
        approved_amount: input.approvedAmount ?? null,
        acknowledge: input.acknowledge ?? false,
      }),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ["claims"] });
      // Approval changes the employee's usage-vs-limits view.
      void qc.invalidateQueries({ queryKey: ["employee-utilization"] });
      void qc.invalidateQueries({ queryKey: ["claim-detail"] });
    },
    meta: { localErrorHandling: true },
  });
}

export function useRerunReview() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (claimId: string) =>
      api.post<BrokerClaim>(`/claims/${claimId}/rerun-review`, {}),
    onSuccess: (_data, claimId) => {
      void qc.invalidateQueries({ queryKey: ["claims"] });
      void qc.invalidateQueries({ queryKey: ["claim-review"], exact: false });
      void qc.invalidateQueries({ queryKey: ["claim-review", undefined, claimId] });
    },
    meta: { localErrorHandling: true },
  });
}

export async function downloadClaimDocument(
  claimId: string,
  doc: StoredDocumentMeta,
): Promise<void> {
  const blob = await api.download(`/claims/${claimId}/documents/${doc.id}/download`);
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = doc.file_name;
  a.click();
  setTimeout(() => URL.revokeObjectURL(url), 10_000);
}
