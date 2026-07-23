import { useEffect, useState } from "react";
import { Link, useNavigate, useSearch } from "@tanstack/react-router";
import {
  AlertTriangle,
  Building2,
  Calendar,
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
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Segmented } from "@/components/ui/segmented";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { ReportDownloadButton } from "@/components/operations/ReportDownloadButton";
import { ReportVersionActions } from "@/components/operations/ReportVersionActions";
import { useReportReadiness } from "@/api/reports";
import { useMe, usePolicyYears } from "@/api/hooks";
import { useSession } from "@/stores/session";
import { formatPolicyRange, isPastPolicyPeriod } from "@/lib/policy-year";
import type { PolicyYear } from "@/types";

// Reports Center — every downloadable/reviewable report, grouped by the team
// that owns it (the same four-team split the sidebar uses). Reports are
// version-per-year: a benefit-year picker at the top scopes every year-owned
// report so brokers can pull a past or draft year's documents, not just the
// current one. Some reports are file exports (download here); a few are
// interactive per-employee views that live on a working page, so their row
// links there instead of exporting.
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

/** Filename suffix carrying the benefit year + export date, so downloads for
 *  different years (or re-pulls) never collide in the downloads folder. */
function stamp(year: PolicyYear): string {
  return `${year.year}-${datestamp()}`;
}

function slug(insurer: string): string {
  return insurer
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-|-$/g, "");
}

/* ── Table shell — one row per report ────────────────────────────────── */
interface ReportRow {
  icon: LucideIcon;
  title: string;
  description: string;
  /** Short format tag: ".xlsx", ".docx", or "Interactive". */
  format: string;
  /** Download button or open-link for this row. */
  action: React.ReactNode;
}

