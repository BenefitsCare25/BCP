/** Company HR view of enrolment e-forms — read and download only. */
import { useQuery } from "@tanstack/react-query";
import { hrApi } from "@/api/hrClient";
import {
  type FormRegister,
  type RegisterFilters,
  registerQuery,
} from "@/api/enrollmentForms";
import { downloadResponseAsFile } from "@/lib/download";

export interface HrFormWindow {
  id: string;
  name: string;
  policy_year: number;
  opens_at: string;
  closes_at: string;
  status: string;
}

export function useHrFormWindows() {
  return useQuery({
    queryKey: ["hr", "enrollment-form-windows"],
    queryFn: () => hrApi.get<HrFormWindow[]>("/hr/enrollment-forms/windows"),
    staleTime: 60_000,
  });
}

export function useHrForms(filters: RegisterFilters) {
  return useQuery({
    queryKey: ["hr", "enrollment-forms", filters],
    queryFn: () => hrApi.get<FormRegister>(`/hr/enrollment-forms${registerQuery(filters)}`),
    placeholderData: (previous) => previous,
  });
}

export async function downloadHrFormPdf(item: { id: string; reference_no: string }) {
  const res = await hrApi.downloadResponse(`/hr/enrollment-forms/${item.id}/pdf`);
  await downloadResponseAsFile(res, `Enrolment form ${item.reference_no}.pdf`);
}

export async function exportHrForms(filters: RegisterFilters, kind: "zip" | "xlsx") {
  const res = await hrApi.downloadResponse(
    `/hr/enrollment-forms/export.${kind}${registerQuery({ ...filters, source: "" }, false)}`,
  );
  await downloadResponseAsFile(res, `Enrolment forms.${kind}`);
}
