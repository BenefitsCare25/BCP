/** The member's side of the enrolment e-form: the form context around the
 * election deck, sign-and-send, and their own signed forms. */
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { portalApi } from "@/api/portalClient";
import type { ElectionIn } from "@/api/enrollment";
import type {
  FormSubmissionSummary,
  MemberFormContext,
} from "@/api/enrollmentForms";
import { triggerDownload } from "@/lib/download";

export interface FormSignInput {
  elections?: ElectionIn[];
  leave?: { action: "none" | "buy" | "sell"; days: number };
  particulars: { contact_no: string | null; email: string | null };
  pending_requests: { dependant_id: string; product_codes: string[] }[];
  accepted_clause_ids: string[];
  signature_name: string;
  confirm: boolean;
}

export function usePortalEnrollmentForm(enabled: boolean) {
  return useQuery({
    queryKey: ["portal", "enrollment-form"],
    queryFn: () => portalApi.get<MemberFormContext>("/portal/enrollment/form"),
    enabled,
    meta: { localErrorHandling: true },
    retry: false,
  });
}

export function useSignEnrollmentForm() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (input: FormSignInput) =>
      portalApi.post<FormSubmissionSummary>("/portal/enrollment/sign", input),
    // Signing submits the enrolment too, so everything portal-side that reads
    // enrolment state (the deck, the nav marker, Home) is refetched.
    onSuccess: () => void qc.invalidateQueries({ queryKey: ["portal"] }),
    meta: { localErrorHandling: true },
  });
}

export function useMyEnrollmentForms() {
  return useQuery({
    queryKey: ["portal", "enrollment-forms"],
    queryFn: () => portalApi.get<FormSubmissionSummary[]>("/portal/enrollment-forms"),
    meta: { localErrorHandling: true },
    retry: false,
  });
}

export async function downloadMyEnrollmentForm(form: FormSubmissionSummary): Promise<void> {
  const blob = await portalApi.blob(`/portal/enrollment-forms/${form.id}/pdf`);
  triggerDownload(blob, `Enrolment form ${form.reference_no}.pdf`);
}

export async function downloadFormResource(documentId: string, label: string): Promise<void> {
  const blob = await portalApi.blob(`/portal/enrollment/form/documents/${documentId}`);
  triggerDownload(blob, label);
}
