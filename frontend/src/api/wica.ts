import { api } from "@/api/client";

export const DOCUMENT_TYPES = ["Contractual Agreement", "Death Certificate", "Employment Pass", "Medical Bill", "Medical Certificate", "Medical Report", "MOM I-Report", "Salary Voucher", "Work Permit"];
export const BENEFIT_TYPES = ["Death", "Permanent Total Disablement", "Permanent Partial Disablement", "Temporary Disablement", "Medical", "Common Law", "Others"];
export const STATUSES: Record<string, string> = { untagged: "Untagged", supporting: "Supporting document", submitted: "Submitted", pending_insurer: "Pending insurer approval", settled: "Settled", rejected: "Rejected" };
export type WicaPeriod = { id: string; label: string; start_date: string; end_date: string; grace_days: number | null; in_use?: boolean };
export type WicaSettings = { enabled: boolean; revision: number; periods: WicaPeriod[] };
export type WicaDocument = {
  id: string; file_name: string; size_bytes: number; created_at: string; doc_type: string | null;
  document_date: string | null; benefit_type: string | null; claim_id: string | null; status: string;
  related_ids: string[]; provider: string; invoice_number: string; incurred_amount: string | number | null;
  settlement_amount: string | number | null; settlement_date: string | null; insurer_reference: string; remarks: string;
};
export type WicaPack = { id: string; created_at: string; sent_on: string | null; sent_reference: string; document_count: number };
export type WicaIncident = {
  id: string; period_id: string; employee_id: string | null; employee_name: string; staff_id: string;
  incident_date: string; report_number: string; remarks: string; revision: number; created_at: string;
  documents: WicaDocument[]; packs: WicaPack[];
};
export type WicaEmployee = { id: string; employee_name: string; staff_id: string };

/** Pin tenant before token acquisition: a company switch cannot retarget a write. */
export function wicaApi(clientId: string) {
  const headers = { "X-Inspro-Client": clientId };
  return {
    get: <T>(path: string) => api.get<T>(`/wica${path}`, { headers }),
    post: <T>(path: string, body: unknown) => api.post<T>(`/wica${path}`, body, { headers }),
    patch: <T>(path: string, body: unknown) => api.patch<T>(`/wica${path}`, body, { headers }),
    put: <T>(path: string, body: unknown) => api.put<T>(`/wica${path}`, body, { headers }),
    upload: (path: string, form: FormData) => api.upload<WicaIncident>(`/wica${path}`, form, headers),
    download: async (path: string, name: string) => {
      const blob = await api.download(`/wica${path}`, headers);
      const url = URL.createObjectURL(blob);
      const link = document.createElement("a");
      link.href = url; link.download = name; link.click();
      setTimeout(() => URL.revokeObjectURL(url), 30_000);
    },
  };
}
