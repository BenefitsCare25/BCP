/** Broker claim-review queue hooks (member claim hooks live in api/portal.ts). */
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "@/api/client";
import { isNotFoundError, isStaleConfigurationError } from "@/lib/errors";
import { useSession } from "@/stores/session";
import type { ConversationSubject } from "@/api/portalMessages";
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
  status: "queued" | "running" | "retry_wait" | "complete" | "error" | "cancelled";
  verdict: "clean" | "flagged" | null;
  confidence: number | null;
  summary: string | null;
  stage: "queued" | "deterministic" | "extraction" | "comparison" | "vision" | "verdict" | "persist";
  progress_current: number;
  progress_total: number;
  attempt: number;
  started_at: string | null;
  heartbeat_at: string | null;
  completed_at: string | null;
  error_code: string | null;
  deterministic_short_circuit: boolean;
  review_config_label: string | null;
  review_config_fingerprint: string | null;
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
  source: "deterministic" | "ai" | "platform";
  evidence: string;
  error_code?: string | null;
  affected_fields?: string[] | null;
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

/** An earlier visit a claim is anchored to. Mirrors the portal's `ClaimAnchor`
 * — one server-side projection, so the broker and the member describe the same
 * visit in the same words. */
export interface ClaimAnchor {
  id: string;
  provider_name: string | null;
  incurred_date: string;
  admission_date: string | null;
  discharge_date: string | null;
  diagnosis: string | null;
  doctor_name: string | null;
  referral_document_id: string | null;
  from_records: boolean;
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
  /** The visit this claim continues — the admission a pre-/post-hospitalisation
   * consult is claimed against, or the first visit of a specialist course.
   * Resolved to a summary server-side (`services/claim_episodes.anchor_out`),
   * which is also what decides how much of a broker-recorded case a MEMBER may
   * see of it — so never re-fetch the anchor claim to render this. */
  related_claim_id: string | null;
  related_claim: ClaimAnchor | null;
  incurred_date: string;
  provider_name: string | null;
  invoice_number: string | null;
  /** The treating doctor — pre-/post-hospitalisation claims must name one. */
  doctor_name: string | null;
  diagnosis: string | null;
  remarks: string | null;
  amount_claimed: number;
  /** The currency the MEMBER was billed in — not the one we settle in. */
  currency: string;
  /** `amount_claimed` in `policy_currency`. NULL means EITHER "already in it"
   *  OR "no rate could be fetched" — read `fx_state`, never this. */
  amount_converted?: number | null;
  /** SERVED. "not_required" | "converted" | "unavailable". An `unavailable`
   *  claim has no policy-currency value at all: it cannot be compared to a
   *  limit and the approve endpoint refuses it until someone supplies one. */
  fx_state: "not_required" | "converted" | "unavailable";
  fx_rate: number | null;
  /** The day the rate was published — earlier than `incurred_date` across a
   *  weekend or holiday, which is ordinary rather than a discrepancy. */
  fx_rate_date: string | null;
  /** "frankfurter" (the reference rate) or "broker" (keyed in by an assessor). */
  fx_source: string | null;
  fx_stale: boolean;
  fx_acknowledged_at: string | null;
  /** The currency limits, approvals and payments are denominated in. */
  policy_currency: string;
  /** ALWAYS in `policy_currency`. Labelling it with `currency` overstates a
   *  foreign approval by the exchange rate. */
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
  /** Server-owned state-machine capabilities for this claim. */
  allowed_actions: string[];

  // ── Settlement (the insurer leg) ───────────────────────────────────────────
  /** Human-quotable reference, minted at submit. Null on a draft. */
  reference_no: string | null;
  sent_to_insurer_at: string | null;
  insurer_deadline_on: string | null;
  paid_on: string | null;
  /** What the insurer actually paid — may fall short of `amount_approved`. */
  payment_amount: number | null;
  hospital_type: string | null;
  /** The sector DERIVED from the provider, served (never re-derived here — the
   *  claims report labels its column from the same resolver). `hospital_type`
   *  is the assessor's OVERRIDE; null there means "use this". */
  hospital_type_derived: string | null;
  admission_date: string | null;
  discharge_date: string | null;
  taxable: boolean | null;
  cpf_claimable: boolean | null;
  /** Broker-only note. Never shown to the member (that is `remarks`). */
  admin_remarks: string | null;
  /** Whether the claim draws on an inpatient benefit. SERVED, never derived
   *  here — the claims report picks its columns from the same helper, so a
   *  product-code list in TypeScript would silently hide the sector field on a
   *  hospitalisation claim while the report kept printing the column. */
  is_inpatient: boolean;
  /** Derived server-side from the dates — never stored, so never stale. */
  servicer_days: number | null;
  insurer_days: number | null;
  days_over_deadline: number | null;