function ReportTable({ rows }: { rows: ReportRow[] }) {
  return (
    <div className="overflow-hidden rounded-lg border border-border">
      <Table>
        <TableHeader>
          <TableRow className="hover:bg-transparent">
            <TableHead>Report</TableHead>
            <TableHead className="w-[120px]">Format</TableHead>
            <TableHead className="w-[190px] text-right">Action</TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {rows.map((r) => (
            <TableRow key={r.title}>
              <TableCell>
                <div className="flex items-start gap-3">
                  <r.icon className="mt-0.5 size-4 shrink-0 text-muted-foreground" />
                  <div className="space-y-0.5">
                    <div className="font-medium text-foreground">{r.title}</div>
                    <div className="text-xs leading-relaxed text-muted-foreground">
                      {r.description}
                    </div>
                  </div>
                </div>
              </TableCell>
              <TableCell>
                <Badge variant="outline">{r.format}</Badge>
              </TableCell>
              <TableCell className="text-right">
                <div className="flex justify-end">{r.action}</div>
              </TableCell>
            </TableRow>
          ))}
        </TableBody>
      </Table>
    </div>
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
    <Button asChild variant="outline" size="sm">
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
function CrReports({ year }: { year: PolicyYear }) {
  const rows: ReportRow[] = [
    {
      icon: FileText,
      title: "Fact-Find Form",
      description:
        "Client fact-find — scheme summary and basis-of-cover tables, pre-filled from this year's configured products.",
      format: ".docx",
      action: (
        <ReportVersionActions
          policyYearId={year.id}
          reportType="fact_find"
          scopeKey={null}
          createInput={{ report_type: "fact_find" }}
          mode="latest"
          hasMovement={false}
        />
      ),
    },
    {
      icon: FileSpreadsheet,
      title: "Quotation Slip",
      description:
        "Placement slip with rates left blank — send to insurers to quote against this year's configured benefits.",
      format: ".xlsx",
      action: (
        <ReportVersionActions
          policyYearId={year.id}
          reportType="quotation_slip"
          scopeKey={null}
          createInput={{ report_type: "quotation_slip" }}
          mode="latest"
          hasMovement={false}
        />
      ),
    },
    {
      icon: FileSpreadsheet,
      title: "Placement Slip",
      description:
        "Full placement slip — the priced schedule reproduced from this year's configuration.",
      format: ".xlsx",
      action: (
        <ReportVersionActions
          policyYearId={year.id}
          reportType="placement_slip"
          scopeKey={null}
          createInput={{ report_type: "placement_slip" }}
          mode="latest"
          hasMovement={false}
        />
      ),
    },
  ];
  return <ReportTable rows={rows} />;
}

/* ── Policy Admin — roster, coverage & insurer submissions ───────────── */
// Insurer + NRIC selections are held by ReportsPage so switching to another
// team tab (which unmounts this one) doesn't discard the broker's choice.
function PaReports({
  year,
  nric,
  setNric,
  insurer,
  setInsurer,
}: {
  year: PolicyYear;
  nric: NricMode;
  setNric: (v: NricMode) => void;
  insurer: string;
  setInsurer: (v: string) => void;
}) {
  const { data: readiness, isError } = useReportReadiness(year.id);

  const insurers = readiness?.insurers ?? [];
  useEffect(() => {
    if (insurers.length && !insurers.includes(insurer)) {
      setInsurer(insurers[0]);
    }
  }, [insurers, insurer, setInsurer]);

  const maskedParam = nric === "full" ? "&masked=false" : "";
  const listingReady = insurers.length > 0 && Boolean(insurer);

  const rows: ReportRow[] = [
    {
      icon: Building2,
      title: "Employee Listing for Insurer",
      description:
        "Per-insurer employee roster with member IDs and coverage — the submission the insurer bills against.",
      format: ".xlsx",
      action: (
        <ReportVersionActions
          policyYearId={year.id}
          reportType="employee_listing"
          scopeKey={insurer ? insurer.toLowerCase() : null}
          createInput={{
            report_type: "employee_listing",
            insurer,
            masked: nric === "masked",
          }}
          mode="versioned"
          hasMovement
          disabled={!listingReady}
          liveDownload={{
            path: `/policy-years/${year.id}/reports/employee-listing?insurer=${encodeURIComponent(insurer)}${maskedParam}`,
            filename: `employee-listing-for-insurer-report-${slug(insurer || "insurer")}-${stamp(year)}.xlsx`,
          }}
        />
      ),
    },
    {
      icon: Users,
      title: "Dependant Listing for Insurer",
      description:
        "Per-insurer dependant roster — covered spouses and children with their relationship and coverage.",
      format: ".xlsx",
      action: (
        <ReportVersionActions
          policyYearId={year.id}
          reportType="dependant_listing"
          scopeKey={insurer ? insurer.toLowerCase() : null}
          createInput={{
            report_type: "dependant_listing",
            insurer,
            masked: nric === "masked",
          }}
          mode="versioned"
          hasMovement
          disabled={!listingReady}
          liveDownload={{
            path: `/policy-years/${year.id}/reports/dependant-listing?insurer=${encodeURIComponent(insurer)}${maskedParam}`,
            filename: `dependant-listing-for-insurer-report-${slug(insurer || "insurer")}-${stamp(year)}.xlsx`,
          }}
        />
      ),
    },
    {
      icon: CalendarCheck,
      title: "Benefit Selection & Leave",
      description:
        "Enrollment selection status with buy/sell-leave — one row per employee for the latest window.",
      format: ".xlsx",
      action: (
        <ReportVersionActions
          policyYearId={year.id}
          reportType="benefit_selection"
          scopeKey={null}
          createInput={{
            report_type: "benefit_selection",
            masked: nric === "masked",
          }}
          mode="versioned"
          hasMovement={false}
          liveDownload={{
            path: `/policy-years/${year.id}/reports/benefit-selection${nric === "full" ? "?masked=false" : ""}`,
            filename: `benefit-selection-status-with-buy-sell-leave-report-${stamp(year)}.xlsx`,
          }}
        />
      ),
    },
    {
      icon: Users,
      title: "Employee Coverage Report",
      description:
        "Internal roster — every active employee with their matched products and NRIC masked.",
      format: ".xlsx",
      action: (
        <ReportDownloadButton
          path={`/employees/coverage-report/export?policy_year_id=${year.id}`}
          filename={`employee-coverage-${stamp(year)}.xlsx`}
          label="Download"
          size="sm"
        />
      ),
    },
    {
      icon: Users,
      title: "Dependant Coverage Report",
      description:
        "Internal dependant roster — covered dependants grouped by their employee, NRIC masked.",
      format: ".xlsx",
      action: (
        <ReportDownloadButton
          path={`/dependants/coverage-report/export?policy_year_id=${year.id}`}
          filename={`dependant-coverage-${stamp(year)}.xlsx`}
          label="Download"
          size="sm"
        />
      ),
    },
    {
      icon: Wallet,
      title: "Flex Coverage",
      description:
        "Flexible-benefit reconciliation — who is left out of a tier and every wallet balance. Open the flex overview to export.",
      format: "Interactive",
      action: (
        <OpenLink
          to="/configuration"
          search={{ tab: "flex" }}
          label="Open flex overview"
        />
      ),
    },
  ];

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

      <ReportTable rows={rows} />
    </div>
  );
}

/* ── Claims — adjudication register + utilization ────────────────────── */
function ClaimsReports({ year }: { year: PolicyYear }) {
  const rows: ReportRow[] = [
    {
      icon: Receipt,
      title: "Claims Register",
      description:
        "Every claim in the year — claimant, coverage line, amounts and decision, for reconciliation against the insurer's ledger.",
      format: ".xlsx",
      action: (
        <ReportDownloadButton
          path={`/claims/register?policy_year_id=${year.id}`}
          filename={`claims-register-${stamp(year)}.xlsx`}
          label="Download"
          size="sm"
        />
      ),
    },
    {
      icon: Gauge,
      title: "Utilization",
      description:
        "Per-employee approved / pending / remaining against each benefit limit. Pick an employee on the coverage page to view.",
      format: "Interactive",
      action: (
        <OpenLink
          to="/operations/coverage"
          search={{ view: "broker" }}
          label="Open in Coverage"
        />
      ),
    },
  ];
  return <ReportTable rows={rows} />;
}

/* ── IT / Firm — audit, spend & access (interactive surfaces) ────────── */
function ItReports() {
  const { data: me } = useMe();
  const canAdmin = me?.role === "broker_admin" || me?.role === "system_admin";
  const rows: ReportRow[] = [
    {
      icon: ScrollText,
      title: "Audit Log",
      description:
        "Recent activity for this company — configuration changes, matching runs, exports and member actions.",
      format: "Interactive",
      action: <OpenLink to="/configuration" label="Open audit feed" />,
    },
    {
      icon: Sparkles,
      title: "AI Spend",
      description:
        "Token usage and cost for AI extraction and claim review, with the circuit-breaker status.",
      format: "Interactive",
      action: <OpenLink to="/configuration/ai-provider" label="Open AI usage" />,
    },
  ];
  if (canAdmin) {
    rows.push({
      icon: UserCog,
      title: "Access & Companies",
      description:
        "Users, roles and company access across the firm — invite, disable and manage grants.",
      format: "Interactive",
      action: <OpenLink to="/admin" label="Open access admin" />,
    });
  }
  return <ReportTable rows={rows} />;
}

/** Status chip for the selected benefit year, so the broker always knows which
 *  version of the year they're exporting. */
function YearStatusBadge({ year }: { year: PolicyYear }) {
  if (year.status === "active") return <Badge variant="good">Current</Badge>;
  if (year.status === "draft") return <Badge variant="warn">Draft</Badge>;
  if (isPastPolicyPeriod(year.coverage_end))
    return <Badge variant="outline">Past year</Badge>;
  return <Badge variant="outline">Archived</Badge>;
}

export function ReportsPage() {
  const sessionYearId = useSession((s) => s.currentPolicyYearId);
  const { data: years = [] } = usePolicyYears();
  const navigate = useNavigate();
  const search = useSearch({ strict: false }) as { tab?: string };
  const tab: TeamKey = isTeam(search.tab) ? search.tab : "pa";

  // The Reports Center is year-scoped independently of the rest of the app: the
  // broker can pull a past or draft year's documents here without changing the
  // current benefit year everywhere else. Default to the session's current year,
  // then let this local selection drive every year-owned report.
  const [selectedYearId, setSelectedYearId] = useState<string | null>(null);
  useEffect(() => {
    if (years.length === 0) {
      if (selectedYearId !== null) setSelectedYearId(null);
      return;
    }
    if (selectedYearId && years.some((y) => y.id === selectedYearId)) return;
    const fallback =
      (sessionYearId && years.find((y) => y.id === sessionYearId)) ||
      years.find((y) => y.status === "active") ||
      years[0];
    setSelectedYearId(fallback.id);
  }, [years, selectedYearId, sessionYearId]);

  const selectedYear = years.find((y) => y.id === selectedYearId) ?? null;

  // Insurer/NRIC held here (not in PaReports) so the choice survives a tab
  // switch, which unmounts the inactive tab's content.
  const [nric, setNric] = useState<NricMode>("masked");
  const [insurer, setInsurer] = useState<string>("");

  return (
    <div className="space-y-5">
      <div className="flex flex-wrap items-center gap-3 rounded-lg border border-border bg-card px-4 py-3">
        <Calendar className="size-4 text-muted-foreground" />
        <span className="text-sm font-medium text-foreground">
          Benefit year
        </span>
        <Select
          value={selectedYearId ?? ""}
          onValueChange={setSelectedYearId}
          disabled={years.length === 0}
        >
          <SelectTrigger className="w-[300px]" aria-label="Benefit year">
            <SelectValue
              placeholder={
                years.length ? "Select a benefit year" : "No benefit years"
              }
            />
          </SelectTrigger>
          <SelectContent>
            {years.map((y) => (
              <SelectItem key={y.id} value={y.id}>
                {formatPolicyRange(y.coverage_start, y.coverage_end)}
                {y.status === "active" ? " · current" : ""}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
        {selectedYear && <YearStatusBadge year={selectedYear} />}
        <span className="ml-auto text-xs text-muted-foreground">
          Reports regenerate live from this year&apos;s configuration.
        </span>
      </div>

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
          {selectedYear ? <CrReports year={selectedYear} /> : <NoYearNotice />}
        </TabsContent>
        <TabsContent value="pa">
          {selectedYear ? (
            <PaReports
              year={selectedYear}
              nric={nric}
              setNric={setNric}
              insurer={insurer}
              setInsurer={setInsurer}
            />
          ) : (
            <NoYearNotice />
          )}
        </TabsContent>
        <TabsContent value="claims">
          {selectedYear ? (
            <ClaimsReports year={selectedYear} />
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
