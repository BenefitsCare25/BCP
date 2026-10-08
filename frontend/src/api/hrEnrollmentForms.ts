/** Company HR view of enrolment e-forms — read and download only. */
import { useQuery } from "@tanstack/react-query";
import { hrApi } from "@/api/hrClient";
import {
  type FormRegister,
  type RegisterFilters,
  registerQuery,
} from "@/api/enrollmentForms";
import { downloadResponseAsFile } from "@/lib/download";
import { ApiError, errorStatus } from "@/lib/errors";

/** The bulk exports allow five downloads a minute. The limiter answers with a
 *  body that has no `detail` (`{"error": "Rate limit exceeded: …"}`), which the
 *  error toast used to print verbatim. */
const TOO_MANY_DOWNLOADS = "Too many downloads — try again in a minute.";

async function hrDownload(path: string): Promise<Response> {
  try {
    return await hrApi.downloadResponse(path);
  } catch (error) {
    if (errorStatus(error) === 429) throw new ApiError(TOO_MANY_DOWNLOADS, 429);
    throw error;
  }
}

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
  const res = await hrDownload(`/hr/enrollment-forms/${item.id}/pdf`);
  await downloadResponseAsFile(res, `Enrolment form ${item.reference_no}.pdf`);
}

/** The Excel summary is always masked for HR; the PDF ZIP is refused for
 *  every role but client_admin. */
export async function exportHrForms(filters: RegisterFilters, kind: "zip" | "xlsx") {
  const res = await hrDownload(
    `/hr/enrollment-forms/export.${kind}${registerQuery({ ...filters, source: "" }, false)}`,
  );
  await downloadResponseAsFile(res, `Enrolment forms.${kind}`);
}