  // ── Amendment ─────────────────────────────────────────────────────────────
  /** Optimistic-concurrency token. **Send it back on every decision.** A member
   *  may correct their claim right up to the moment it is decided, so without
   *  it an assessor can read $150, have it changed to $105 under them, and
   *  approve a figure that is no longer on the claim. */
  revision: number;
  /** When the claim was last corrected. Null = never amended. */
  amended_at: string | null;
  /** WHICH SURFACE made that correction — `"member"` or `"broker"`, null on a
   *  claim amended before the column existed. The queue's Amended chip reads
   *  THIS: `amended_at` is stamped by the assessor's own correction too, so a
   *  chip on the timestamp flagged their own save back at them. */
  amended_by: "member" | "broker" | null;
  /** Whether the CLAIMANT may still edit. Broker-side this is context, not a
   *  gate: a broker may amend in any status (with a reason once settled). */
  member_editable: boolean;
  member_edit_block: string | null;
}

export interface BrokerClaimList {
  total: number;
  offset: number;
  limit: number;
  items: BrokerClaim[];
}

/** A thread as WORK. Shares the member's shape (`api/portalMessages`) plus the
 * employee, which the member's own list has no business carrying. */
export interface BrokerConversation {
  /** The SAME shape the member's own list gets — one served subject, two
   *  surfaces. Re-declaring a claim-only copy here is how the broker queue
   *  ended up unable to see a question's topic at all. */
  subject: ConversationSubject;
  last_message: ClaimMessage;
  message_count: number;
  /** Member messages nobody here has opened — the opposite sense to the
   *  member's own `unread`, filled server-side per surface. */
  unread: number;
  employee: { id: string; staff_id: string; employee_name: string | null } | null;
}

export interface BrokerConversationList {
  total: number;
  offset: number;
  limit: number;
  unread_total: number;
  items: BrokerConversation[];
}

export function useBrokerClaims(
  policyYearId: string | undefined,
  status: string,
  offset: number,
  limit: number,
  /** "" = both categories, which is what the server defaults to. */
  caseType: CaseType | "" = "",
  /** One member's claims. The endpoint has always accepted `employee_id`; the
   *  queue never passed it, so the flex panel's "2 claims" link landed on the
   *  whole firm's queue. */
  employeeId?: string,
  search = "",
) {
  const cid = useSession((s) => s.activeClientId);
  return useQuery({
    // caseType and employeeId are part of the key: without them, switching a
    // filter would serve the previous selection's page from cache.
    queryKey: [
      "claims", cid, policyYearId, status, caseType, employeeId, search, offset, limit,
    ],
    queryFn: () => {
      const params = new URLSearchParams({
        policy_year_id: policyYearId!,
        offset: String(offset),
        limit: String(limit),
      });
      if (status) params.set("status", status);
      if (caseType) params.set("case_type", caseType);
      if (employeeId) params.set("employee_id", employeeId);
      if (search.trim()) params.set("search", search.trim());
      return api.get<BrokerClaimList>(`/claims?${params.toString()}`);
    },
    enabled: !!policyYearId,
    // Keep the operational queue and unread badges current while it is open.
    refetchInterval: 15_000,
    refetchIntervalInBackground: false,
  });
}

/** The broker's message queue.
 *
 * `awaiting: "us"` is the work — threads whose last word is the member's. It is
 * the default on the server too, because the alternative was scrolling the
 * claims queue looking for unread badges: `GET /claims` filters on status,
 * employee and case type, and nothing there could ask who is waiting.
 *
 * Shares the `["claims", cid, …]` key prefix, so replying to a member (which
 * invalidates `["claims"]`) refreshes this list without naming it. */
