import { useMemo, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { Link, useNavigate, useSearch } from "@tanstack/react-router";
import {
  AlertTriangle,
  CalendarCheck,
  Coins,
  FileSpreadsheet,
  FileText,
  Gauge,
  ScrollText,
  Sparkles,
  UserCog,
  Wallet,
} from "lucide-react";
import type { LucideIcon } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Segmented } from "@/components/ui/segmented";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import {
  PageTabsBar,
  Tabs,
  TabsContent,
  TabsList,
  TabsTrigger,
} from "@/components/ui/tabs";
import { ReportWorkbookRow } from "@/components/operations/ReportWorkbookRow";
import { ReportDownloadButton } from "@/components/operations/ReportDownloadButton";
import {
  ReportVersionActions,
  SubmissionRecord,
} from "@/components/operations/ReportVersionActions";
import { useReportReadiness, useReportWorkbooks } from "@/api/reports";
import { useMe, usePolicyYears } from "@/api/hooks";
import { useSession } from "@/stores/session";
import type { PolicyYear } from "@/types";

// Reports Center — every downloadable/reviewable report, grouped by the team
// that owns it (the same four-team split the sidebar uses). Reports are
// version-per-year: a benefit-year picker at the top scopes every year-owned
// report so brokers can pull a past or draft year's documents, not just the
// current one.
//
// **The unit is the WORKBOOK, not the file.** This page used to list 26
// downloads for what is really about a dozen artifacts, because a workbook had
// not been allowed more than one sheet: the roster went out as five files, an
// insurer submission as three (zipped), and the year's claims as four. Every
// one of those workbooks was also called "Sheet1". They are now composites with
// named sheets, and each row prints the sheets it contains — a broker knows
// what is inside before downloading, and the names travel with the file once it
// is forwarded on.
//
// A few rows are still single artifacts (the fact-find, the slips) or
// interactive surfaces that live on a working page; those link there instead of
// exporting.
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

/* ── Table shell — one row per single-artifact report ─────────────────── */
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

/** The benefit-selection download, with its own masking control beside it.
 *
 *  Two reasons it is a component rather than a bare button. The record line
 *  under the table has to refresh the moment the file is served — the download
 *  FILES the retained copy, so a stale "Not sent yet" underneath it is the one
 *  thing that would send a broker round the loop twice. And masking belongs
 *  IN the card, exactly as it does on a workbook row: it decides whether this
 *  download is a submission, so it cannot sit in a section header away from the
 *  button it qualifies. */
function BenefitSelectionDownload({
  year,
  nric,
  setNric,
}: {
  year: PolicyYear;
  nric: NricMode;
  setNric: (v: NricMode) => void;
}) {
  const qc = useQueryClient();
  return (
    <div className="flex items-center justify-end gap-2">
      <Segmented
        value={nric}
        onChange={setNric}
        options={[
          { value: "masked", label: "Masked" },
          { value: "full", label: "Unmasked" },
        ]}
      />
      <ReportDownloadButton
        path={`/policy-years/${year.id}/reports/benefit-selection${nric === "full" ? "?masked=false" : ""}`}
        filename={`benefit-selection-status-with-buy-sell-leave-report-${stamp(year)}.xlsx`}
        label="Download"
        size="sm"
        onDownloaded={() => {
          qc.invalidateQueries({ queryKey: ["report-version-status"] });
          qc.invalidateQueries({ queryKey: ["report-versions"] });
        }}
      />
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

/** A titled group, with the controls that actually scope THEM in its header. A
 *  filter rendered above the whole tab reads as scoping every row in it — which
 *  is how the insurer picker came to sit over reports no insurer submission
 *  touches. Composite workbooks carry their own controls (each declares what it
 *  supports), so a section wrapping them needs none. */
function ReportSection({
  title,
  hint,
  controls,
  children,
}: {
  title: string;
  hint: string;
  controls?: React.ReactNode;
  children: React.ReactNode;
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
      {children}
    </section>
  );
}

/** Renders the composite workbooks a tab owns, in the order named.
 *
 *  Driven off the SERVED list rather than a hardcoded set, so a workbook added
 *  on the server appears with its real sheet list; an unknown key is simply not
 *  rendered rather than throwing. */
function Workbooks({ keys, year }: { keys: string[]; year: PolicyYear }) {
  const { data: workbooks = [] } = useReportWorkbooks(year.id);
  const byKey = useMemo(
    () => new Map(workbooks.map((w) => [w.key, w])),
    [workbooks],
  );
  const shown = keys.map((k) => byKey.get(k)).filter((w) => w !== undefined);
  if (!shown.length) return null;
  return (
    <div className="space-y-2">
      {shown.map((wb) => (
        <ReportWorkbookRow
          key={wb.key}
          policyYearId={year.id}
          workbook={wb}
          year={year.year}
        />
      ))}
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
        />
      ),
    },
  ];
  return <ReportTable rows={rows} />;
}

