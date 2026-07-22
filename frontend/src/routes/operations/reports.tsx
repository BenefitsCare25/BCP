import { useEffect, useState } from "react";
import { Link, useNavigate, useSearch } from "@tanstack/react-router";
import {
  AlertTriangle,
  Building2,
  CalendarCheck,
  FileSpreadsheet,
  FileText,
  Gauge,
  Receipt,
  ScrollText,
  Sparkles,
  UserCog,
  Users,
  Wallet,
} from "lucide-react";
import type { LucideIcon } from "lucide-react";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { Button } from "@/components/ui/button";
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

// Reports Center — every downloadable/reviewable report, grouped by the team
// that owns it (the same four-team split the sidebar uses). Some reports are
// file exports (download here); a few are interactive per-employee views that
// live on a working page, so their card links there instead of exporting.
type TeamKey = "cr" | "pa" | "claims" | "it";

const TEAMS: { key: TeamKey; label: string }[] = [
  { key: "cr", label: "Client Relations" },
  { key: "pa", label: "Policy Admin" },
  { key: "claims", label: "Claims" },
  { key: "it", label: "IT / Firm" },
];

function isTeam(v: string | undefined): v is TeamKey {
  return v === "cr" || v === "pa" || v === "claims" || v === "it";
}

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

/** One report tile: icon + title + blurb, with a download button or link. */
function ReportCard({
  icon: Icon,
  title,
  description,
  children,
}: {
  icon: LucideIcon;
  title: string;
  description: string;
  children: React.ReactNode;
}) {
  return (
    <Card className="flex flex-col">
      <CardHeader className="pb-2">
        <CardTitle className="flex items-center gap-2 text-sm">
          <Icon className="size-4 text-muted-foreground" />
          {title}
        </CardTitle>
      </CardHeader>
      <CardContent className="flex flex-1 flex-col justify-between gap-4">
        <p className="text-xs leading-relaxed text-muted-foreground">
          {description}
        </p>
        <div>{children}</div>
      </CardContent>
    </Card>
  );
}

function CardGrid({ children }: { children: React.ReactNode }) {
  return (
    <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-3">{children}</div>
  );
}

/** "Open in <page>" action for reports that are interactive views, not files. */
function OpenLink({
  to,
  search,
  label,
}: {
  to: string;
  search?: Record<string, unknown>;
  label: string;
}) {
  return (
    <Button asChild variant="outline">
      <Link to={to} search={search}>
        {label}
      </Link>
    </Button>
  );
}

function NoYearNotice() {
  return (
    <p className="text-sm text-muted-foreground">
      Select a benefit year to generate reports.
    </p>
  );
}

/* ── Client Relations — quote/config stage documents ─────────────────── */
function CrReports({ policyYearId }: { policyYearId: string }) {
  return (
    <CardGrid>
      <ReportCard
        icon={FileText}
        title="Fact-Find Form"
        description="Client fact-find (.docx) — scheme summary and basis-of-cover tables, pre-filled from the configured products."
      >
        <ReportDownloadButton
          path={`/policy-years/${policyYearId}/fact-find-form`}
          filename={`fact-find-${datestamp()}.docx`}
          label="Download"
        />
      </ReportCard>
      <ReportCard
        icon={FileSpreadsheet}
        title="Quotation Slip"
        description="Placement slip with rates left blank (.xlsx) — send to insurers to quote against the configured benefits."
      >
        <ReportDownloadButton
          path={`/policy-years/${policyYearId}/reports/quotation-slip`}
          filename={`quotation-slip-${datestamp()}.xlsx`}
          label="Download"
        />
      </ReportCard>
      <ReportCard
        icon={FileSpreadsheet}
        title="Placement Slip"
        description="Full placement slip (.xlsx) — the priced schedule reproduced from the current configuration."
      >
        <ReportDownloadButton
          path={`/policy-years/${policyYearId}/reports/placement-slip`}
          filename={`placement-slip-${datestamp()}.xlsx`}
          label="Download"
        />
      </ReportCard>
    </CardGrid>
  );
}