export function useBrokerConversations(
  policyYearId: string | undefined,
  awaiting: "us" | "any",
  offset: number,
  limit: number,
  search = "",
) {
  const cid = useSession((s) => s.activeClientId);
  return useQuery({
    queryKey: [
      "claims",
      cid,
      policyYearId,
      "conversations",
      awaiting,
      offset,
      limit,
      search,
    ],
    queryFn: () => {
      const params = new URLSearchParams({
        policy_year_id: policyYearId!,
        awaiting,
        offset: String(offset),
        limit: String(limit),
      });
      if (search.trim()) params.set("q", search.trim());
      return api.get<BrokerConversationList>(
        `/conversations?${params.toString()}`,
      );
    },
    enabled: !!policyYearId,
  });
}

/** A member's question, broker side. Its own sheet rather than a branch in the
 *  claim one — a question has no amount, no documents and no decision. */
export interface BrokerEnquiry {
  id: string;
  topic: string;
  /** Served labels — the vocabulary has one home on the backend. `topic_urgent`
   *  marks a Letter of Guarantee request, which the queue lifts to the top. */
  topic_label: string | null;
  topic_urgent: boolean;
  subject: string;
  status: "open" | "answered" | "closed";
  about_claim: ConversationSubject | null;
  created_at: string;
  employee: { id: string; staff_id: string; employee_name: string | null } | null;
}

export function useBrokerEnquiry(enquiryId: string | null) {
  const cid = useSession((s) => s.activeClientId);
  return useQuery({
    queryKey: ["claims", cid, "enquiry", enquiryId],
    queryFn: () => api.get<BrokerEnquiry>(`/enquiries/${enquiryId}`),
    enabled: Boolean(enquiryId),
  });
}

export function useBrokerEnquiryMessages(enquiryId: string | null) {
  const cid = useSession((s) => s.activeClientId);
  return useQuery({
    queryKey: ["claim-messages", cid, "enquiry", enquiryId],
    queryFn: () => api.get<ClaimMessage[]>(`/enquiries/${enquiryId}/messages`),
    enabled: Boolean(enquiryId),
  });
}

export function useSendBrokerEnquiryMessage() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (input: { enquiryId: string; body: string }) =>
      api.post<ClaimMessage>(`/enquiries/${input.enquiryId}/messages`, {
        body: input.body,
        subject: null,
      }),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ["claim-messages"] });
      // Answering changes who the thread is WAITING ON, which decides whether
      // it appears in the Messages queue at all — the same reason the claim
      // send mutation invalidates this prefix.
      void qc.invalidateQueries({ queryKey: ["claims"] });
    },
    meta: { localErrorHandling: true },
  });
}

export function useSetEnquiryStatus() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (input: { enquiryId: string; action: "close" | "reopen" }) =>
      api.post<BrokerEnquiry>(`/enquiries/${input.enquiryId}/status`, {
        action: input.action,
      }),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ["claims"] });
    },
    meta: { localErrorHandling: true },
  });
}

export function useMarkEnquiryRead() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (enquiryId: string) =>
      api.post<{ marked: number }>(`/enquiries/${enquiryId}/messages/read`, {}),
    onSuccess: (out) => {
      if (out.marked === 0) return;
      void qc.invalidateQueries({ queryKey: ["claim-messages"] });
      void qc.invalidateQueries({ queryKey: ["claims"] });
    },
    meta: { localErrorHandling: true },
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
  const policyYearId = useSession((s) => s.currentPolicyYearId);
  return useQuery({
    queryKey: ["claim-detail", cid, policyYearId, claimId],
    queryFn: () => api.get<BrokerClaim>(`/claims/${claimId}`),
    enabled: !!claimId,
    meta: { localErrorHandling: true },
  });
}

export function useClaimReview(
  claimId: string | null,
  claimPending = false,
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
    refetchInterval: (query) => {
      const review = query.state.data;
      if (review === null) return claimPending ? 5_000 : false;
      return review && ["queued", "running", "retry_wait"].includes(review.status)
        ? 5_000
        : false;
    },
    meta: { localErrorHandling: true },
  });
}

/** Broker view of one employee's claim usage vs limits. */
export function useEmployeeUtilization(employeeId: string | null) {
  const cid = useSession((s) => s.activeClientId);
  const policyYearId = useSession((s) => s.currentPolicyYearId);
  return useQuery({
    queryKey: ["employee-utilization", cid, policyYearId, employeeId],
    queryFn: () => api.get<Utilization>(`/employees/${employeeId}/utilization`),
    enabled: !!employeeId,
  });
}

/** A live conversion preview. `available: false` = no rate could be fetched;
 *  the case is still recordable and a person converts it. */
