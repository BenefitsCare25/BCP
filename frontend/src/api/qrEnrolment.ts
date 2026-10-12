/** Broker side of no-login QR enrolment: the shared employee QR per enrolment
 *  period, the queue of QR submissions to match, and HR download batches with
 *  their link + PIN. */
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "@/api/client";
import type { FormSubmissionSummary } from "@/api/enrollmentForms";
import { downloadResponseAsFile } from "@/lib/download";
import { keepPreviousInScope } from "@/lib/scopedPlaceholder";
import { useSession } from "@/stores/session";

export interface QrEntry {
  exists: boolean;
  enabled: boolean;
  window_id: string;
  window_name: string;
  window_status: string;
  closes_at: string;
  created_at: string | null;
  rotated_at: string | null;
  pending: number;
  linked: number;
  rejected: number;
}

export type IntakeStatus = "pending" | "linked" | "rejected";

export interface IntakeItem {
  id: string;
  reference_no: string;
  status: IntakeStatus;
  window_id: string | null;
  window_name: string | null;
  submitted_at: string;
  claimed_staff_id: string | null;
  claimed_name: string | null;
  id_masked: string | null;
  category_label: string | null;
  requests: string[];
  dependants: number;
  linked_employee_id: string | null;
  linked_employee_name: string | null;
  linked_submission_id: string | null;
  rejected_reason: string | null;
}

export interface IntakeList {
  items: IntakeItem[];
  total: number;
  offset: number;
  limit: number;
  counts: Partial<Record<IntakeStatus, number>>;
}

export interface IntakeCandidate {
  employee_id: string;
  staff_id: string;
  employee_name: string | null;
  id_masked: string | null;
  category: string | null;
  status: string;
  match: string[];
  existing_form: string | null;
}

export interface IntakeSelection {
  product_code: string;
  product_name?: string;
  category?: string;
  action: "no_change" | "enrol" | "withdraw";
  elected_plan?: string;
  covered?: string[];
  withdrawn?: string[];
  declined?: boolean;
  composition?: string;
  full_premium?: number | null;
  you_pay?: number | null;
}

export interface IntakeFamilyMember {
  id: string;
  relationship: string;
  name: string;
  gender: string;
  id_no: string;
  dob: string;
  occupation?: string | null;
  action_label?: string;
  covered_on?: string[];
}

export interface IntakeDetail {
  item: IntakeItem;
  particulars: Record<string, string | null>;
  selections: IntakeSelection[];
  family: IntakeFamilyMember[];
  life_event: { kind: string; date: string } | null;
  candidates: IntakeCandidate[];
}

export type RejectReason = "duplicate" | "not_employee" | "wrong_company" | "withdrawn" | "other";

export interface BatchFilter {
  window_id: string | null;
  statuses: ("submitted" | "acknowledged")[];
  include_pending_qr: boolean;
  exclude_ids: string[];
}

export interface BatchPreviewItem {
  id: string;
  kind: "form" | "intake";
  reference_no: string;
  version: number;
  staff_id: string | null;
  employee_name: string | null;
  source: string;
  status: string;
  submitted_at: string | null;
}

export interface Grant {
  id: string;
  state: "ready" | "active" | "expired" | "locked" | "revoked";
  recipient_note: string | null;
  issued_at: string;
  unused_expires_at: string;
  activated_at: string | null;
  expires_at: string | null;
  downloads: number;
  revoke_reason: string | null;
}

export interface Batch {
  id: string;
  label: string;
  status: "ready" | "closed" | "expired";
  window_id: string | null;
  file_count: number;
  size_bytes: number;
  created_at: string;
  expires_at: string;
  grant: Grant | null;
}

export interface IssuedGrant {
  grant: Grant;
  url: string;
  pin: string;
}

function useClientId(): string | null {
  return useSession((s) => s.activeClientId);
}

const invalidateQr = (qc: ReturnType<typeof useQueryClient>) => {
  void qc.invalidateQueries({ queryKey: ["qr-entry"] });
  void qc.invalidateQueries({ queryKey: ["qr-intakes"] });
  void qc.invalidateQueries({ queryKey: ["enrollment-forms"] });
};

// ── Shared QR ────────────────────────────────────────────────────────────────

export function useQrEntry(windowId: string | undefined) {
  const cid = useClientId();
  return useQuery({
    queryKey: ["qr-entry", windowId, cid],
    queryFn: () => api.get<QrEntry>(`/enrollment-windows/${windowId}/qr-entry`),
    enabled: !!windowId,
  });
}

export function useQrEntryLink(windowId: string | undefined, enabled: boolean) {
  const cid = useClientId();
  return useQuery({
    queryKey: ["qr-entry-link", windowId, cid],
    queryFn: () => api.post<{ url: string }>(`/enrollment-windows/${windowId}/qr-entry/link`, {}),
    enabled: !!windowId && enabled,
    staleTime: Infinity,
  });
}