/* ── Policy Admin — roster, coverage & insurer submissions ───────────── */
function PaReports({ year }: { year: PolicyYear }) {
  const { data: readiness, isError } = useReportReadiness(year.id);

  // The insurer is picked ON the submission row (the workbook declares that it
  // needs one), so this tab holds no insurer state of its own. It used to hold
  // one for a second insurer picker in the retained-history section — the same
  // two concepts offered by three controls that did not govern each other.
  const insurers = readiness?.insurers ?? [];
  const missingInsurer = readiness?.products_without_insurer ?? [];

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

      {/* The submission itself — one workbook, one insurer. Its insurer picker
          lives ON the row (the workbook declares it), which is what keeps the
          control beside the only thing it scopes. */}
      <ReportSection
        title="Insurer submissions"
        hint="A whole submission in one workbook — pick the insurer and NRIC treatment on the row. An unmasked download is the copy an insurer receives, and files a record of it."
      >
        <Workbooks keys={["insurer-submission"]} year={year} />
      </ReportSection>

      {/* No section-level NRIC toggle: every row here is a workbook, and a
          workbook declares and owns its own masking control. A toggle in the
          header would be a second control for the same thing — and the one that
          used to live here also governed the Insurer Submission row in the
          section ABOVE it. */}
      <ReportSection
        title="Internal registers"
        hint="Our own records — these span every insurer and aren't insurer-scoped."
      >
        <Workbooks keys={["member-register", "leavers", "underwriting"]} year={year} />
      </ReportSection>

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
  const electionRows: ReportRow[] = [
    {
      icon: CalendarCheck,
      title: "Benefit Selection & Leave",
      description:
        "What each member elected and what it draws from their flex wallet — plan changes, dependant cover and buy/sell-leave, one row per employee for the latest window.",
      format: ".xlsx",
      // Downloading files a retained copy, exactly like the insurer submission
      // — the record line below the table says what was last sent. It used to
      // offer "Save version" as its ONLY control, with the download itself two
      // clicks inside a drawer that did not exist until something was saved.
      action: (
        <BenefitSelectionDownload year={year} nric={nric} setNric={setNric} />
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
      >
        <div className="rounded-lg border border-border bg-card">
          <ReportTable rows={electionRows} />
          <SubmissionRecord
            policyYearId={year.id}
            reportType="benefit_selection"
            scopeKey={null}
            hasMovement={false}
            filesOnDownload={nric === "full"}
          />
        </div>
      </ReportSection>
      <ReportSection
        title="Wallet utilisation"
        hint="Where each member's wallet went — the position and the ledger behind it, in one workbook."
      >
        <Workbooks keys={["flex-wallet"]} year={year} />
      </ReportSection>
      <ReportSection
        title="Scheme & pricing"
        hint="The scheme and what each plan draws. No NRICs — nothing to mask."
      >
        <ReportTable rows={walletRows} />
      </ReportSection>
    </div>
  );
}

/* ── Claims — adjudication register + utilization ────────────────────── */
function ClaimsReports({ year }: { year: PolicyYear }) {
  const rows: ReportRow[] = [
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
    <div className="space-y-6">
      <ReportSection
        title="Claims"
        hint="The year's claims and their servicing history — the whole book, split by setting and per member, in one workbook."
      >
        <Workbooks keys={["claims-register"]} year={year} />
      </ReportSection>
      <ReportSection
        title="Live surfaces"
        hint="Reviewed in the app rather than exported."
      >
        <ReportTable rows={rows} />
      </ReportSection>
    </div>
  );
}

/* ── IT / Firm — audit, spend & access ───────────────────────────────── */
function ItReports({ year }: { year: PolicyYear | null }) {
  const { data: me } = useMe();
  const canAdmin = me?.role === "broker_admin" || me?.role === "system_admin";
  const rows: ReportRow[] = [
    {
      icon: ScrollText,
      title: "Audit Log",
      description:
        "Recent activity for this company — configuration changes, matching runs, exports and member actions.",
      format: "Interactive",
      action: (
        <OpenLink to="/client-relations/company-benefits" label="Open audit feed" />
      ),
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
      {year && (
        <ReportSection
          title="Activity & access"
          hint="Sign-ins, changes and portal provisioning — the three questions a security review opens together. Defaults to the last 30 days."
        >
          <Workbooks keys={["activity-access"]} year={year} />
        </ReportSection>
      )}
      <ReportSection
        title="Live surfaces"
        hint="Reviewed in the app rather than exported."
      >
        <ReportTable rows={rows} />
      </ReportSection>
    </div>
  );
}

export function ReportsPage() {
  const sessionYearId = useSession((s) => s.currentPolicyYearId);
  const { data: years = [] } = usePolicyYears();
  const navigate = useNavigate();
  const search = useSearch({ strict: false }) as { tab?: string };
  const tab: TeamKey = isTeam(search.tab) ? search.tab : "pa";

  const selectedYear = years.find((y) => y.id === sessionYearId) ?? null;

  // NRIC held here (not in the tab) so the choice survives a tab switch, which
  // unmounts the inactive tab's content.
  const [nric, setNric] = useState<NricMode>("masked");

  return (
    <div className="space-y-5">
      <Tabs
        value={tab}
        onValueChange={(value) =>
          navigate({ to: "/claims/reports", search: { tab: value } })
        }
      >
        <PageTabsBar className="overflow-x-auto">
          <TabsList className="min-w-max">
            {TEAMS.map((t) => (
              <TabsTrigger key={t.key} value={t.key}>
                {t.label}
              </TabsTrigger>
            ))}
          </TabsList>
        </PageTabsBar>

        <TabsContent value="cr">
          {selectedYear ? <CrReports year={selectedYear} /> : <NoYearNotice />}
        </TabsContent>
        <TabsContent value="pa">
          {selectedYear ? (
            <PaReports year={selectedYear} />
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
            <ClaimsReports year={selectedYear} />
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