export interface FxQuote {
  currency: string;
  policy_currency: string;
  amount: number;
  converted: number | null;
  rate: number | null;
  as_of_date: string | null;
  rate_date: string | null;
  stale: boolean;
  source: string | null;
  available: boolean;
  note: string | null;
}

export interface ClaimDecisionInput {
  claimId: string;
  action: "approve" | "reject" | "needs_info";
  note?: string;
  approvedAmount?: number;
  /** The policy-currency value of a foreign claim, keyed in by the assessor
   *  because no reference rate could be fetched. REQUIRED to approve such a
   *  claim — the server 422s `fx_amount_required` without it. */
  convertedAmount?: number;
  /** Approve past the remaining limit (after a 409 `limit_exceeded`). */
  acknowledge?: boolean;
  /** The `revision` the assessor actually read. A mismatch 409s
   *  (`claim_amended`) instead of deciding on a figure that has since moved. */
  expectedRevision?: number;
}

function commandInit(): RequestInit {
  return { headers: { "Idempotency-Key": crypto.randomUUID() } };
}

export function useDecideClaim() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (input: ClaimDecisionInput) =>
      api.post<BrokerClaim>(`/claims/${input.claimId}/decision`, {
        action: input.action,
        note: input.note ?? null,
        approved_amount: input.approvedAmount ?? null,
        converted_amount: input.convertedAmount ?? null,
        acknowledge: input.acknowledge ?? false,
        expected_revision: input.expectedRevision ?? null,
      }, commandInit()),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ["claims"] });
      // Approval changes the employee's usage-vs-limits view.
      void qc.invalidateQueries({ queryKey: ["employee-utilization"] });
      void qc.invalidateQueries({ queryKey: ["claim-detail"] });
    },
    meta: { localErrorHandling: true },
  });
}

/** Dispatch an approved claim to the insurer. Dates optional — the server
 *  defaults to now plus the standard turnaround. */
/** Try the reference rate again on a claim that arrived unconverted.
 *
 * Clears the server's outage breaker first: an assessor pressing this is saying
 * "try now", not "wait out a cooldown somebody else's submit started". */
export function useRefreshClaimConversion() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (claimId: string) =>
      api.post<BrokerClaim>(`/claims/${claimId}/fx-refresh`, {}, commandInit()),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ["claims"] });
      void qc.invalidateQueries({ queryKey: ["claim-detail"] });
      // The claim moves from `pending_unconverted` into the pending SUM.
      void qc.invalidateQueries({ queryKey: ["employee-utilization"] });
    },
    meta: { localErrorHandling: true },
  });
}

/** What a foreign amount is worth, for the LOG-case form. */
export function useBrokerFxQuote(
  currency: string,
  amount: number | null,
  on: string,
) {
  const enabled =
    Boolean(currency) && currency !== "SGD" && Boolean(on) && (amount ?? 0) > 0;
  return useQuery({
    queryKey: ["fx-quote", currency, amount, on],
    queryFn: () => {
      const params = new URLSearchParams({
        currency,
        amount: String(amount),
        on,
      });
      return api.get<FxQuote>(`/claims/fx-quote?${params.toString()}`);
    },
    enabled,
    meta: { localErrorHandling: true },
    // A rate for a given day is fixed; re-asking on focus spends the limit to
    // be told the same number.
    staleTime: 5 * 60 * 1000,
    retry: false,
  });
}

export function useSendToInsurer() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (input: {
      claimId: string;
      sentOn?: string;
      deadlineOn?: string;
      note?: string;
    }) =>
      api.post<BrokerClaim>(`/claims/${input.claimId}/send-to-insurer`, {
        sent_on: input.sentOn ?? null,
        deadline_on: input.deadlineOn ?? null,
        note: input.note ?? null,
      }, commandInit()),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ["claims"] });
      void qc.invalidateQueries({ queryKey: ["claim-detail"] });
    },
    meta: { localErrorHandling: true },
  });
}

/** Record the insurer's payment advice. */
export function useRecordClaimPayment() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (input: {
      claimId: string;
      paidOn: string;
      amount?: number;
      note?: string;
      acknowledgeOverpayment?: boolean;
    }) =>
      api.post<BrokerClaim>(`/claims/${input.claimId}/payment`, {
        paid_on: input.paidOn,
        amount: input.amount ?? null,
        note: input.note ?? null,
        acknowledge_overpayment: input.acknowledgeOverpayment ?? false,
      }, commandInit()),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ["claims"] });
      void qc.invalidateQueries({ queryKey: ["claim-detail"] });
      // Settlement keeps the money spent, but the member's usage view reads
      // through the same buckets — refetch so a paid claim is not left showing
      // as pending beside its own limit.
      void qc.invalidateQueries({ queryKey: ["employee-utilization"] });
    },
    meta: { localErrorHandling: true },
  });
}

