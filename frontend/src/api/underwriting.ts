import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "@/api/client";
import { useSession } from "@/stores/session";

export interface UnderwritingCase {
  id: string;
  product_id: string;
  product_code: string;
  subject_type: "employee" | "dependant";
  subject_name: string | null;
  staff_id: string | null;
  eligible_si: number;
  accepted_si: number;
  pending_si: number;
  free_cover_limit: number | null;
  status: "pending" | "accepted" | "declined";
  decided_on: string | null;
  remarks: string | null;
}

export interface UnderwritingQueue {
  total: number;
  pending: number;
  items: UnderwritingCase[];
}

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

export function useDecideUnderwriting(policyYearId: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({
      caseId,
      ...body
    }: {
      caseId: string;
      status: string;
      accepted_si?: number | null;
      decided_on?: string | null;
      remarks?: string | null;
    }) => api.patch<UnderwritingCase>(`/underwriting/cases/${caseId}`, body),
    onSuccess: () =>
      qc.invalidateQueries({ queryKey: ["underwriting", policyYearId] }),
  });
}
