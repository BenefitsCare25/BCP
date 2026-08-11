/** Typed calls + query hooks for the employee portal surface. */
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import type {
  ElectionIn,
  EnrollmentDetail,
  EnrollmentOptions,
  EnrollmentWindow,
} from "@/api/enrollment";
import {
  useArtworkObjectUrl,
  type ArtworkState,
  type CardFace,
  type MemberCards,
} from "@/api/panelCards";
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

/** What this member may still do, and until when. SERVED — the client renders
 *  from `capabilities` and must NEVER re-derive the matrix from `state`. Which
 *  capabilities a state carries is the server's rule (`member_access.py`), and
 *  a copy of that table here would go on hiding (or showing) the wrong tab the
 *  day it changes. */
export interface PortalAccess {
  state: "active" | "run_off" | "settling" | "ended" | "unknown";
  capabilities: string[];
  /** Stated last day of service; null when they have not left. */
  last_day: string | null;
  /** The derived bound — can be set with no `last_day` (a terminated row with
   *  no date is still bounded, by the benefit year). */
  access_ends_on: string | null;
}

export interface PortalMe {
  member: PortalMember;
  access: PortalAccess;
  /** The member's employer, resolved server-side from the TOKEN's client — not
   *  from the URL, which is what makes it usable to check the URL against. */
  company: {
    /** The URL alias. `/portal/{slug}/…` is built from this. */
    slug: string | null;
    /** The broker's internal short handle ("CDL") — a fallback for display. */
    name: string;
    /** The registered name, which is what a member actually recognises. */
    legal_name: string | null;
  };
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

// ── Credential login (username + password) ──
export interface MemberTokenResult {
  token: string;
  expires_at: string;
  member: PortalMember;
}

export interface MemberChallengeResult {
  status: "mfa_required" | "password_reset_required";
  challenge_token: string;
}

export type MemberLoginResult = MemberTokenResult | MemberChallengeResult;

export function isMemberToken(r: MemberLoginResult): r is MemberTokenResult {
  return "token" in r;
}

export function useMemberLogin() {
  const setSession = usePortalSession((s) => s.setSession);
  return useMutation({
    mutationFn: (input: { identifier: string; password: string }) =>
      portalApi.postPublic<MemberLoginResult>("/portal/auth/login", input),
    onSuccess: (out) => {
      if (isMemberToken(out)) setSession(out.token, out.expires_at, out.member);
    },
    meta: { localErrorHandling: true },
  });
}

export function useMemberMfa() {
  const setSession = usePortalSession((s) => s.setSession);
  return useMutation({
    mutationFn: (input: { challenge_token: string; code: string }) =>
      portalApi.postPublic<MemberTokenResult>("/portal/auth/mfa", input),
    onSuccess: (out) => setSession(out.token, out.expires_at, out.member),
    meta: { localErrorHandling: true },
  });
}

export function useMemberSetPassword() {
  const setSession = usePortalSession((s) => s.setSession);
  // May return a full session OR an `mfa_required` challenge (2FA on + enrolled)
  // — a reset link never skips MFA. The caller drives the follow-up step.
  return useMutation({
    mutationFn: (input: { token: string; password: string }) =>
      portalApi.postPublic<MemberLoginResult>("/portal/auth/set-password", input),
    onSuccess: (out) => {
      if (isMemberToken(out)) setSession(out.token, out.expires_at, out.member);
    },
    meta: { localErrorHandling: true },
  });
}

export function usePortalMe() {
  return useQuery({
    queryKey: ["portal", "me"],
    queryFn: () => portalApi.get<PortalMe>("/portal/me"),
  });
}

// ── Member two-factor (self-service TOTP) ──
export interface MemberSecurityStatus {
  /** "none" | "pending" | "confirmed". */
  mfa_status: string;
  /** Whether the company has enabled 2FA for the employee portal. */
  mfa_available: boolean;
}

export interface MemberMfaStart {
  secret: string;
  otpauth_uri: string;
}

export function useMemberSecurityStatus() {
  return useQuery({
    queryKey: ["portal", "security-status"],
    queryFn: () =>
      portalApi.get<MemberSecurityStatus>("/portal/auth/security-status"),
    meta: { localErrorHandling: true },
  });
}

export function useMemberMfaEnrollStart() {
  return useMutation({
    mutationFn: () =>
      portalApi.post<MemberMfaStart>("/portal/auth/mfa/enroll/start", {}),
    meta: { localErrorHandling: true },
  });
}

export function useMemberMfaEnrollConfirm() {
  return useMutation({
    mutationFn: (code: string) =>
      // `verify`, not `post`: a 401 here means the typed code is wrong, and
      // signing the member out for a typo would make 2FA unenrollable.
      portalApi.verify<{ status: string; recovery_codes: string[] }>(
        "/portal/auth/mfa/enroll/confirm",
        { code },
      ),
    meta: { localErrorHandling: true },
  });
}

export function useMemberMfaDisable() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (password: string) =>
      // `verify` for the same reason as enrol-confirm: the 401 is about this
      // password, not about the session.
      portalApi.verify<{ status: string }>("/portal/auth/mfa/disable", {
        password,
      }),
    onSuccess: () =>
      void qc.invalidateQueries({ queryKey: ["portal", "security-status"] }),
    meta: { localErrorHandling: true },
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

// ── Panel e-cards ─────────────────────────────────────────────────────────────

export function usePortalCards() {
  return useQuery({
    queryKey: ["portal", "cards"],
    queryFn: () => portalApi.get<MemberCards>("/portal/cards"),
    meta: { localErrorHandling: true },
    retry: false,
  });
}

/** Card artwork for the member surface — the member token rides an
 * Authorization header, so the image is fetched as a blob and rendered from an
 * object URL. Mirrors useBrokerCardArtwork on the broker side. */
export function usePortalCardArtwork(
  cardId: string | null,
  face: CardFace,
  enabled = true,
): ArtworkState {
  return useArtworkObjectUrl(
    (path) => portalApi.blob(path),
    cardId && enabled ? `/portal/cards/${cardId}/artwork/${face}` : null,
  );
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
  /** Required-document slot this upload fills; null = additional document. */
  doc_type: string | null;
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
  sub_type: string | null;
  visit_type: string | null;
  referral_document_id: string | null;
  referral_document: PortalClaimDocument | null;
  referral_not_applicable: boolean;
  incurred_date: string;
  provider_name: string | null;
  invoice_number: string | null;
  doctor_name: string | null;
  diagnosis: string | null;
  remarks: string | null;
  amount_claimed: number;
  currency: string;
  amount_converted: number | null;
  amount_approved: number | null;
  status: string;
  dependant_id: string | null;
  dependant_name: string | null;
  submitted_at: string | null;
  decided_at: string | null;
  decision_notes: string | null;
  created_at: string;
  documents: PortalClaimDocument[];
  /** Slots this claim must fill at submit — drives the tagged-upload UI. */
  required_doc_slots: DocSlot[];
  /** Whether the claimant may still change this claim, and the sentence to
   * show when they may not.
   *
   * **SERVED — never re-derive this from `status`.** The window is "until the
   * broker decides", which is six statuses and grows; this page used to answer
   * it itself with `status === "draft" || status === "needs_info"` and that
   * mirror silently stopped being true the day the window widened. Same drift
   * class as mirroring the pending-status list (see `UsageLeaf`). */
  member_editable: boolean;
  member_edit_block: string | null;
  /** Optimistic-concurrency token. Sent back on every amendment so two devices
   * on one claim cannot silently clobber each other. */
  revision: number;
  amended_at: string | null;
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
  flex_category_name?: string | null;
  claim_type: string;
  sub_type?: string | null;
  visit_type?: string | null;
  incurred_date: string;
  provider_name: string;
  invoice_number: string;
  doctor_name?: string | null;
  diagnosis?: string | null;
  remarks?: string | null;
  amount_claimed: number;
  currency?: string;
  dependant_id?: string | null;
  referral_document_id?: string | null;
  referral_not_applicable?: boolean;
}

/** A required-document upload slot on the claim form. */
export interface DocSlot {
  key: string;
  label: string;
}

/** One claim-type dropdown entry — the sub-type rides in the selection. */
export interface ClaimTypeOption {
  label: string;
  sub_type: string | null;
  /** Pre-/post-hospitalisation consults must name the treating doctor. SERVED
   * by `claim_intake.requires_doctor_name`, never re-derived from the sub-type
   * label here — a relabel there would silently stop the form asking for a
   * field the server still requires. */
  requires_doctor_name: boolean;
  /** Required-document slots (unlisted-hospital default for inpatient). */
  doc_slots: DocSlot[];
  /** Hospitalisation/Day Surgery only: govt/private slot sets. */
  doc_slots_by_sector: Record<string, DocSlot[]> | null;
}

export interface InsuredClaimOption {
  product_code: string;
  product_name: string | null;
  plan_code: string | null;
  annual_policy_limit: string | null;
  covers_dependants: boolean;
  covered_dependant_ids: string[];
  /** Insurer + the member's ID with it (display-only, keys off claim type). */
  insurer: string | null;
  insurer_member_id: string | null;
  /** Claim-intake profile — drives the conditional form fields. */
  sub_types: string[];
  requires_referral: boolean;
  diagnosis_group: string | null;
  diagnosis_required: boolean;
  /** Outpatient / Inpatient / other grouping for the claim-type dropdown. */
  category: "outpatient" | "inpatient" | "other";
  /** Plan-aware dropdown entries (GP riders only when the schedule has them). */
  claim_types: ClaimTypeOption[];
}

export interface CoverageOptions {
  policy_year_start: string;
  policy_year_end: string;
  /** The dates an INSURED claim may be incurred on — the policy year clamped
   *  to this member's own cover (a leaver's ends on their last day). Served by
   *  the same function submit enforces, never re-derived here.
   *
   *  NULL when that window refuses every date (cover that ended before the
   *  period began — a leaver still inside their run-off against a flex scheme
   *  that starts mid-year). `insured` is empty in that case and `claim_block`
   *  says why; the bounds must never reach the date input as `min > max`. */
  claimable_from: string | null;
  claimable_to: string | null;
  insured: InsuredClaimOption[];
  flex: {
    currency: string | null;
    wallet_amount: number | null;
    flex_balance: number | null;
    categories: { name: string; sub_limit: number | null; note: string | null }[];
    doc_slots: DocSlot[];
    /** The flex scheme's own effective window, member-clamped. Genuinely
     *  different from the policy year — a scheme can start mid-year. */
    claimable_from: string | null;
    claimable_to: string | null;
  } | null;
  /** Why a claim kind was withheld — the member's cover ended before the period
   *  began, so its options are empty and its window null. Worded server-side
   *  (`ClaimWindow.empty_note`), which is also the 422 submit would raise. */
  claim_block: string | null;
  dependants: { id: string; name: string | null; relationship: string | null }[];
  currencies: string[];
  hospitals: { name: string; sector: "govt" | "private" }[];
}

export interface DiagnosisOption {
  label: string;
  icd10: string | null;
}

export interface DiagnosisSearch {
  group: string | null;
  items: DiagnosisOption[];
}

export function useCoverageOptions() {
  return useQuery({
    queryKey: ["portal", "coverage-options"],
    queryFn: () => portalApi.get<CoverageOptions>("/portal/coverage-options"),
    meta: { localErrorHandling: true },
    retry: false,
  });
}

/** One document's field reading in an autofill suggestion. */
export interface IntakeSuggestFields {
  provider_name: string | null;
  incurred_date: string | null;
  invoice_number: string | null;
  amount: number | null;
  currency: string | null;
  diagnosis: string | null;
  /** Always suggested when the document names a doctor; only asked for on
   * pre-/post-hospitalisation claims. */
  doctor_name: string | null;
}

/** One uploaded document in the autofill set. `claim_index` is set when the
 * set carries several distinct invoices — this document anchors its own claim
 * (0 = the claim prefilled now); null = supporting document. */
export interface IntakeSuggestDocument {
  file_name: string;
  /** 0-based position in the original upload — join File objects to documents
   *  on this (robust to duplicate names and to skipped files). */
  upload_index: number;
  detected_doc_type: string | null;
  doc_slot: string | null;
  claim_index: number | null;
  fields: IntakeSuggestFields | null;
  /** Field names of `fields` the AI was unsure about (only when `fields` is
   *  set) — the form flags them when it advances to this claim. */
  low_confidence: string[];
}

/** Document-driven autofill: what the AI read off an uploaded receipt, mapped
 * to claim-form fields. Every value is a suggestion the member confirms. */
export interface ClaimIntakeSuggestion {
  available: boolean;
  reason: string | null;
  document_type: string | null;
  /** Broker-recognised document type (e.g. "Discharge Summary"), if any —
   *  mirrors the primary document. */
  detected_doc_type: string | null;
  /** Required-document slot key the primary upload fills, when unambiguous. */
  doc_slot: string | null;
  /** Per-document classification for the whole uploaded set (up to 3). */
  documents: IntakeSuggestDocument[];
  /** ≥2 distinct invoices detected — one claim per invoice; the top-level
   *  fields prefill the first invoice's claim. */
  multi_claim: boolean;
  claimant: {
    kind: "self" | "dependant";
    dependant_id: string | null;
    name: string | null;
    confidence: number;
  } | null;
  /** Encoded claim-type selection (`insured:<code>:<idx>` / `flex:<name>`). */
  claim_selection: string | null;
  claim_candidates: string[];
  fields: IntakeSuggestFields;
  low_confidence: string[];
}

export function useExtractClaimIntake() {
  return useMutation({
    mutationFn: (files: File[]) => {
      const fd = new FormData();
      for (const file of files) fd.append("files", file);
      return portalApi.upload<ClaimIntakeSuggestion>("/portal/claims/intake", fd);
    },
    meta: { localErrorHandling: true },
  });
}

/** The whole year, not the server's first page.
 *
 * The endpoint defaults to 50 and there is no pager on any surface that reads
 * this, so the default silently truncated three things at once: the claims
 * ledger's state counts and month groups (a "Needs you 1" chip that omits an
 * older claim sent back for more information is the one omission that costs a
 * member money), and the usage page's per-claim itemisation, which is shown
 * only when the rows reconcile with the bucket to the cent and therefore
 * vanishes wholesale when a claim is missing from the window.
 *
 * 200 is `core/pagination.MAX_LIMIT`, so this is as complete as one request
 * gets; the ledger still discloses the remainder from `total` above it. The
 * extra payload is paid only by members who genuinely have that many claims —
 * exactly the ones the truncation was wrong for. */
const CLAIMS_PAGE = 200;

export function usePortalClaims() {
  return useQuery({
    queryKey: ["portal", "claims"],
    queryFn: () =>
      portalApi.get<PortalClaimList>(`/portal/claims?limit=${CLAIMS_PAGE}`),
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
    mutationFn: (input: { claimId: string; file: File; docType?: string }) => {
      const fd = new FormData();
      fd.append("file", input.file);
      if (input.docType) fd.append("doc_type", input.docType);
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

export function useClaimDiagnoses(productCode: string | null, q: string) {
  return useQuery({
    queryKey: ["portal", "claim-diagnoses", productCode, q],
    queryFn: () => {
      const params = new URLSearchParams();
      if (productCode) params.set("product_code", productCode);
      if (q) params.set("q", q);
      return portalApi.get<DiagnosisSearch>(
        `/portal/claim-diagnoses?${params.toString()}`,
      );
    },
    enabled: productCode !== null,
    meta: { localErrorHandling: true },
    retry: false,
    placeholderData: (prev) => prev,
  });
}

export function useReferralLetters(enabled: boolean) {
  return useQuery({
    queryKey: ["portal", "referral-letters"],
    queryFn: () =>
      portalApi.get<PortalClaimDocument[]>("/portal/referral-letters"),
    enabled,
    meta: { localErrorHandling: true },
    retry: false,
  });
}

export function useUploadReferralLetter() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (file: File) => {
      const fd = new FormData();
      fd.append("file", file);
      return portalApi.upload<PortalClaimDocument>(
        "/portal/referral-letters",
        fd,
      );
    },
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ["portal", "referral-letters"] });
    },
    meta: { localErrorHandling: true },
  });
}

