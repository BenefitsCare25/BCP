/** Enrolment → Enrolment forms: the register of signed online forms and filed
 * paper forms for the benefit year, plus the selected period's form setup.
 *
 * Acknowledging a form records that the broker received and checked it.
 * Applying the choices to live cover stays the Members tab's Confirm. */
import { FilePlus2 } from "lucide-react";
import { useMemo, useState } from "react";
import { useEnrollmentWindows } from "@/api/enrollment";
import {
  type RegisterFilters,
  downloadBrokerFormPdf,
  exportBrokerForms,
  useAcknowledgeForm,
  useFormRegister,
} from "@/api/enrollmentForms";
import {
  FormRegisterView,
  REGISTER_PAGE_SIZE,
} from "@/components/enrollmentForms/FormRegister";
import { FormSetupCard } from "@/components/enrollmentForms/FormSetupCard";
import { PaperFormSheet } from "@/components/enrollmentForms/PaperFormSheet";
import { Button } from "@/components/ui/button";
import { NativeSelect } from "@/components/ui/native-select";
import { useDebouncedValue } from "@/lib/use-debounced-value";
import { useSession } from "@/stores/session";

export function EnrollmentFormsPage({ readOnly = false }: { readOnly?: boolean }) {
  const policyYearId = useSession((s) => s.currentPolicyYearId) ?? undefined;
  const { data: windows } = useEnrollmentWindows(policyYearId);
  const sorted = useMemo(
    () => [...(windows ?? [])].sort((a, b) => b.opens_at.localeCompare(a.opens_at)),
    [windows],
  );
  const [setupWindowId, setSetupWindowId] = useState<string | undefined>(undefined);
  const setupWindow =
    sorted.find((w) => w.id === setupWindowId) ??
    sorted.find((w) => w.status === "open") ??
    sorted[0];

  const [filters, setFilters] = useState<RegisterFilters>({ limit: REGISTER_PAGE_SIZE });
  const query = useDebouncedValue(filters.query ?? "", 300);
  const effective = { ...filters, query };
  const register = useFormRegister(policyYearId, effective);
  const acknowledge = useAcknowledgeForm();
  const [paperOpen, setPaperOpen] = useState(false);

  if (!policyYearId) {
    return <p className="text-sm text-muted-foreground">Select a benefit year first.</p>;
  }

  return (
    <div className="space-y-6">
      <div className="flex flex-col gap-3 sm:flex-row sm:items-end sm:justify-between">
        <div>
          <h2 className="text-base font-semibold text-foreground">Enrolment forms</h2>
          <p className="mt-0.5 max-w-2xl text-sm text-muted-foreground">
            Forms members signed in the employee portal and paper forms filed here. HR sees the
            same list in the HR portal; each member can download their own.
          </p>
        </div>
        {!readOnly && (
          <Button variant="outline" className="h-11 sm:h-9" onClick={() => setPaperOpen(true)}>
            <FilePlus2 className="size-4" />
            File a paper form
          </Button>
        )}
      </div>

      <FormRegisterView
        data={register.data}
        isLoading={register.isLoading}
        error={register.error}
        onRetry={() => void register.refetch()}
        filters={filters}
        onFiltersChange={setFilters}
        windows={sorted.map((w) => ({ id: w.id, name: w.name }))}
        onDownload={downloadBrokerFormPdf}
        onExport={(kind) => exportBrokerForms(policyYearId, effective, kind)}
        onAcknowledge={readOnly ? undefined : (item) => acknowledge.mutateAsync({ id: item.id })}
        emptyHint="No forms yet. They appear here as members sign them during an open enrolment period."
      />

      {setupWindow ? (
        <div className="space-y-3">
          <div className="flex flex-wrap items-center gap-2">
            <span className="text-sm text-muted-foreground">Form setup for</span>
            <NativeSelect
              aria-label="Enrolment period to set up"
              className="h-11 sm:h-9"
              value={setupWindow.id}
              onChange={(e) => setSetupWindowId(e.target.value)}
            >
              {sorted.map((w) => (
                <option key={w.id} value={w.id}>
                  {w.name}
                </option>
              ))}
            </NativeSelect>
          </div>
          <FormSetupCard windowId={setupWindow.id} readOnly={readOnly} />
        </div>
      ) : (
        <p className="text-sm text-muted-foreground">
          Create an enrolment period on the Overview tab to set up its form.
        </p>
      )}

      <PaperFormSheet
        open={paperOpen}
        onOpenChange={setPaperOpen}
        policyYearId={policyYearId}
        windowId={filters.windowId ?? setupWindow?.id}
      />
    </div>
  );
}
