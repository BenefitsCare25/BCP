/** Online enrolment e-forms — shared types + the broker's setup/register hooks.
 *
 * The member (`portalEnrollmentForms.ts`) and HR (`hrEnrollmentForms.ts`)
 * surfaces import their types from here so the three portals read one shape. */
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "@/api/client";
import { useSession } from "@/stores/session";
import { downloadResponseAsFile } from "@/lib/download";

// ── Setup ────────────────────────────────────────────────────────────────────

export type ClauseScope = "all" | "dependants";

export interface FormClause {
  id: string;
  text: string;
  applies_to: ClauseScope;
}

export interface FormDocument {
  id: string;
  label: string;
  url: string | null;
  document_id: string | null;
  /** The uploaded file's own name (member context only), so a download keeps
   *  its extension. */
  file_name?: string | null;
}

export interface FormContribution {
  employee_pct: number | null;
  dependant_pct: number | null;
  /** Company pays the default plan; the member pays this % of the extra for a
   *  higher plan. Ignored when `employee_pct` is set. */
  upgrade_pct?: number | null;
}

export interface FormRule {
  product_code: string;
  requires_product_code: string;
}

export interface FormSettings {
  title: string;
  intro: string | null;
  submission_note: string | null;
  helpline: string | null;
  eligibility_notes: string[];
  clauses: FormClause[];
  documents: FormDocument[];
  contributions: Record<string, FormContribution>;
  rules: FormRule[];
}

export interface FormProduct {
  product_code: string;
  product_name: string | null;
  participation: string | null;
  has_dependant_cover: boolean;
  dependant_participation: string | null;
  has_upgrades: boolean;
}

export interface FormConfig {
  window_id: string;
  is_default: boolean;
  settings: FormSettings;
  products: FormProduct[];
  age_limits: Record<string, { min?: number; max?: number }>;
  updated_at: string | null;
}

// ── Member form context ─────────────────────────────────────────────────────

export interface FormParticulars {
  name: string | null;
  staff_id: string;
  id_masked: string;
  gender: string | null;
  dob: string | null;
  job_grade: string | null;
  date_of_hire: string | null;
  email: string | null;
  contact_no: string | null;
}

export interface FormDependant {
  id: string;
  name: string | null;
  relationship: string | null;
  role: "spouse" | "child" | null;
  gender: string | null;
  id_masked: string;
  dob: string | null;
  age_next_birthday: number | null;
  occupation: string | null;
  status: "active" | "pending";
  eligible: boolean;
  eligibility_note: string | null;
  /** Products whose own age window excludes this person. */
  ineligible_products: string[];
}

export interface ContributionTier {
  tier_key: string;
  mode: "tiered" | "flat";
  /** Full annual premium per family composition (EO/ES/EC/EF; EO alone for a
   *  flat or sum-insured plan). */
  premium: Partial<Record<"EO" | "ES" | "EC" | "EF", number>>;
  premium_per_dependant: number | null;
  /** The member's SHARE — own cover, then the family portion. */
  employee: number | null;
  family: Partial<Record<"spouse" | "child" | "both", number>>;
  per_dependant: number | null;
}

export interface ProductContribution {
  product_code: string;
  employee_pct: number | null;
  dependant_pct: number | null;
  upgrade_pct: number | null;
  gst_included: boolean;
  tiers: ContributionTier[];
}

/** One plan's line on the paper form: key benefit and sum insured. */
export interface PlanFact {
  product_code: string;
  tier_key: string;
  label: string;
  highlight: string | null;
  sum_insured: number | null;
  insurer: string | null;
}

export interface FormRuleView {
  product_code: string;
  product_name: string | null;
  requires_product_code: string;
  requires_product_name: string | null;
}

export type FormSource = "portal" | "paper";
export type FormStatus = "submitted" | "acknowledged" | "superseded" | "returned" | "cancelled";

export interface FormSubmissionSummary {
  id: string;
  reference_no: string;
  version: number;
  source: FormSource;
  status: FormStatus;
  submitted_at: string;
  signature_name: string | null;
  acknowledged_at: string | null;
  has_pdf: boolean;
  enrollment_status: string | null;
}

export interface MemberFormContext {
  company_name: string;
  title: string;
  policy_start: string;
  policy_end: string;
  closes_at: string;
  window_type: string;
  intro_lines: string[];
  submission_note: string | null;
  helpline: string | null;
  eligibility_notes: string[];
  clauses: FormClause[];
  documents: FormDocument[];
  rules: FormRuleView[];
  particulars: FormParticulars;
  compulsory: { product_code: string; product_name: string | null; plan_label: string | null }[];
  contributions: ProductContribution[];
  plans: PlanFact[];
  dependants: FormDependant[];
  latest: FormSubmissionSummary | null;
}

