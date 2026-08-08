import { useEffect, useState } from "react";
import { Link, useNavigate, useSearch } from "@tanstack/react-router";
import {
  AlertTriangle,
  Building2,
  Calendar,
  CalendarCheck,
  ClipboardCheck,
  Coins,
  FileSpreadsheet,
  FileText,
  Gauge,
  Receipt,
  ScrollText,
  Sparkles,
  UserCog,
  UserMinus,
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
import { ReportBundleCard } from "@/components/operations/ReportBundleCard";
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
//
// Flex is its own tab, NOT a Policy Admin section: everything in it is funded by
// the member's OWN flex wallet, so it is never insurer-scoped — and the Policy
// Admin tab is headed by an insurer picker. Sitting under that picker made the
// enrollment/leave and wallet reports read as an AIA submission when the insurer
// is meaningless to them.
type TeamKey = "cr" | "pa" | "flex" | "claims" | "it";

const TEAMS: { key: TeamKey; label: string }[] = [
  { key: "cr", label: "Client Relations" },
  { key: "pa", label: "Policy Admin" },
  { key: "flex", label: "Flex" },
  { key: "claims", label: "Claims" },
  { key: "it", label: "IT / Firm" },
];

function isTeam(v: string | undefined): v is TeamKey {
  return TEAMS.some((t) => t.key === v);
}

type NricMode = "masked" | "full";
// Which slice of the roster the internal listings cover. `all` is the default
// (and the incumbent's), because "who is on file" is a wider question than
// "who is billable" — a leaver missing from a roster export reads as data loss.
type RosterScope = "all" | "active";

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

/** A titled group of rows, with the controls that actually scope THEM in its
 *  header. A filter rendered above the whole tab reads as scoping every row in
 *  it — which is how the insurer picker came to sit over reports no insurer
 *  submission touches. */
function ReportSection({
  title,
  hint,
  controls,
  rows,
}: {
  title: string;
  hint: string;
  controls?: React.ReactNode;
  rows: ReportRow[];
}) {
  return (
    <section className="space-y-2">
      <div className="flex flex-wrap items-center justify-between gap-x-4 gap-y-2">
        <div>
          <h3 className="text-sm font-semibold text-foreground">{title}</h3>
          <p className="text-xs text-muted-foreground">{hint}</p>
        </div>
        {controls}
      </div>
      <ReportTable rows={rows} />
    </section>
  );
}

/** NRIC/FIN masking toggle — shared by the tabs whose exports carry NRICs. */
function NricToggle({
  nric,
  setNric,
}: {
  nric: NricMode;
  setNric: (v: NricMode) => void;
}) {
  return (
    <div className="flex items-center gap-2">
      <span className="text-sm text-muted-foreground">NRIC/FIN</span>
      <Segmented
        value={nric}
        onChange={setNric}
        options={[
          { value: "masked", label: "Masked" },
          { value: "full", label: "Unmasked" },
        ]}
      />
    </div>
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
        "Placement slip with rates left blank — send to insurers to quote against this year's configured benefits. Member counts, tier splits and sums insured come from the current roster.",
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
        "Full placement slip — the priced schedule reproduced from this year's configuration, costed on the current roster.",
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
  rosterScope,
  setRosterScope,
}: {
  year: PolicyYear;
  nric: NricMode;
  setNric: (v: NricMode) => void;
  insurer: string;
  setInsurer: (v: string) => void;
  rosterScope: RosterScope;
  setRosterScope: (v: RosterScope) => void;
}) {
  const { data: readiness, isError } = useReportReadiness(year.id);

  const insurers = readiness?.insurers ?? [];
  const missingInsurer = readiness?.products_without_insurer ?? [];
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
  ];

  // `all` = everyone on file (the incumbent's default, and wider than the
  // insurer listings' active + in-period-leaver population). The toggle sits in
  // the section header because it scopes both listings and nothing else.
  const statusParam = `?employee_status=${rosterScope}${maskedParam}`;

  const internalRows: ReportRow[] = [
    {
      icon: Users,
      title: "Employee Listing",
      description:
        "The full company roster across every insurer, with each product's default plan and family grouping.",
      format: ".xlsx",
      action: (
        <ReportDownloadButton
          path={`/policy-years/${year.id}/reports/built-in-employee-listing${statusParam}`}
          filename={`built-in-employee-listing-report-${stamp(year)}.xlsx`}
          label="Download"
          size="sm"
        />
      ),
    },
    {
      icon: Users,
      title: "Dependant Listing",
      description:
        "Every dependant on file with their details and status — including those nobody covers yet.",
      format: ".xlsx",
      action: (
        <ReportDownloadButton
          path={`/policy-years/${year.id}/reports/built-in-dependant-listing${statusParam}`}
          filename={`built-in-dependant-listing-report-${stamp(year)}.xlsx`}
          label="Download"
          size="sm"
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
      icon: UserMinus,
      title: "Leaver Summary",
      description:
        "Everyone who left during the period, with their cover window and final wallet position.",
      format: ".xlsx",
      action: (
        <ReportDownloadButton
          path={`/policy-years/${year.id}/reports/leaver-summary${nric === "full" ? "?masked=false" : ""}`}
          filename={`leaver-summary-report-${stamp(year)}.xlsx`}
          label="Download"
          size="sm"
        />
      ),
    },
    {
      icon: UserMinus,
      title: "Leaver Details",
      description:
        "Leavers' claims — including anything still in flight when their cover ended.",
      format: ".xlsx",
      action: (
        <ReportDownloadButton
          path={`/policy-years/${year.id}/reports/leaver-details${nric === "full" ? "?masked=false" : ""}`}
          filename={`leaver-details-report-${stamp(year)}.xlsx`}
          label="Download"
          size="sm"
        />
      ),
    },
    {
      icon: ClipboardCheck,
      title: "Underwriting Report",
      description:
        "Internal underwriting register — one row per life and product above the Non-Evidence Limit, with the insurer case status, decision and sums insured. Covers every insurer.",
      format: ".xlsx",
      action: (
        <ReportDownloadButton
          path={`/policy-years/${year.id}/reports/underwriting${nric === "full" ? "?masked=false" : ""}`}
          filename={`underwriting-report-${stamp(year)}.xlsx`}
          label="Download"
          size="sm"
        />
      ),
    },
  ];

  return (
    <div className="space-y-6">
      {isError && (
        <div className="flex items-center gap-2 rounded-lg border border-error/40 bg-error-soft/40 px-3 py-2 text-sm text-error">
          <AlertTriangle className="size-4 shrink-0" />
          Couldn&apos;t load report readiness. Retry, or check your connection.
        </div>
      )}
      {/* A product with no insurer is silently absent from EVERY insurer
          submission, so name the products rather than only warning when none
          of them has one. The insurer is a per-benefit-year placement fact —
          it lives on the product's own Header & Policy tab, not in the firm
          product catalog.
          Text is `text-warn` (amber), NOT `text-warn-foreground` — that token
          is white, meant for the SOLID warn background; over `bg-warn-soft` it
          rendered as unreadable white-on-cream. */}
      {readiness && missingInsurer.length > 0 && (
        <div className="flex items-start gap-2 rounded-lg border border-warn/40 bg-warn-soft/40 px-3 py-2 text-sm text-warn">
          <AlertTriangle className="mt-0.5 size-4 shrink-0" />
          <p>
            <span className="font-medium">{missingInsurer.join(", ")}</span>{" "}
            {missingInsurer.length === 1 ? "has" : "have"} no insurer for this
            benefit year, so{" "}
            {insurers.length === 0
              ? "no insurer listing can be generated"
              : `${missingInsurer.length === 1 ? "it is" : "they are"} left out of every insurer submission`}
            . Set it on{" "}
            <Link
              to="/client-relations/company-benefits"
              className="font-medium underline underline-offset-2 hover:text-primary"
            >
              Company &amp; Benefits
            </Link>{" "}
            → the product → Header &amp; Policy.
          </p>
        </div>
      )}
      {/* Nothing to name and nothing to submit: the year has no products with
          categories yet. Without this the insurer Select just reads "No
          insurers configured" and both rows sit disabled, unexplained. */}
      {readiness && insurers.length === 0 && missingInsurer.length === 0 && (
        <div className="flex items-start gap-2 rounded-lg border border-warn/40 bg-warn-soft/40 px-3 py-2 text-sm text-warn">
          <AlertTriangle className="mt-0.5 size-4 shrink-0" />
          <p>
            No products are configured for this benefit year, so there is
            nothing to submit to an insurer yet. Add them on{" "}
            <Link
              to="/client-relations/company-benefits"
              className="font-medium underline underline-offset-2 hover:text-primary"
            >
              Company &amp; Benefits
            </Link>
            .
          </p>
        </div>
      )}

      <ReportSection
        title="Insurer submissions"
        hint="One submission per insurer — pick the insurer these are generated for."
        controls={
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
            <NricToggle nric={nric} setNric={setNric} />
          </div>
        }
        rows={rows}
      />

      {/* Its own NRIC control, bound to the same state: the underwriting export
          in here honours masking, so the toggle must sit with the rows it
          governs rather than in the insurer header above (the exact
          mislabelled-scope problem this split exists to fix). */}
      <ReportSection
        title="Internal registers"
        hint="Our own records — these span every insurer and aren't filtered by the picker above."
        controls={
          <div className="flex flex-wrap items-center gap-3">
            <div className="flex items-center gap-2">
              <span className="text-sm text-muted-foreground">Members</span>
              <Segmented
                value={rosterScope}
                onChange={setRosterScope}
                options={[
                  { value: "all", label: "All" },
                  { value: "active", label: "Active only" },
                ]}
              />
            </div>
            <NricToggle nric={nric} setNric={setNric} />
          </div>
        }
        rows={internalRows}
      />
    </div>
  );
}

/* ── Flex — funded by the member's own wallet, never insurer-scoped ──── */
function FlexReports({
  year,
  nric,
  setNric,
}: {
  year: PolicyYear;
  nric: NricMode;
  setNric: (v: NricMode) => void;
}) {
  // Only the elections export carries NRICs, so it is the ONLY row the masking
  // toggle governs — hence its own section rather than a tab-level control that
  // silently does nothing to the wallet export beside it.
  const electionRows: ReportRow[] = [
    {
      icon: CalendarCheck,
      title: "Benefit Selection & Leave",
      description:
        "What each member elected and what it draws from their flex wallet — plan changes, dependant cover and buy/sell-leave, one row per employee for the latest window.",
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
  ];

  // Their OWN section, not appended to "Wallets & pricing": that section's hint
  // says it carries no NRICs, and these two do. A masking toggle that silently
  // governs some rows of a section and not others is the exact mislabelled-scope
  // problem the insurer-picker split was made to fix.
  const utilisationRows: ReportRow[] = [
    {
      icon: Wallet,
      title: "Wallet Utilisation",
      description:
        "The wallet ledger — every dated movement: the allocation, what each product's price tag draws, leave traded and claims paid.",
      format: ".xlsx",
      action: (
        <ReportDownloadButton
          path={`/policy-years/${year.id}/reports/wallet-utilisation${nric === "full" ? "?masked=false" : ""}`}
          filename={`utilisation-report-${stamp(year)}.xlsx`}
          label="Download"
          size="sm"
        />
      ),
    },
    {
      icon: Wallet,
      title: "Wallet Utilisation Summary",
      description:
        "One row per member: allocation, what they have spent, what is still in flight and what is left.",
      format: ".xlsx",
      action: (
        <ReportDownloadButton
          path={`/policy-years/${year.id}/reports/wallet-utilisation-summary${nric === "full" ? "?masked=false" : ""}`}
          filename={`utilisation-summary-report-${stamp(year)}.xlsx`}
          label="Download"
          size="sm"
        />
      ),
    },
  ];

  const walletRows: ReportRow[] = [
    {
      icon: Wallet,
      title: "Flex Coverage & Wallets",
      description:
        "Flexible-benefit reconciliation — every member's wallet balance, and who is left out of a tier (no family status, in no tier, orphaned dependants).",
      format: ".xlsx",
      action: (
        <ReportDownloadButton
          path={`/policy-years/${year.id}/flex-scheme/coverage/export`}
          filename={`flex-coverage-${stamp(year)}.xlsx`}
          label="Download"
          size="sm"
        />
      ),
    },
    {
      icon: Coins,
      title: "Flex Price Tags",
      description:
        "What each plan and dependant option costs the wallet, plus the buy/sell-leave rate per tier. Set on the enrollment Flex tab.",
      format: "Interactive",
      action: (
        <OpenLink
          to="/client-relations/enrollment"
          search={{ tab: "flex" }}
          label="Open flex pricing"
        />
      ),
    },
    {
      icon: Wallet,
      title: "Flex Scheme",
      description:
        "The scheme itself — family-status tiers, wallet amounts and eligibility, with the coverage drill-down.",
      format: "Interactive",
      action: (
        <OpenLink
          to="/client-relations/company-benefits"
          search={{ tab: "flex" }}
          label="Open flex overview"
        />
      ),
    },
  ];

  return (
    <div className="space-y-6">
      <ReportSection
        title="Member benefits selection"
        hint="What members chose, and what it costs their wallet."
        controls={<NricToggle nric={nric} setNric={setNric} />}
        rows={electionRows}
      />
      <ReportSection
        title="Wallet utilisation"
        hint="Where each member's wallet went, as a ledger and as a summary."
        controls={<NricToggle nric={nric} setNric={setNric} />}
        rows={utilisationRows}
      />
      <ReportSection
        title="Wallets & pricing"
        hint="The scheme, the wallet balances and what each plan draws. No NRICs — nothing to mask."
        rows={walletRows}
      />
    </div>
  );
}

/* ── Claims — adjudication register + utilization ────────────────────── */
function ClaimsReports({
  year,
  nric,
  setNric,
}: {
  year: PolicyYear;
  nric: NricMode;
  setNric: (v: NricMode) => void;
}) {
  const maskedParam = nric === "full" ? "&masked=false" : "";
  const rows: ReportRow[] = [
    {
      icon: Receipt,
      title: "All Insurance Claims",
      description:
        "Every insured claim in the year with its full servicing history — reference, document dates, what went to the insurer and when they paid.",
      format: ".xlsx",
      action: (
        <ReportDownloadButton
          path={`/policy-years/${year.id}/reports/insurance-claims?scope=all${maskedParam}`}
          filename={`all-insurance-claims-in-benefit-year-${stamp(year)}.xlsx`}
          label="Download"
          size="sm"
        />
      ),
    },
    {
      icon: Receipt,
      title: "Inpatient Claims",
      description:
        "Hospitalisation and day surgery only, with sector, admission and discharge.",
      format: ".xlsx",
      action: (
        <ReportDownloadButton
          path={`/policy-years/${year.id}/reports/insurance-claims?scope=inpatient${maskedParam}`}
          filename={`inpatient-claims-in-benefit-year-${stamp(year)}.xlsx`}
          label="Download"
          size="sm"
        />
      ),
    },
    {
      icon: Receipt,
      title: "Outpatient Claims",
      description:
        "GP, specialist and dental claims, with the referral-letter position on each.",
      format: ".xlsx",
      action: (
        <ReportDownloadButton
          path={`/policy-years/${year.id}/reports/insurance-claims?scope=outpatient${maskedParam}`}
          filename={`outpatient-claims-in-benefit-year-${stamp(year)}.xlsx`}
          label="Download"
          size="sm"
        />
      ),
    },
    {
      icon: Users,
      title: "Employee Claims in Benefit Year",
      description:
        "Every claim a member made this year — insured and flex together, so one page covers their whole year.",
      format: ".xlsx",
      action: (
        <ReportDownloadButton
          path={`/policy-years/${year.id}/reports/employee-claims${nric === "full" ? "?masked=false" : ""}`}
          filename={`employee-claims-in-benefit-year-${stamp(year)}.xlsx`}
          label="Download"
          size="sm"
        />
      ),
    },
    {
      icon: Receipt,
      title: "Claims Register",
      description:
        "The flat adjudication register — one row per claim with the decision, for a quick reconciliation.",
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
          to="/policy-admin/member-coverage"
          search={{ view: "broker" }}
          label="Open in Member Coverage"
        />
      ),
    },
  ];
  return (
    <ReportSection
      title="Claims"
      hint="The year's claims and their servicing history."
      controls={<NricToggle nric={nric} setNric={setNric} />}
      rows={rows}
    />
  );
}

/* ── IT / Firm — audit, spend & access ───────────────────────────────── */
function ItReports({ year }: { year: PolicyYear | null }) {
  const { data: me } = useMe();
  const canAdmin = me?.role === "broker_admin" || me?.role === "system_admin";
  const exportRows: ReportRow[] = year
    ? [
        {
          icon: ScrollText,
          title: "Portal Activity",
          description:
            "Sign-ins across the member portal and HR surface for the last 30 days — including failed attempts and lockouts.",
          format: ".xlsx",
          action: (
            <ReportDownloadButton
              path={`/policy-years/${year.id}/reports/portal-activity`}
              filename={`portal-login-activity-report-${stamp(year)}.xlsx`}
              label="Download"
              size="sm"
            />
          ),
        },
        {
          icon: ScrollText,
          title: "Company Activity",
          description:
            "Configuration and administration changes for the last 30 days — who changed what, and when.",
          format: ".xlsx",
          action: (
            <ReportDownloadButton
              path={`/policy-years/${year.id}/reports/company-activity`}
              filename={`company-activity-report-${stamp(year)}.xlsx`}
              label="Download"
              size="sm"
            />
          ),
        },
        {
          icon: UserCog,
          title: "Portal Access",
          description:
            "The roster beside its portal accounts — who is provisioned, whose invite is still unsent, and who has never signed in.",
          format: ".xlsx",
          action: (
            <ReportDownloadButton
              path={`/policy-years/${year.id}/reports/portal-access`}
              filename={`portal-access-report-${stamp(year)}.xlsx`}
              label="Download"
              size="sm"
            />
          ),
        },
      ]
    : [];
  const rows: ReportRow[] = [
    {
      icon: ScrollText,
      title: "Audit Log",
      description:
        "Recent activity for this company — configuration changes, matching runs, exports and member actions.",
      format: "Interactive",
      action: <OpenLink to="/client-relations/company-benefits" label="Open audit feed" />,
    },
    {
      icon: Sparkles,
      title: "AI Spend",
      description:
        "Token usage and cost for AI extraction and claim review, with the circuit-breaker status.",
      format: "Interactive",
      action: <OpenLink to="/settings/ai" label="Open AI usage" />,
    },
  ];
  if (canAdmin) {
    rows.push({
      icon: UserCog,
      title: "Access & Companies",
      description:
        "Users, roles and company access across the firm — invite, disable and manage grants.",
      format: "Interactive",
      action: <OpenLink to="/firm/access" label="Open access admin" />,
    });
  }
  return (
    <div className="space-y-6">
      {exportRows.length > 0 && (
        <ReportSection
          title="Activity & access"
          hint="Exports covering sign-ins, changes and portal provisioning. No NRICs — nothing to mask."
          rows={exportRows}
        />
      )}
      <ReportSection
        title="Live surfaces"
        hint="Reviewed in the app rather than exported."
        rows={rows}
      />
    </div>
  );
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
  const [rosterScope, setRosterScope] = useState<RosterScope>("all");

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

      {/* Report sets sit ABOVE the team tabs, not inside one. A set is defined
          by the submission it makes up, and an insurer submission spans Policy
          Admin (the listings) and Flex (the benefit-selection record) — filing
          it under either tab would hide it from half the people who send it. */}
      {selectedYear && (
        <section className="space-y-2">
          <div>
            <h3 className="text-sm font-semibold text-foreground">
              Report sets
            </h3>
            <p className="text-xs text-muted-foreground">
              A whole submission in one download. Uses the NRIC/FIN setting from
              the tab below.
            </p>
          </div>
          <ReportBundleCard
            policyYearId={selectedYear.id}
            masked={nric === "masked"}
          />
        </section>
      )}

      <Tabs
        value={tab}
        onValueChange={(value) =>
          navigate({ to: "/claims/reports", search: { tab: value } })
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
              rosterScope={rosterScope}
              setRosterScope={setRosterScope}
            />
          ) : (
            <NoYearNotice />
          )}
        </TabsContent>
        <TabsContent value="flex">
          {selectedYear ? (
            <FlexReports year={selectedYear} nric={nric} setNric={setNric} />
          ) : (
            <NoYearNotice />
          )}
        </TabsContent>
        <TabsContent value="claims">
          {selectedYear ? (
            <ClaimsReports year={selectedYear} nric={nric} setNric={setNric} />
          ) : (
            <NoYearNotice />
          )}
        </TabsContent>
        <TabsContent value="it">
          <ItReports year={selectedYear} />
        </TabsContent>
      </Tabs>
    </div>
  );
}