export function useDeleteReferralLetter() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (docId: string) =>
      portalApi.delete<void>(`/portal/referral-letters/${docId}`),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ["portal", "referral-letters"] });
    },
    meta: { localErrorHandling: true },
  });
}

/** A partial correction to a claim. Only the keys PRESENT are applied — the
 * sheet sends what the member actually touched, so an edit to one field cannot
 * blank the rest. `expected_revision` is the `revision` the sheet opened on; a
 * mismatch 409s (`claim_amended`) rather than overwriting someone else's edit. */
export interface ClaimAmendInput {
  claim_type?: string;
  incurred_date?: string;
  provider_name?: string;
  invoice_number?: string;
  doctor_name?: string | null;
  diagnosis?: string | null;
  remarks?: string | null;
  amount_claimed?: number;
  currency?: string;
  expected_revision?: number;
}

export function useAmendClaim() {
  const qc = usePortalQueryInvalidator();
  return useMutation({
    mutationFn: (input: { claimId: string; patch: ClaimAmendInput }) =>
      portalApi.patch<PortalClaim>(
        `/portal/claims/${input.claimId}`,
        input.patch,
      ),
    onSuccess: qc,
    meta: { localErrorHandling: true },
  });
}

export function useDeleteClaimDocument() {
  const qc = usePortalQueryInvalidator();
  return useMutation({
    mutationFn: (input: { claimId: string; docId: string }) =>
      portalApi.delete<void>(
        `/portal/claims/${input.claimId}/documents/${input.docId}`,
      ),
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