// ── Register ─────────────────────────────────────────────────────────────────

export interface FormRegisterItem {
  id: string;
  reference_no: string;
  version: number;
  source: FormSource;
  status: FormStatus;
  employee_id: string;
  staff_id: string | null;
  employee_name: string | null;
  id_masked: string;
  window_id: string | null;
  window_name: string | null;
  submitted_at: string;
  signature_name: string | null;
  acknowledged_at: string | null;
  enrollment_status: string | null;
  changes: number;
  has_pdf: boolean;
}

export interface FormRegister {
  items: FormRegisterItem[];
  total: number;
  offset: number;
  limit: number;
  counts: Partial<Record<FormStatus, number>>;
}

export interface RegisterFilters {
  windowId?: string;
  status?: FormStatus | "";
  source?: FormSource | "";
  query?: string;
  offset?: number;
  limit?: number;
}

/** Query string for the register and its exports — one builder, so a list and
 * the file exported from it always describe the same rows. */
export function registerQuery(f: RegisterFilters, paging = true): string {
  const p = new URLSearchParams();
  if (f.windowId) p.set("window_id", f.windowId);
  if (f.status) p.set("status", f.status);
  if (f.source) p.set("source", f.source);
  if (f.query?.trim()) p.set("q", f.query.trim());
  if (paging) {
    p.set("offset", String(f.offset ?? 0));
    p.set("limit", String(f.limit ?? 50));
  }
  const qs = p.toString();
  return qs ? `?${qs}` : "";
}

// ── Broker hooks ─────────────────────────────────────────────────────────────

function useClientId(): string | null {
  return useSession((s) => s.activeClientId);
}

export function useFormConfig(windowId: string | undefined) {
  const cid = useClientId();
  return useQuery({
    queryKey: ["enrollment-form-config", windowId, cid],
    queryFn: () => api.get<FormConfig>(`/enrollment-windows/${windowId}/form-config`),
    enabled: !!windowId,
  });
}

export function useSaveFormConfig(windowId: string | undefined) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (settings: FormSettings) =>
      api.put<FormConfig>(`/enrollment-windows/${windowId}/form-config`, settings),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["enrollment-form-config"] }),
  });
}

export async function uploadFormDocument(
  windowId: string,
  file: File,
): Promise<{ document_id: string; file_name: string }> {
  const body = new FormData();
  body.append("file", file);
  return api.upload(`/enrollment-windows/${windowId}/form-config/documents`, body);
}

export async function downloadBrokerFormDocument(
  windowId: string,
  documentId: string,
  label: string,
): Promise<void> {
  const res = await api.downloadResponse(
    `/enrollment-windows/${windowId}/form-config/documents/${documentId}`,
  );
  await downloadResponseAsFile(res, label);
}

export function useFormRegister(policyYearId: string | undefined, filters: RegisterFilters) {
  const cid = useClientId();
  return useQuery({
    queryKey: ["enrollment-forms", policyYearId, cid, filters],
    queryFn: () =>
      api.get<FormRegister>(
        `/policy-years/${policyYearId}/enrollment-forms${registerQuery(filters)}`,
      ),
    enabled: !!policyYearId,
    placeholderData: (previous) => previous,
  });
}

export function useAcknowledgeForm() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ id, note }: { id: string; note?: string }) =>
      api.post<FormSubmissionSummary>(`/enrollment-forms/${id}/acknowledge`, {
        note: note || null,
      }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["enrollment-forms"] }),
  });
}

export function useFilePaperForm(policyYearId: string | undefined) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (input: { employeeId: string; windowId?: string; note?: string; file: File }) => {
      const body = new FormData();
      body.append("employee_id", input.employeeId);
      if (input.windowId) body.append("window_id", input.windowId);
      if (input.note?.trim()) body.append("note", input.note.trim());
      body.append("file", input.file);
      return api.upload<FormSubmissionSummary>(
        `/policy-years/${policyYearId}/enrollment-forms/paper`,
        body,
      );
    },
    onSuccess: () => qc.invalidateQueries({ queryKey: ["enrollment-forms"] }),
  });
}

export async function downloadBrokerFormPdf(item: { id: string; reference_no: string }) {
  const res = await api.downloadResponse(`/enrollment-forms/${item.id}/pdf`);
  await downloadResponseAsFile(res, `Enrolment form ${item.reference_no}.pdf`);
}

export async function exportBrokerForms(
  policyYearId: string,
  filters: RegisterFilters,
  kind: "zip" | "xlsx",
): Promise<void> {
  const res = await api.downloadResponse(
    `/policy-years/${policyYearId}/enrollment-forms/export.${kind}${registerQuery(filters, false)}`,
  );
  await downloadResponseAsFile(res, `Enrolment forms.${kind}`);
}
