/** Broker claim-review queue hooks (member claim hooks live in api/portal.ts). */
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "@/api/client";
import { isNotFoundError } from "@/lib/errors";
import { useSession } from "@/stores/session";
import type { Utilization } from "@/types";

/** Display labels for document-slot tags (claim_intake.DOC_SLOT_LABELS). */
export const DOC_TYPE_LABELS: Record<string, string> = {
  invoice_receipt: "Invoice/receipt",
  sp_invoice: "SP/hospital invoice",
  finalised_tax_invoice: "Finalised tax invoice",
  summary_tax_invoice: "Summary tax invoice",
  itemised_tax_invoice: "Itemised tax invoice",
  discharge_summary: "Discharge summary",
};

/** Claim category — mirrors `models/claim.CASE_TYPE_*`. */
export type CaseType = "claim" | "log";

/** How a LOG request reached the assessor (`services/log_cases.RECEIVED_VIA`).
 *  Display/reporting only — nothing branches on it. */
export const RECEIVED_VIA_LABELS: Record<string, string> = {
  email: "Email",
  phone: "Phone",
  hr: "HR",
  hospital: "Hospital",
  other: "Other",
};

export interface StoredDocumentMeta {
  id: string;
  file_name: string;
  /** Required-document slot this upload fills; null = additional document. */
  doc_type: string | null;
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
  /** Claim category. "log" = a case an assessor recorded, usually from an
   *  emailed request — same queue, same statuses, same decision. */
  case_type: CaseType;
  /** Who filed it. The member portal shows only `portal` rows, so a case an
   *  assessor created is invisible to them while one they submitted stays
   *  visible even after it is reclassified. */
  origin: "portal" | "broker";
  /** Provenance of a broker-entered case (all optional). */
  received_via: string | null;
  received_on: string | null;
  requested_by: string | null;
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
  /** Member replies nobody here has opened — on the list AND the detail. */
  unread_member_messages: number;
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
  /** "" = both categories, which is what the server defaults to. */
  caseType: CaseType | "" = "",
) {
  const cid = useSession((s) => s.activeClientId);
  return useQuery({
    // caseType is part of the key: without it, switching the filter would serve
    // the previous category's page from cache.
    queryKey: ["claims", cid, policyYearId, status, caseType, offset, limit],
    queryFn: () => {
      const params = new URLSearchParams({
        policy_year_id: policyYearId!,
        offset: String(offset),
        limit: String(limit),
      });
      if (status) params.set("status", status);
      if (caseType) params.set("case_type", caseType);
      return api.get<BrokerClaimList>(`/claims?${params.toString()}`);
    },
    enabled: !!policyYearId,
  });
}

/** One employee's LOG cases — the employee-level card on Coverage & Members. */
export function useEmployeeLogCases(
  policyYearId: string | undefined,
  employeeId: string | null,
) {
  const cid = useSession((s) => s.activeClientId);
  return useQuery({
    queryKey: ["claims", cid, policyYearId, "log", employeeId],
    queryFn: () => {
      const params = new URLSearchParams({
        policy_year_id: policyYearId!,
        employee_id: employeeId!,
        case_type: "log",
        limit: "50",
      });
      return api.get<BrokerClaimList>(`/claims?${params.toString()}`);
    },
    enabled: !!policyYearId && !!employeeId,
  });
}

export interface LogCaseInput {
  employeeId: string;
  claim_kind: "insured" | "flex";
  product_code?: string | null;
  flex_category_name?: string | null;
  dependant_id?: string | null;
  sub_type?: string | null;
  incurred_date: string;
  provider_name?: string | null;
  invoice_number?: string | null;
  diagnosis?: string | null;
  remarks?: string | null;
  amount_claimed: number;
  currency: string;
  received_via?: string | null;
  received_on?: string | null;
  requested_by?: string | null;
  /** Attached after creation, one call each — optional by design. */
  files?: File[];
}

export function useCreateLogCase() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async ({ employeeId, files, ...body }: LogCaseInput) => {
      const claim = await api.post<BrokerClaim>(
        `/employees/${employeeId}/log-cases`,
        body,
      );
      // Documents ride separately and are optional: a failed upload must not
      // discard the case that was just recorded, so each is reported on its own
      // and the case survives regardless.
      const failed: string[] = [];
      for (const file of files ?? []) {
        const form = new FormData();
        form.append("file", file);
        try {
          await api.upload<StoredDocumentMeta>(
            `/claims/${claim.id}/documents`,
            form,
          );
        } catch {
          failed.push(file.name);
        }
      }
      return { claim, failedUploads: failed };
    },
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ["claims"] });
      void qc.invalidateQueries({ queryKey: ["employee-utilization"] });
    },
    meta: { localErrorHandling: true },
  });
}

export function useSetCaseType() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (input: { claimId: string; caseType: CaseType; reason: string }) =>
      api.patch<BrokerClaim>(`/claims/${input.claimId}/case-type`, {
        case_type: input.caseType,
        reason: input.reason,
      }),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ["claims"] });
      void qc.invalidateQueries({ queryKey: ["claim-detail"] });
    },
    meta: { localErrorHandling: true },
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