/* ── Policy Admin — roster, coverage & insurer submissions ───────────── */
function PaReports({ policyYearId }: { policyYearId: string }) {
  const { data: readiness, isError } = useReportReadiness(policyYearId);
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
    <div className="space-y-5">
      {isError && (
        <div className="flex items-center gap-2 rounded-lg border border-error/40 bg-error-soft/40 px-3 py-2 text-sm text-error">
          <AlertTriangle className="size-4 shrink-0" />
          Couldn&apos;t load report readiness. Retry, or check your connection.
        </div>
      )}
      {readiness && insurers.length === 0 && (
        <div className="flex items-center gap-2 rounded-lg border border-warn/40 bg-warn-soft/40 px-3 py-2 text-sm text-warn-foreground">
          <AlertTriangle className="size-4 shrink-0" />
          Assign an insurer to each product under Schema &amp; Reference to
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

      <CardGrid>
        <ReportCard
          icon={Building2}
          title="Employee Listing for Insurer"
          description="Per-insurer employee roster (.xlsx) with member IDs and coverage — the submission the insurer bills against."
        >
          <ReportDownloadButton
            path={`/policy-years/${policyYearId}/reports/employee-listing?insurer=${encodeURIComponent(insurer)}${maskedParam}`}
            filename={`employee-listing-for-insurer-report-${slug(insurer || "insurer")}-${datestamp()}.xlsx`}
            label="Download"
            disabled={!listingReady}
          />
        </ReportCard>
        <ReportCard
          icon={Users}
          title="Dependant Listing for Insurer"
          description="Per-insurer dependant roster (.xlsx) — covered spouses and children with their relationship and coverage."
        >
          <ReportDownloadButton
            path={`/policy-years/${policyYearId}/reports/dependant-listing?insurer=${encodeURIComponent(insurer)}${maskedParam}`}
            filename={`dependant-listing-for-insurer-report-${slug(insurer || "insurer")}-${datestamp()}.xlsx`}
            label="Download"
            disabled={!listingReady}
          />
        </ReportCard>
        <ReportCard
          icon={CalendarCheck}
          title="Benefit Selection &amp; Leave"
          description="Enrollment selection status with buy/sell-leave (.xlsx) — one row per employee for the latest window."
        >
          <ReportDownloadButton
            path={`/policy-years/${policyYearId}/reports/benefit-selection${nric === "full" ? "?masked=false" : ""}`}
            filename={`benefit-selection-status-with-buy-sell-leave-report-${datestamp()}.xlsx`}
            label="Download"
          />
        </ReportCard>
        <ReportCard
          icon={Users}
          title="Employee Coverage Report"
          description="Internal roster (.xlsx) — every active employee with their matched products and NRIC masked."
        >
          <ReportDownloadButton
            path={`/employees/coverage-report/export?policy_year_id=${policyYearId}`}
            filename={`employee-coverage-${datestamp()}.xlsx`}
            label="Download"
          />
        </ReportCard>
        <ReportCard
          icon={Users}
          title="Dependant Coverage Report"
          description="Internal dependant roster (.xlsx) — covered dependants grouped by their employee, NRIC masked."
        >
          <ReportDownloadButton
            path={`/dependants/coverage-report/export?policy_year_id=${policyYearId}`}
            filename={`dependant-coverage-${datestamp()}.xlsx`}
            label="Download"
          />
        </ReportCard>
        <ReportCard
          icon={Wallet}
          title="Flex Coverage"
          description="Flexible-benefit reconciliation — who is left out of a tier and every wallet balance. Open the flex overview to export."
        >
          <OpenLink to="/configuration" label="Open flex overview" />
        </ReportCard>
      </CardGrid>
    </div>
  );
}

/* ── Claims — adjudication register + utilization ────────────────────── */
function ClaimsReports({ policyYearId }: { policyYearId: string }) {
  return (
    <CardGrid>
      <ReportCard
        icon={Receipt}
        title="Claims Register"
        description="Every claim in the year (.xlsx) — claimant, coverage line, amounts and decision, for reconciliation against the insurer's ledger."
      >
        <ReportDownloadButton
          path={`/claims/register?policy_year_id=${policyYearId}`}
          filename={`claims-register-${datestamp()}.xlsx`}
          label="Download"
        />
      </ReportCard>
      <ReportCard
        icon={Gauge}
        title="Utilization"
        description="Per-employee approved / pending / remaining against each benefit limit. Pick an employee on the coverage page to view."
      >
        <OpenLink
          to="/operations/coverage"
          search={{ view: "broker" }}
          label="Open in Coverage"
        />
      </ReportCard>
    </CardGrid>
  );
}

/* ── IT / Firm — audit, spend & access (interactive surfaces) ────────── */
function ItReports() {
  return (
    <CardGrid>
      <ReportCard
        icon={ScrollText}
        title="Audit Log"
        description="Recent activity for this company — configuration changes, matching runs, exports and member actions."
      >
        <OpenLink to="/configuration" label="Open audit feed" />
      </ReportCard>
      <ReportCard
        icon={Sparkles}
        title="AI Spend"
        description="Token usage and cost for AI extraction and claim review, with the circuit-breaker status."
      >
        <OpenLink to="/schema" label="Open AI usage" />
      </ReportCard>
      <ReportCard
        icon={UserCog}
        title="Access &amp; Companies"
        description="Users, roles and company access across the firm — invite, disable and manage grants."
      >
        <OpenLink to="/admin" label="Open access admin" />
      </ReportCard>
    </CardGrid>
  );
}

export function ReportsPage() {
  const policyYearId = useSession((s) => s.currentPolicyYearId);
  const navigate = useNavigate();
  const search = useSearch({ strict: false }) as { tab?: string };
  const tab: TeamKey = isTeam(search.tab) ? search.tab : "pa";

  return (
    <div className="space-y-5">
      <Tabs
        value={tab}
        onValueChange={(value) =>
          navigate({ to: "/reports", search: { tab: value } })
        }
      >
        <TabsList>
          {TEAMS.map((t) => (
            <TabsTrigger key={t.key} value={t.key}>
              {t.label}
            </TabsTrigger>
          ))}
        </TabsList>

        <TabsContent value="cr">
          {policyYearId ? (
            <CrReports policyYearId={policyYearId} />
          ) : (
            <NoYearNotice />
          )}
        </TabsContent>
        <TabsContent value="pa">
          {policyYearId ? (
            <PaReports policyYearId={policyYearId} />
          ) : (
            <NoYearNotice />
          )}
        </TabsContent>
        <TabsContent value="claims">
          {policyYearId ? (
            <ClaimsReports policyYearId={policyYearId} />
          ) : (
            <NoYearNotice />
          )}
        </TabsContent>
        <TabsContent value="it">
          <ItReports />
        </TabsContent>
      </Tabs>
    </div>
  );
}
