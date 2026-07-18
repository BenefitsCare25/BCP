import { useEffect, useState } from "react";
import {
  AlertTriangle,
  Building2,
  CalendarCheck,
  CheckCircle2,
  FileSpreadsheet,
  Users,
} from "lucide-react";
import type { LucideIcon } from "lucide-react";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { PageGuide } from "@/components/ui/page-guide";
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
  description,
  includes,
  children,
}: {
  icon: LucideIcon;
  title: string;
  description: string;
  includes: string;
  children: React.ReactNode;
}) {
  return (
    <Card className="flex flex-col">
      <CardHeader>
        <CardTitle className="flex items-center gap-2 text-base">
          <Icon className="size-4 text-muted-foreground" />
          {title}
        </CardTitle>
        <CardDescription>{description}</CardDescription>
      </CardHeader>
      <CardContent className="mt-auto space-y-3">
        <p className="text-xs text-muted-foreground">{includes}</p>
        {children}
      </CardContent>
    </Card>
  );
}

function NricToggle({
  value,
  onChange,
}: {
  value: NricMode;
  onChange: (v: NricMode) => void;
}) {
  return (
    <div className="flex items-center justify-between gap-2">
      <span className="text-xs text-muted-foreground">NRIC/FIN</span>
      <Segmented
        value={value}
        onChange={onChange}
        options={[
          { value: "masked", label: "Masked" },
          { value: "full", label: "Unmasked" },
        ]}
      />
    </div>
  );
}

export function ReportsPage() {
  const policyYearId = useSession((s) => s.currentPolicyYearId);
  const { data: readiness } = useReportReadiness(policyYearId);
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

  const warnings: string[] = [];
  if (readiness) {
    if (readiness.products_without_insurer.length) {
      warnings.push(
        `Products without an insurer: ${readiness.products_without_insurer.join(", ")} — assign them under Attributes & products.`,
      );
    }
    if (readiness.plans_missing_report_label.length) {
      const sample = readiness.plans_missing_report_label
        .slice(0, 4)
        .map((p) => `${p.product_code} ${p.plan_code}`)
        .join(", ");
      warnings.push(
        `${readiness.plans_missing_report_label.length} plan(s) have no insurer report label (${sample}…) — the internal plan name is used until set on the category card.`,
      );
    }
    if (readiness.employees_missing_nric) {
      warnings.push(
        `${readiness.employees_missing_nric} of ${readiness.employee_count} employees have no NRIC/FIN on file.`,
      );
    }
    const missingIds = Object.entries(
      readiness.employees_missing_member_id,
    ).filter(([, n]) => n > 0);
    if (missingIds.length) {
      warnings.push(
        missingIds
          .map(([ins, n]) => `${n} employees missing their ${ins} member ID`)
          .join("; ") + " — upload via the member-listing template.",
      );
    }
  }

  return (
    <div className="space-y-5 max-w-5xl">
      <PageGuide
        purpose="Download insurer-format reports for billing and membership submission. Every download is audit-logged; unmasked NRIC/FIN requires write access and is intended only for files sent to the insurer."
        connections={[
          {
            label: "Insurer setup",
            description:
              "Products are grouped into reports by their assigned insurer (Attributes & products); plan labels come from each category card.",
          },
          {
            label: "Underwriting",
            description:
              "Pending U/W and Last Accepted figures come from the Underwriting queue (free cover limits on the Configuration page).",
          },
          {
            label: "PII handling",
            description:
              "NRIC/FIN is masked by default. Unmasked downloads are recorded in the audit log.",
          },
        ]}
      />

      {warnings.length > 0 && (
        <div className="rounded-lg border border-warn/40 bg-warn-soft/40 p-3 space-y-1">
          <div className="flex items-center gap-2 text-sm font-medium text-warn-foreground">
            <AlertTriangle className="size-4" /> Report readiness
          </div>
          <ul className="list-disc pl-5 text-xs text-muted-foreground space-y-0.5">
            {warnings.map((w, i) => (
              <li key={i}>{w}</li>
            ))}
          </ul>
        </div>
      )}
      {readiness && warnings.length === 0 && (
        <div className="flex items-center gap-2 rounded-lg border border-border bg-muted/40 p-3 text-sm text-muted-foreground">
          <CheckCircle2 className="size-4 text-emerald-500" /> Insurer setup
          complete —
          all reports ready.
        </div>
      )}

      <div className="flex items-center gap-3">
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
        <NricToggle value={nric} onChange={setNric} />
      </div>

      <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-3">
        <ReportCard
          icon={Building2}
          title="Employee Listing for Insurer"
          description="Membership listing with demographics, basis of cover, eligible / pending / accepted sums insured per product."
          includes="Active employees plus in-period leavers, columns for the selected insurer's products only."
        >
          <ReportDownloadButton
            path={`/policy-years/${policyYearId}/reports/employee-listing?insurer=${encodeURIComponent(insurer)}${maskedParam}`}
            filename={`employee-listing-for-insurer-report-${slug(insurer || "insurer")}-${datestamp()}.xlsx`}
            label="Download report"
            disabled={!policyYearId || !listingReady}
          />
        </ReportCard>

        <ReportCard
          icon={Users}
          title="Dependant Listing for Insurer"
          description="Dependant listing with relationship, spouse/child sums insured and family grouping per product."
          includes="Dependants covered by at least one of the selected insurer's products."
        >
          <ReportDownloadButton
            path={`/policy-years/${policyYearId}/reports/dependant-listing?insurer=${encodeURIComponent(insurer)}${maskedParam}`}
            filename={`dependant-listing-for-insurer-report-${slug(insurer || "insurer")}-${datestamp()}.xlsx`}
            label="Download report"
            disabled={!policyYearId || !listingReady}
          />
        </ReportCard>

        <ReportCard
          icon={CalendarCheck}
          title="Benefit Selection & Leave"
          description="Selection status per employee for the latest enrollment window, with buy/sell-leave days and price tags."
          includes="Every active employee plus in-period leavers with their last day of service."
        >
          <ReportDownloadButton
            path={`/policy-years/${policyYearId}/reports/benefit-selection${nric === "full" ? "?masked=false" : ""}`}
            filename={`benefit-selection-status-with-buy-sell-leave-report-${datestamp()}.xlsx`}
            label="Download report"
            disabled={!policyYearId}
          />
        </ReportCard>
      </div>

      <p className="flex items-center gap-1.5 text-xs text-muted-foreground">
        <FileSpreadsheet className="size-3.5" />
        Reports are generated live from the current roster, coverage,
        underwriting and enrollment data. The member-listing upload template is
        on the Roster page.
      </p>
    </div>
  );
}
