import { useEffect, useState } from "react";
import { AlertTriangle, Building2, CalendarCheck, Users } from "lucide-react";
import type { LucideIcon } from "lucide-react";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Segmented } from "@/components/ui/segmented";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { ReportDownloadButton } from "@/components/operations/ReportDownloadButton";
import { useReportReadiness } from "@/api/reports";
import { useSession } from "@/stores/session";

type NricMode = "masked" | "full";

function datestamp(): string {
  const d = new Date();
  const pad = (n: number) => String(n).padStart(2, "0");
  return `${d.getFullYear()}${pad(d.getMonth() + 1)}${pad(d.getDate())}`;
}

function slug(insurer: string): string {
  return insurer
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-|-$/g, "");
}

function ReportCard({
  icon: Icon,
  title,
  children,
}: {
  icon: LucideIcon;
  title: string;
  children: React.ReactNode;
}) {
  return (
    <Card className="flex flex-col">
      <CardHeader>
        <CardTitle className="flex items-center gap-2 text-base">
          <Icon className="size-4 text-muted-foreground" />
          {title}
        </CardTitle>
      </CardHeader>
      <CardContent className="mt-auto">{children}</CardContent>
    </Card>
  );
}

export function ReportsPage() {
  const policyYearId = useSession((s) => s.currentPolicyYearId);
  const { data: readiness, isError: readinessError } =
    useReportReadiness(policyYearId);
  const [nric, setNric] = useState<NricMode>("masked");
  const [insurer, setInsurer] = useState<string>("");

  const insurers = readiness?.insurers ?? [];
  useEffect(() => {
    if (insurers.length && !insurers.includes(insurer)) {
      setInsurer(insurers[0]);
    }
  }, [insurers, insurer]);

  const maskedParam = nric === "full" ? "&masked=false" : "";
  const listingReady = insurers.length > 0 && Boolean(insurer);

  return (
    <div className="space-y-5 max-w-5xl">
      {readinessError && (
        <div className="flex items-center gap-2 rounded-lg border border-error/40 bg-error-soft/40 px-3 py-2 text-sm text-error">
          <AlertTriangle className="size-4 shrink-0" />
          Couldn&apos;t load report readiness. The insurer listings may be
          unavailable — retry, or check your connection.
        </div>
      )}
      {readiness && insurers.length === 0 && (
        <div className="flex items-center gap-2 rounded-lg border border-warn/40 bg-warn-soft/40 px-3 py-2 text-sm text-warn-foreground">
          <AlertTriangle className="size-4 shrink-0" />
          Assign an insurer to each product under Attributes &amp; products to
          enable the insurer listings.
        </div>
      )}

      <div className="flex flex-wrap items-center gap-3">
        <span className="text-sm text-muted-foreground">Insurer</span>
        <Select
          value={insurer}
          onValueChange={setInsurer}
          disabled={!insurers.length}
        >
          <SelectTrigger className="w-[200px]" aria-label="Insurer">
            <SelectValue
              placeholder={
                insurers.length ? "Select insurer" : "No insurers configured"
              }
            />
          </SelectTrigger>
          <SelectContent>
            {insurers.map((ins) => (
              <SelectItem key={ins} value={ins}>
                {ins}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
        <span className="ml-2 text-sm text-muted-foreground">NRIC/FIN</span>
        <Segmented
          value={nric}
          onChange={setNric}
          options={[
            { value: "masked", label: "Masked" },
            { value: "full", label: "Unmasked" },
          ]}
        />
      </div>

      <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-3">
        <ReportCard icon={Building2} title="Employee Listing for Insurer">
          <ReportDownloadButton
            path={`/policy-years/${policyYearId}/reports/employee-listing?insurer=${encodeURIComponent(insurer)}${maskedParam}`}
            filename={`employee-listing-for-insurer-report-${slug(insurer || "insurer")}-${datestamp()}.xlsx`}
            label="Download report"
            disabled={!policyYearId || !listingReady}
          />
        </ReportCard>

        <ReportCard icon={Users} title="Dependant Listing for Insurer">
          <ReportDownloadButton
            path={`/policy-years/${policyYearId}/reports/dependant-listing?insurer=${encodeURIComponent(insurer)}${maskedParam}`}
            filename={`dependant-listing-for-insurer-report-${slug(insurer || "insurer")}-${datestamp()}.xlsx`}
            label="Download report"
            disabled={!policyYearId || !listingReady}
          />
        </ReportCard>

        <ReportCard icon={CalendarCheck} title="Benefit Selection &amp; Leave">
          <ReportDownloadButton
            path={`/policy-years/${policyYearId}/reports/benefit-selection${nric === "full" ? "?masked=false" : ""}`}
            filename={`benefit-selection-status-with-buy-sell-leave-report-${datestamp()}.xlsx`}
            label="Download report"
            disabled={!policyYearId}
          />
        </ReportCard>
      </div>
    </div>
  );
}
