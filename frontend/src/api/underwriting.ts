import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "@/api/client";
import { useSession } from "@/stores/session";

// Per-product decision line under a review.
export interface UnderwritingCaseLine {
  id: string;
  product_id: string;
  product_code: string;
  product_name: string;
  requested_si: number;
  guaranteed_si: number;
  pending_si: number;
  accepted_si: number;
  status:
    | "pending"
    | "approved_standard"
    | "approved_substandard"
    | "rejected"
    | "postponed"
    | "closed";
  decided_on: string | null;
  remarks: string | null;
}

// One underwriting case per (life, insurer): workflow status + requirements
// with the per-product decisions nested.
export interface UnderwritingReview {
  id: string;
  insurer: string;
  subject_type: "employee" | "dependant";
  subject_name: string | null;
  relationship: string;
  staff_id: string | null;
  identification_no: string | null;
  status:
    | "pending_requirements"
    | "pending_employee"
    | "pending_insurer"
    | "pending_hr"
    | "completed"
    | "cancelled";
  requirements: string | null;
  cases: UnderwritingCaseLine[];
}

export interface UnderwritingQueue {
  total: number;
  open: number;
  pending_amount: number;
  items: UnderwritingReview[];
}

export const REVIEW_STATUS_LABELS: Record<UnderwritingReview["status"], string> =
  {
    pending_requirements: "Pending U/W Requirements",
    pending_employee: "Pending Employee",
    pending_insurer: "Pending Insurer Decision",
    pending_hr: "Pending HR",
    completed: "Completed",
    cancelled: "Cancelled",
  };

export const DECISION_LABELS: Record<UnderwritingCaseLine["status"], string> = {
  pending: "Pending",
  approved_standard: "Approved Standard Life",
  approved_substandard: "Approved Substandard Life",
  rejected: "Rejected",
  postponed: "Postponed",
  closed: "Closed",
};

export function useUnderwritingQueue(policyYearId: string | null) {
  // Scope the key by active client so a tenant switch reads a fresh cache. The
  // mutations below invalidate by the ["underwriting", policyYearId] prefix,
  // which still matches this longer key.
  const cid = useSession((s) => s.activeClientId);
  return useQuery({
    queryKey: ["underwriting", policyYearId, cid],
    queryFn: () =>
      api.get<UnderwritingQueue>(
        `/policy-years/${policyYearId}/underwriting/cases`,
      ),
    enabled: Boolean(policyYearId),
  });
}

export function useRefreshUnderwriting(policyYearId: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: () =>
      api.post<{ opened: number; updated: number; removed: number }>(
        `/policy-years/${policyYearId}/underwriting/refresh`,
        {},
      ),
    onSuccess: () =>
      qc.invalidateQueries({ queryKey: ["underwriting", policyYearId] }),
  });
}

export function useUpdateReview(policyYearId: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({
      reviewId,
      ...body
    }: {
      reviewId: string;
      status?: UnderwritingReview["status"];
      requirements?: string | null;
    }) =>
      api.patch<UnderwritingReview>(`/underwriting/reviews/${reviewId}`, body),
    onSuccess: () =>
      qc.invalidateQueries({ queryKey: ["underwriting", policyYearId] }),
  });
}

export function useDecideUnderwriting(policyYearId: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({
      caseId,
      ...body
    }: {
      caseId: string;
      status: UnderwritingCaseLine["status"];
      accepted_si?: number | null;
      guaranteed_si?: number | null;
      decided_on?: string | null;
      remarks?: string | null;
    }) =>
      api.patch<UnderwritingReview>(`/underwriting/cases/${caseId}`, body),
    onSuccess: () =>
      qc.invalidateQueries({ queryKey: ["underwriting", policyYearId] }),
  });
}