// ── Claim messages (broker side of the member conversation) ──────────────────

/** Same row the member reads, with two differences the backend fills in:
 * `author_name` is the REAL author here, and `mine`/`unread` are from the
 * broker's point of view (`unread` = the member wrote it and nobody here has
 * opened the thread). Re-deriving either from `author_type` in the UI would
 * invert it on one surface. */
export interface ClaimMessage {
  id: string;
  claim_id: string;
  author_type: "system" | "broker" | "member";
  author_name: string | null;
  subject: string;
  body: string;
  event: string | null;
  created_at: string;
  mine: boolean;
  unread: boolean;
  claim_type: string | null;
  claim_status: string | null;
}

export function useClaimMessages(claimId: string | null) {
  const cid = useSession((s) => s.activeClientId);
  return useQuery({
    queryKey: ["claim-messages", cid, claimId],
    queryFn: () => api.get<ClaimMessage[]>(`/claims/${claimId}/messages`),
    enabled: Boolean(claimId),
    meta: { localErrorHandling: true },
    retry: false,
  });
}

export function useSendClaimMessage() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (input: { claimId: string; body: string; subject?: string }) =>
      api.post<ClaimMessage>(`/claims/${input.claimId}/messages`, {
        body: input.body,
        subject: input.subject?.trim() || null,
      }),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ["claim-messages"] });
    },
    meta: { localErrorHandling: true },
  });
}

/** Clears the queue's unread badge for one claim. `["claims"]` is invalidated
 * because the count rides on the LIST row, not just the thread. */
export function useMarkClaimMessagesRead() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (claimId: string) =>
      api.post<{ marked: number }>(`/claims/${claimId}/messages/read`, {}),
    onSuccess: (out) => {
      if (out.marked === 0) return;
      // The thread too, not just the counts: `unread` is a field ON each
      // message, so without this the rows keep their "new" badges while the
      // queue behind the sheet has already cleared — the same fact disagreeing
      // with itself on one screen.
      void qc.invalidateQueries({ queryKey: ["claim-messages"] });
      void qc.invalidateQueries({ queryKey: ["claims"] });
      void qc.invalidateQueries({ queryKey: ["claim-detail"] });
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

/** Broker-configurable claim document-type registry (aliases + key fields).
 * Per-client rows, lazily seeded from the backend defaults on first read. */
export interface ClaimDocKeyField {
  name: string;
  keywords: string[];
  /** Optional fields are checked but never warned on when absent (e.g. Surgery
   *  on a non-surgical discharge summary). */
  optional?: boolean;
}

export interface ClaimDocType {
  id: string;
  key: string;
  display: string;
  aliases: string[];
  key_fields: ClaimDocKeyField[];
  sector: "govt" | "private" | null;
  slot_key: string | null;
  /** Seeded from the backend defaults (still editable). */
  is_default: boolean;
}

export interface ClaimDocTypeInput {
  display: string;
  aliases: string[];
  key_fields: ClaimDocKeyField[];
  sector: "govt" | "private" | null;
  slot_key: string | null;
}

export function useClaimDocTypes() {
  const cid = useSession((s) => s.activeClientId);
  return useQuery({
    queryKey: ["claim-doc-types", cid],
    queryFn: () => api.get<ClaimDocType[]>("/claim-doc-types"),
  });
}

export function useCreateClaimDocType() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (input: ClaimDocTypeInput) =>
      api.post<ClaimDocType>("/claim-doc-types", input),
    onSuccess: () => void qc.invalidateQueries({ queryKey: ["claim-doc-types"] }),
    meta: { localErrorHandling: true },
  });
}

export function useUpdateClaimDocType() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ id, ...input }: ClaimDocTypeInput & { id: string }) =>
      api.put<ClaimDocType>(`/claim-doc-types/${id}`, input),
    onSuccess: () => void qc.invalidateQueries({ queryKey: ["claim-doc-types"] }),
    meta: { localErrorHandling: true },
  });
}

export function useDeleteClaimDocType() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => api.delete<void>(`/claim-doc-types/${id}`),
    onSuccess: () => void qc.invalidateQueries({ queryKey: ["claim-doc-types"] }),
    meta: { localErrorHandling: true },
  });
}

export function useResetClaimDocTypes() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: () => api.post<ClaimDocType[]>("/claim-doc-types/reset", {}),
    onSuccess: () => void qc.invalidateQueries({ queryKey: ["claim-doc-types"] }),
    meta: { localErrorHandling: true },
  });
}

/** Per-claim-type AI review rule setup. One config per (claim_kind, claim_key)
 * — a claim type with no config keeps the backend's built-in defaults. */
export type ReviewMatchMode = "fuzzy" | "exact" | "numeric";
export type ReviewSeverity = "critical" | "warning" | "info";

export interface ReviewFieldMap {
  portal_field: string;
  document_field: string;
  mode: ReviewMatchMode;
  tolerance?: number | null;
  /** Spend an extra AI vision pass when the text comparison disagrees. */
  verify_with_vision: boolean;
  /** Flag the claim when nothing in the documents substantiates this field.
   *  Independent of the vision flag on purpose. */
  require_evidence: boolean;
}

