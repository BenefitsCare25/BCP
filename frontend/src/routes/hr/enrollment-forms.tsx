/** HR — every enrolment form filed for the company (online and scanned paper),
 * downloadable one at a time, as a ZIP, or as an Excel summary. Read-only:
 * the broker reviews and acknowledges. */
import { useState } from "react";
import type { RegisterFilters } from "@/api/enrollmentForms";
import {
  downloadHrFormPdf,
  exportHrForms,
  useHrFormWindows,
  useHrForms,
} from "@/api/hrEnrollmentForms";
import {
  FormRegisterView,
  REGISTER_PAGE_SIZE,
} from "@/components/enrollmentForms/FormRegister";
import { useDebouncedValue } from "@/lib/use-debounced-value";
import { useDocumentTitle } from "@/lib/useDocumentTitle";

export function HrEnrollmentFormsPage() {
  useDocumentTitle("Enrolment forms");
  const [filters, setFilters] = useState<RegisterFilters>({ limit: REGISTER_PAGE_SIZE });
  const query = useDebouncedValue(filters.query ?? "", 300);
  const effective = { ...filters, query };
  const forms = useHrForms(effective);
  const windows = useHrFormWindows();

  return (
    <div className="space-y-3">
      <div>
        <h1 className="sr-only">Enrolment forms</h1>
        <p className="text-sm text-muted-foreground">
          Your broker reviews each form before cover changes.
        </p>
      </div>
      <FormRegisterView
        data={forms.data}
        isLoading={forms.isLoading}
        error={forms.error}
        onRetry={() => void forms.refetch()}
        filters={filters}
        onFiltersChange={setFilters}
        windows={(windows.data ?? []).map((w) => ({ id: w.id, name: `${w.name} (${w.policy_year})` }))}
        showSource={false}
        onDownload={downloadHrFormPdf}
        onExport={(kind) => exportHrForms(effective, kind)}
        emptyHint={filters.query || filters.status || filters.windowId
          ? "No forms match these filters. Change the filters or clear your search."
          : "No enrolment forms yet. Signed online forms and filed paper forms appear here."}
      />
    </div>
  );
}