/** Assessor-entered detail. PARTIAL — only the keys passed are written, so two
 *  forms editing different fields cannot blank each other's. */
export function useUpdateClaimAssessment() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (input: {
      claimId: string;
      patch: Partial<{
        hospital_type: string | null;
        admission_date: string | null;
        discharge_date: string | null;
        taxable: boolean | null;
        cpf_claimable: boolean | null;
        admin_remarks: string | null;
        // Settlement AMENDMENTS — they correct the recorded dates without
        // moving the status. `send-to-insurer` / `payment` are the
        // transitions, and each is offered from one status only, so without
        // these a claim past that point could never have a wrong date fixed.
        sent_to_insurer_on: string | null;
        insurer_deadline_on: string | null;
        paid_on: string | null;
        payment_amount: number | null;
        acknowledge_overpayment: boolean;
      }>;
    }) =>
      api.patch<BrokerClaim>(
        `/claims/${input.claimId}/assessment`,
        input.patch,
        commandInit(),
      ),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ["claims"] });
      void qc.invalidateQueries({ queryKey: ["claim-detail"] });
    },
    meta: { localErrorHandling: true },
  });
}

/** Correcting what the MEMBER stated — a different act from
 *  `useUpdateClaimAssessment`, which records facts the broker owns and the
 *  member never stated (sector, admission dates, payroll treatment).
 *
 *  Available in every status, but a settled claim demands a `reason`: by then
 *  the figure has been given to the member and, on a dispatched claim, to the
 *  insurer. Partial, like everything else here. */
export interface BrokerClaimAmendInput {
  claimId: string;
  patch: Partial<{
    claim_type: string;
    incurred_date: string;
    provider_name: string;
    invoice_number: string;
    doctor_name: string | null;
    diagnosis: string | null;
    amount_claimed: number;
    currency: string;
    benefit_key: string | null;
  }>;
  /** Required once the claim is settled — the server 422s (`reason_required`)
   *  without it, because a blank reason on a corrected payment figure is an
   *  audit row that explains nothing. */
  reason?: string;
  expectedRevision?: number;
}

export function useAmendClaim() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (input: BrokerClaimAmendInput) =>
      api.patch<BrokerClaim>(`/claims/${input.claimId}`, {
        ...input.patch,
        ...(input.reason ? { reason: input.reason } : {}),
        expected_revision: input.expectedRevision ?? null,
      }),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ["claims"] });
      void qc.invalidateQueries({ queryKey: ["claim-detail"] });
      // The claimed figure feeds the utilization buckets.
      void qc.invalidateQueries({ queryKey: ["employee-utilization"] });
    },
    meta: { localErrorHandling: true },
  });
}

export function useRerunReview() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (claimId: string) =>
      api.post<BrokerClaim>(
        `/claims/${claimId}/rerun-review`,
        {},
        commandInit(),
      ),
    onSuccess: (_data, claimId) => {
      void qc.invalidateQueries({ queryKey: ["claims"] });
      void qc.invalidateQueries({ queryKey: ["claim-detail"] });
      void qc.invalidateQueries({ queryKey: ["claim-review"], exact: false });
      void qc.invalidateQueries({
        queryKey: ["claim-review"],
        predicate: (query) => query.queryKey.includes(claimId),
      });
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
  // NOTE: no `claim_type` / `claim_status`. They were dropped from
  // `ClaimMessageOut` when the flat inbox became conversations — that context
  // is the thread's SUBJECT now. Left declared here they would type a value the
  // API never sends, which the next consumer reads as `string | null` and gets
  // `undefined`.
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
      // `["claims"]` too, and it is load-bearing: replying changes who the
      // thread is WAITING ON, and that decides whether it appears in the
      // Messages queue at all (`useBrokerConversations`, whose key sits under
      // this prefix). Without it a broker replies from the sheet and the thread
      // stays listed under "Needs reply" with the tab still counting it — the
      // queue reporting work that has just been done.
      //
      // `useMarkClaimMessagesRead` cannot cover this: it early-returns when
      // nothing was marked, which is exactly the case once the sheet has
      // already been opened.
      void qc.invalidateQueries({ queryKey: ["claims"] });
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
  const blob = await getClaimDocumentBlob(claimId, doc);
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = doc.file_name;
  a.click();
  setTimeout(() => URL.revokeObjectURL(url), 10_000);
}