export function useQrEntryAction(windowId: string | undefined) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (action: "enable" | "pause" | "resume" | "rotate") => {
      const base = `/enrollment-windows/${windowId}/qr-entry`;
      if (action === "enable") return api.post<QrEntry>(base, {});
      if (action === "rotate") return api.post<QrEntry>(`${base}/rotate`, {});
      return api.patch<QrEntry>(base, { enabled: action === "resume" });
    },
    onSuccess: (_data, action) => {
      if (action === "rotate") void qc.removeQueries({ queryKey: ["qr-entry-link", windowId] });
      invalidateQr(qc);
    },
  });
}

// ── Submissions ──────────────────────────────────────────────────────────────

export interface IntakeFilters {
  status: IntakeStatus;
  windowId?: string;
  query?: string;
  offset?: number;
}

export function useQrIntakes(policyYearId: string | undefined, f: IntakeFilters) {
  const cid = useClientId();
  const params = new URLSearchParams({ status: f.status, limit: "50", offset: String(f.offset ?? 0) });
  if (f.windowId) params.set("window_id", f.windowId);
  if (f.query?.trim()) params.set("q", f.query.trim());
  return useQuery({
    queryKey: ["qr-intakes", policyYearId, cid, f],
    queryFn: () => api.get<IntakeList>(`/policy-years/${policyYearId}/qr-intakes?${params}`),
    enabled: !!policyYearId,
    ...keepPreviousInScope(cid, policyYearId),
  });
}

export function useQrIntake(id: string | null) {
  const cid = useClientId();
  return useQuery({
    queryKey: ["qr-intake", id, cid],
    queryFn: () => api.get<IntakeDetail>(`/qr-intakes/${id}`),
    enabled: !!id,
  });
}

export function searchCandidates(id: string, q: string): Promise<IntakeCandidate[]> {
  return api.get<IntakeCandidate[]>(`/qr-intakes/${id}/candidates?q=${encodeURIComponent(q)}`);
}

export async function downloadIntakePdf(item: { id: string; reference_no: string }) {
  const res = await api.downloadResponse(`/qr-intakes/${item.id}/pdf`);
  await downloadResponseAsFile(res, `Enrolment form ${item.reference_no}.pdf`);
}

export function useIntakeAction() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (input:
      | { kind: "link"; id: string; employeeId: string; note?: string; confirmSupersede?: boolean }
      | { kind: "reject"; id: string; reason: RejectReason; note?: string }
      | { kind: "restore"; id: string }): Promise<FormSubmissionSummary | void> => {
      if (input.kind === "link") {
        return api.post<FormSubmissionSummary>(`/qr-intakes/${input.id}/link`, {
          employee_id: input.employeeId, note: input.note?.trim() || null,
          confirm_supersede: input.confirmSupersede ?? false,
        });
      }
      if (input.kind === "reject") {
        return api.post<void>(`/qr-intakes/${input.id}/reject`, {
          reason: input.reason, note: input.note?.trim() || null,
        });
      }
      return api.post<void>(`/qr-intakes/${input.id}/restore`, {});
    },
    onSuccess: () => {
      invalidateQr(qc);
      void qc.invalidateQueries({ queryKey: ["qr-intake"] });
    },
  });
}

// ── HR download batches ──────────────────────────────────────────────────────

export function useHrBatches(policyYearId: string | undefined) {
  const cid = useClientId();
  return useQuery({
    queryKey: ["hr-batches", policyYearId, cid],
    queryFn: () => api.get<{ items: Batch[] }>(`/policy-years/${policyYearId}/hr-batches`),
    enabled: !!policyYearId,
    refetchInterval: 30_000,
  });
}

export function useBatchPreview(
  policyYearId: string | undefined,
  f: Omit<BatchFilter, "exclude_ids">,
  enabled: boolean,
) {
  const cid = useClientId();
  const params = new URLSearchParams();
  if (f.window_id) params.set("window_id", f.window_id);
  f.statuses.forEach((s) => params.append("statuses", s));
  if (f.include_pending_qr) params.set("include_pending_qr", "true");
  return useQuery({
    queryKey: ["hr-batch-preview", policyYearId, cid, f],
    queryFn: () =>
      api.get<{ items: BatchPreviewItem[]; total: number; limit: number }>(
        `/policy-years/${policyYearId}/hr-batches/preview?${params}`,
      ),
    enabled: !!policyYearId && enabled && f.statuses.length > 0,
  });
}

export function usePrepareBatch(policyYearId: string | undefined) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: BatchFilter) => api.post<Batch>(`/policy-years/${policyYearId}/hr-batches`, body),
    onSuccess: () => void qc.invalidateQueries({ queryKey: ["hr-batches"] }),
  });
}

export function useBatchAction() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (input:
      | { kind: "issue"; batchId: string; recipientNote?: string }
      | { kind: "revoke"; grantId: string }
      | { kind: "close"; batchId: string }): Promise<IssuedGrant | Grant | Batch> => {
      if (input.kind === "issue") {
        return api.post<IssuedGrant>(`/hr-batches/${input.batchId}/grants`, {
          recipient_note: input.recipientNote?.trim() || null,
        });
      }
      if (input.kind === "revoke") return api.post<Grant>(`/hr-grants/${input.grantId}/revoke`, {});
      return api.post<Batch>(`/hr-batches/${input.batchId}/close`, {});
    },
    onSuccess: () => void qc.invalidateQueries({ queryKey: ["hr-batches"] }),
  });
}
