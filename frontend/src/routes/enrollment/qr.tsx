/** Enrolment → QR forms: the no-login alternative to the employee and HR
 * portals. One shared QR per period for employees, the queue of submissions to
 * match to the roster, and prepared downloads HR opens with a link + PIN. */
import { useMemo, useState } from "react";
import { useEnrollmentWindows } from "@/api/enrollment";
import { useMe } from "@/api/hooks";
import { HrDownloadPanel } from "@/components/qrEnrolment/HrDownloadPanel";
import { QrEntryCard } from "@/components/qrEnrolment/QrEntryCard";
import { QrIntakeQueue } from "@/components/qrEnrolment/QrIntakeQueue";
import { NativeSelect } from "@/components/ui/native-select";
import { isBrokerAdminRole } from "@/lib/roles";
import { useSession } from "@/stores/session";

export function EnrollmentQrPage({ readOnly = false }: { readOnly?: boolean }) {
  const policyYearId = useSession((s) => s.currentPolicyYearId) ?? undefined;
  const { data: windows } = useEnrollmentWindows(policyYearId);
  const { data: me } = useMe();
  const sorted = useMemo(
    () => [...(windows ?? [])].sort((a, b) => b.opens_at.localeCompare(a.opens_at)),
    [windows],
  );
  const [chosen, setChosen] = useState<string | undefined>(undefined);
  const period = sorted.find((w) => w.id === chosen) ?? sorted.find((w) => w.status === "open") ?? sorted[0];
  const companyName =
    me?.accessible_clients.find((c) => c.id === me.active_client_id)?.name ?? "Company";
  const canIssue = !readOnly && isBrokerAdminRole(me?.role);

  if (!policyYearId) {
    return <p className="text-sm text-muted-foreground">Select a benefit year first.</p>;
  }
  return (
    <div className="space-y-8">
      <div className="flex flex-col gap-3 sm:flex-row sm:items-end sm:justify-between">
        <div>
          <h2 className="text-base font-semibold text-foreground">QR forms</h2>
          <p className="mt-0.5 max-w-2xl text-sm text-muted-foreground">
            A no-login way for employees to send their enrolment form, and for HR to collect
            the submitted forms. The employee and HR portals keep working as usual.
          </p>
        </div>
        {sorted.length > 1 && period && (
          <NativeSelect
            aria-label="Enrolment period"
            className="h-11 sm:h-9"
            value={period.id}
            onChange={(e) => setChosen(e.target.value)}
          >
            {sorted.map((w) => <option key={w.id} value={w.id}>{w.name}</option>)}
          </NativeSelect>
        )}
      </div>
      {period ? (
        <QrEntryCard window={period} companyName={companyName} readOnly={readOnly || !canIssue} />
      ) : (
        <p className="text-sm text-muted-foreground">
          Create an enrolment period on the Overview tab to turn on its QR form.
        </p>
      )}
      <QrIntakeQueue policyYearId={policyYearId} windowId={period?.id} readOnly={readOnly} />
      <HrDownloadPanel
        policyYearId={policyYearId}
        windows={sorted}
        defaultWindowId={period?.id}
        companyName={companyName}
        canIssue={canIssue}
      />
    </div>
  );
}