/** Fetch document bytes without forcing a browser download. Claim review uses
 * this to keep the evidence visible beside the form and decision controls. */
export function getClaimDocumentBlob(
  claimId: string,
  doc: StoredDocumentMeta,
): Promise<Blob> {
  return api.download(`/claims/${claimId}/documents/${doc.id}/download`);
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
  /** Stable claim scopes this recognised document can suggest at intake. */
  claim_scope_keys: string[];
  /** Seeded from the backend defaults (still editable). */
  is_default: boolean;
  updated_at: string;
}

export interface ClaimDocTypeInput {
  display: string;
  aliases: string[];
  key_fields: ClaimDocKeyField[];
  sector: "govt" | "private" | null;
  slot_key: string | null;
  claim_scope_keys: string[];
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
    mutationFn: ({ id, ...input }: ClaimDocTypeInput & { id: string; expected_updated_at: string }) =>
      api.put<ClaimDocType>(`/claim-doc-types/${id}`, input),
    onSuccess: () => void qc.invalidateQueries({ queryKey: ["claim-doc-types"] }),
    onError: (error) => {
      if (isStaleConfigurationError(error)) {
        void qc.invalidateQueries({ queryKey: ["claim-doc-types"] });
      }
    },
    meta: { localErrorHandling: true },
  });
}

/** One required upload owned by exactly one claim type. Recognition settings
 * are copied, never shared, when a setup is duplicated. */
export interface ClaimSetupDocument {
  id: string;
  key: string;
  display: string;
  instructions: string | null;
  aliases: string[];
  key_fields: ClaimDocKeyField[];
}

export interface ClaimDocumentSetup {
  id: string | null;
  claim_kind: "insured" | "flex";
  claim_key: string;
  scope_code: string;
  scope_key: string;
  product_label: string;
  display_label: string;
  group_code: string | null;
  group_label: string | null;
  documents: ClaimSetupDocument[];
  is_default: boolean;
  updated_at: string | null;
}

export interface ClaimDocumentSetupInput {
  claim_kind: "insured" | "flex";
  claim_key: string;
  scope_code: string;
  display_label: string;
  documents: ClaimSetupDocument[];
  expected_updated_at?: string | null;
}

export function useClaimDocumentSetups() {
  const cid = useSession((s) => s.activeClientId);
  return useQuery({
    queryKey: ["claim-document-setups", cid],
    queryFn: () => api.get<ClaimDocumentSetup[]>("/claim-document-setups"),
  });
}

export function useSaveClaimDocumentSetup() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (input: ClaimDocumentSetupInput) =>
      api.put<ClaimDocumentSetup>("/claim-document-setups", input),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ["claim-document-setups"] });
      void qc.invalidateQueries({ queryKey: ["portal", "coverage-options"] });
    },
    onError: (error) => {
      if (isStaleConfigurationError(error)) {
        void qc.invalidateQueries({ queryKey: ["claim-document-setups"] });
      }
    },
    meta: { localErrorHandling: true },
  });
}

export function useDuplicateClaimDocumentSetup() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (input: {
      source_scope_key: string;
      target: ClaimDocumentSetupInput;
    }) =>
      api.post<ClaimDocumentSetup>("/claim-document-setups/duplicate", input),
    onSuccess: () =>
      void qc.invalidateQueries({ queryKey: ["claim-document-setups"] }),
    onError: (error) => {
      if (isStaleConfigurationError(error)) {
        void qc.invalidateQueries({ queryKey: ["claim-document-setups"] });
      }
    },
    meta: { localErrorHandling: true },
  });
}

export function useUpdateClaimDocScopeAssignments() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (assignments: Array<{
      id: string;
      expected_updated_at: string;
      claim_scope_keys: string[];
    }>) =>
      api.post<ClaimDocType[]>("/claim-doc-types/scope-assignments", {
        assignments,
      }),
    onSuccess: () => void qc.invalidateQueries({ queryKey: ["claim-doc-types"] }),
    onError: (error) => {
      if (isStaleConfigurationError(error)) {
        void qc.invalidateQueries({ queryKey: ["claim-doc-types"] });
      }
    },
    meta: { localErrorHandling: true },
  });
}