export interface ReviewAIRule {
  id?: string | null;
  rule: string;
  category: string;
  severity: ReviewSeverity;
}

export interface ClaimReviewConfigInput {
  claim_kind: "insured" | "flex";
  claim_key: string;
  display_label: string;
  enabled: boolean;
  field_maps: ReviewFieldMap[];
  ai_rules: ReviewAIRule[];
  required_documents: string[];
}

export interface ClaimReviewConfig extends ClaimReviewConfigInput {
  id: string;
  /** Server-computed identity of the claim type. ALWAYS join configs to claim
   *  types on this — never on a locally derived key. The backend normalizes
   *  with Python's `casefold()`, which has no exact JS equivalent, and a key
   *  that drifts is silent: the type renders "Default" while its custom rules
   *  are live, and "Customize" then 409s. */
  key: string;
}

export interface ReviewClaimType {
  claim_kind: "insured" | "flex";
  claim_key: string;
  /** See `ClaimReviewConfig.key`. */
  key: string;
  display_label: string;
  sub_types: string[];
}

export interface ReviewScopeOptions {
  claim_types: ReviewClaimType[];
  default_config: {
    field_maps: ReviewFieldMap[];
    ai_rules: ReviewAIRule[];
    required_documents: string[];
  };
  /** False when no benefit year is flagged current — the vocabulary is read
   *  from that year alone, so an empty list means something different (and
   *  one-click fixable) in that case. */
  has_current_year: boolean;
}

export interface SourceReviewConfig {
  id: string;
  claim_kind: string;
  claim_key: string;
  /** See `ClaimReviewConfig.key`. */
  key: string;
  display_label: string;
  enabled: boolean;
  field_map_count: number;
  rule_count: number;
  required_document_count: number;
}

export function useClaimReviewConfigs() {
  const cid = useSession((s) => s.activeClientId);
  return useQuery({
    queryKey: ["claim-review-configs", cid],
    queryFn: () => api.get<ClaimReviewConfig[]>("/claim-review-configs"),
  });
}

export function useReviewScopeOptions() {
  const cid = useSession((s) => s.activeClientId);
  return useQuery({
    queryKey: ["claim-review-configs", "options", cid],
    queryFn: () => api.get<ReviewScopeOptions>("/claim-review-configs/options"),
  });
}

export function useCreateClaimReviewConfig() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (input: ClaimReviewConfigInput) =>
      api.post<ClaimReviewConfig>("/claim-review-configs", input),
    onSuccess: () =>
      void qc.invalidateQueries({ queryKey: ["claim-review-configs"] }),
    meta: { localErrorHandling: true },
  });
}

export function useUpdateClaimReviewConfig() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ id, ...input }: ClaimReviewConfigInput & { id: string }) =>
      api.put<ClaimReviewConfig>(`/claim-review-configs/${id}`, input),
    onSuccess: () =>
      void qc.invalidateQueries({ queryKey: ["claim-review-configs"] }),
    meta: { localErrorHandling: true },
  });
}

export function useDeleteClaimReviewConfig() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => api.delete<void>(`/claim-review-configs/${id}`),
    onSuccess: () =>
      void qc.invalidateQueries({ queryKey: ["claim-review-configs"] }),
    meta: { localErrorHandling: true },
  });
}

/** Stateless prompt preview of the editor's current state. */
export function usePreviewReviewPrompt() {
  return useMutation({
    mutationFn: (input: ClaimReviewConfigInput) =>
      api.post<{ prompt: string }>("/claim-review-configs/preview", input),
    meta: { localErrorHandling: true },
  });
}

export interface ImportSourceCompany {
  id: string;
  name: string;
  configured_count: number;
}

/** Companies this user may import a rule setup FROM. Server-authoritative:
 *  it returns exactly what /import accepts (same broker firm, never the
 *  active company), so the picker can't offer a company that would 404. */
export function useImportSourceCompanies(enabled: boolean) {
  const cid = useSession((s) => s.activeClientId);
  return useQuery({
    queryKey: ["claim-review-configs", "sources", cid],
    queryFn: () =>
      api.get<ImportSourceCompany[]>("/claim-review-configs/sources"),
    enabled,
  });
}

/** Another company's configured claim types, offered for import. */
export function useSourceReviewConfigs(sourceClientId: string | null) {
  const cid = useSession((s) => s.activeClientId);
  return useQuery({
    queryKey: ["claim-review-configs", "from", sourceClientId, cid],
    queryFn: () =>
      api.get<SourceReviewConfig[]>(
        `/claim-review-configs/from/${sourceClientId}`,
      ),
    enabled: sourceClientId !== null,
  });
}

export function useImportReviewConfigs() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (input: { source_client_id: string; config_ids: string[] }) =>
      api.post<{ imported: ClaimReviewConfig[] }>(
        "/claim-review-configs/import",
        input,
      ),
    onSuccess: () =>
      void qc.invalidateQueries({ queryKey: ["claim-review-configs"] }),
    meta: { localErrorHandling: true },
  });
}