export function useDeleteClaimDocType() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ id, expected_updated_at }: { id: string; expected_updated_at: string }) =>
      api.delete<void>(
        `/claim-doc-types/${id}?expected_updated_at=${encodeURIComponent(expected_updated_at)}`,
      ),
    onSuccess: () => void qc.invalidateQueries({ queryKey: ["claim-doc-types"] }),
    onError: (error) => {
      if (isStaleConfigurationError(error)) {
        void qc.invalidateQueries({ queryKey: ["claim-doc-types"] });
      }
    },
    meta: { localErrorHandling: true },
  });
}

export function useResetClaimDocTypes() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (expected_versions: Record<string, string>) =>
      api.post<ClaimDocType[]>("/claim-doc-types/reset", { expected_versions }),
    onSuccess: () => void qc.invalidateQueries({ queryKey: ["claim-doc-types"] }),
    onError: (error) => {
      if (isStaleConfigurationError(error)) {
        void qc.invalidateQueries({ queryKey: ["claim-doc-types"] });
      }
    },
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
  /** `*` is the product default; a stable subtype code is an exact override. */
  scope_code: string;
  display_label: string;
  enabled: boolean;
  field_maps: ReviewFieldMap[];
  ai_rules: ReviewAIRule[];
  required_documents: string[];
}

export interface ClaimReviewConfig extends ClaimReviewConfigInput {
  id: string;
  updated_at: string;
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
  scopes: ReviewClaimScope[];
}

export interface ReviewClaimScope {
  scope_code: string;
  key: string;
  display_label: string;
  sub_type: string | null;
  /** Immediate inherited scope, before the product default. */
  parent_scope_code: string | null;
  /** Server-computed identity of the inherited compatibility scope. */
  parent_key: string | null;
  /** Presentation-only hierarchy; grouping rows are never configurable. */
  group_code: string | null;
  group_label: string | null;
  configurable: boolean;
}

export interface ReviewScopeOptions {
  claim_types: ReviewClaimType[];
  default_config: {
    field_maps: ReviewFieldMap[];
    ai_rules: ReviewAIRule[];
    required_documents: string[];
  };
  /** Backend-owned keys available to field mappings. */
  portal_fields: string[];
  /** False when no benefit year is flagged current — the vocabulary is read
   *  from that year alone, so an empty list means something different (and
   *  one-click fixable) in that case. */
  has_current_year: boolean;
}

export interface SourceReviewConfig {
  id: string;
  claim_kind: string;
  claim_key: string;
  scope_code: string;
  /** See `ClaimReviewConfig.key`. */
  key: string;
  product_label: string;
  display_label: string;
  group_label: string | null;
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
    mutationFn: ({ id, ...input }: ClaimReviewConfigInput & { id: string; expected_updated_at: string }) =>
      api.put<ClaimReviewConfig>(`/claim-review-configs/${id}`, input),
    onSuccess: () =>
      void qc.invalidateQueries({ queryKey: ["claim-review-configs"] }),
    onError: (error) => {
      if (isStaleConfigurationError(error)) {
        void qc.invalidateQueries({ queryKey: ["claim-review-configs"] });
      }
    },
    meta: { localErrorHandling: true },
  });
}

export function useDeleteClaimReviewConfig() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ id, expected_updated_at }: { id: string; expected_updated_at: string }) =>
      api.delete<void>(
        `/claim-review-configs/${id}?expected_updated_at=${encodeURIComponent(expected_updated_at)}`,
      ),
    onSuccess: () =>
      void qc.invalidateQueries({ queryKey: ["claim-review-configs"] }),
    onError: (error) => {
      if (isStaleConfigurationError(error)) {
        void qc.invalidateQueries({ queryKey: ["claim-review-configs"] });
      }
    },
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
    mutationFn: (input: {
      source_client_id: string;
      config_ids: string[];
      target_versions: Record<string, string>;
    }) =>
      api.post<{ imported: ClaimReviewConfig[] }>(
        "/claim-review-configs/import",
        input,
      ),
    onSuccess: () =>
      void qc.invalidateQueries({ queryKey: ["claim-review-configs"] }),
    onError: (error) => {
      if (isStaleConfigurationError(error)) {
        void qc.invalidateQueries({ queryKey: ["claim-review-configs"] });
      }
    },
    meta: { localErrorHandling: true },
  });
}
