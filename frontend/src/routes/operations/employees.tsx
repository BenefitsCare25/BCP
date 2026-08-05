/** The Employees TAB of the roster page — NOT a route of its own.
 *
 * `/operations/employees` was retired by the nav consolidation and redirects to
 * `/operations/roster?tab=employees` (`router.tsx`); this file is imported by
 * `roster.tsx:8` and rendered as that tab, so everything here IS reachable —
 * under the nav item labelled **Member Listing**.
 *
 * The file previously carried a banner claiming it was orphaned. It was not:
 * the check that produced that conclusion excluded this file from its own
 * search, and `docs/ORPHANED_UI_RECOVERY.md` still repeats it. Run matching,
 * match results, the per-employee override, bulk delete and the flex detail
 * blocks are all live on the tab.
 *
 * Bulk portal invites DID move out — to Company settings → Authentication,
 * beside the sign-in policy that governs them. `MemberAccountActions` renders
 * both here and on `routes/operations/coverage.tsx`.
 */
import { useEffect, useMemo, useState } from "react";
import { AlertTriangle, Loader2, Play, RefreshCw, Save, Trash2 } from "lucide-react";
import {
  useBenefitStatement,
  useBulkDeleteEmployees,
  useCategoriesGrouped,
  useEmployee,
  useEmployees,
  useFlexMembership,
  useMatchResults,
  useRunMatching,
  useSetMatchOverride,
  useUpdateEmployee,
} from "@/api/hooks";
import { useDebouncedValue } from "@/lib/use-debounced-value";
import { useSession } from "@/stores/session";
import { AlertDialog } from "@/components/ui/alert-dialog";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { PaginationControls } from "@/components/ui/pagination-controls";
import { SkeletonTable } from "@/components/ui/skeleton";
import {
  Sheet,
  SheetBody,
  SheetContent,
  SheetHeader,
  SheetTitle,
} from "@/components/ui/sheet";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import {
  ListingCount,
  ListingExceptionLink,
  ListingImportBar,
} from "@/components/operations/ListingImportBar";
import { ReportDownloadButton } from "@/components/operations/ReportDownloadButton";
import { RosterTabActions } from "./rosterTabActions";
import { MemberAccountActions } from "@/components/operations/MemberAccountActions";
import { BenefitScheduleView } from "@/components/configuration/BenefitScheduleView";
import { useCoverageHistory } from "@/api/enrollment";
import { FlexPriceTagSummary } from "@/components/benefits/FlexPriceTagSummary";
import { CoverageHistory } from "@/components/enrollment/CoverageHistory";
import { CoverageRevertControls } from "@/components/enrollment/CoverageRevertControls";
import { EntityBreakdownCard } from "@/components/configuration/EntityBreakdownCard";
import { EntityReconciliationPanel } from "@/components/configuration/EntityReconciliationPanel";
import { OrphanOverridesPanel } from "@/components/enrollment/OrphanOverridesPanel";
import { PageGuide } from "@/components/ui/page-guide";
import { InfoHint } from "@/components/ui/tooltip";
import { coerceAttrs } from "@/lib/attrs";
import { ConflictDetailError, formatError } from "@/lib/errors";
import { formatWallet } from "@/lib/flex";
import { fmtCurrency } from "@/lib/format";
import { FAMILY_STATUS_LABELS } from "@/types";
import type {
  FlexCoverageLine,
  FlexEmployeeAssignment,
  MatchMethod,
  MatchResults,
} from "@/types";
import { toast } from "sonner";

const METHOD_LABEL: Record<MatchMethod, string> = {
  exact_name: "Exact match",
  fuzzy_name: "Fuzzy match",
  rule: "Rule match",
  manual_override: "Manual override",
};

const PAGE_SIZE = 50;

export function EmployeesPage() {
  const policyYearId = useSession((s) => s.currentPolicyYearId);
  const bulkDelete = useBulkDeleteEmployees();
  const runMatching = useRunMatching();
  const [page, setPage] = useState(0);
  const [search, setSearch] = useState("");
  const debouncedSearch = useDebouncedValue(search, 250);
  const [matchStatus, setMatchStatus] = useState<"all" | "matched" | "unmatched">(
    "all",
  );
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [editName, setEditName] = useState("");
  const [editAttrs, setEditAttrs] = useState<Record<string, string>>({});
  const [selectedCats, setSelectedCats] = useState<Set<string>>(new Set());
  const [showDeleteAll, setShowDeleteAll] = useState(false);
  const [deleteRisk, setDeleteRisk] = useState<{
    enrollments_at_risk: number;
    leave_elections_at_risk: number;
    claims_at_risk: number;
    overrides_at_risk: number;
  } | null>(null);
  const [showRunConfirm, setShowRunConfirm] = useState(false);
  const updateEmployee = useUpdateEmployee();
  const { data, isLoading } = useEmployees(
    policyYearId ?? undefined,
    page * PAGE_SIZE,
    PAGE_SIZE,
    debouncedSearch,
    matchStatus,
  );
  const { data: matchData } = useMatchResults(policyYearId ?? undefined, 0, 1);
  const { data: membership } = useFlexMembership(policyYearId ?? undefined);
  const flexByEmp = useMemo(() => {
    const m = new Map<string, FlexEmployeeAssignment>();
    membership?.assignments.forEach((a) => m.set(a.employee_id, a));
    return m;
  }, [membership]);
  const { data: detail } = useEmployee(selectedId);
  // Benefit statement of the open employee — for the flex price-tag / balance block.
  const { data: detailStatement } = useBenefitStatement(selectedId);
  // Shared (cached) with the CoverageHistory timeline below; drives whether the
  // 'Revert to baseline' control is offered for this member.
  const { data: coverageHistory } = useCoverageHistory(selectedId);
  const { data: categoryGroups = [] } = useCategoriesGrouped(
    policyYearId ?? undefined,
  );
  const override = useSetMatchOverride();

  // Seed editable fields when a different employee is opened. Keyed on the id
  // so a background refetch of the same employee doesn't clobber in-progress edits.
  useEffect(() => {
    if (!detail) return;
    setEditName(detail.employee_name ?? "");
    setEditAttrs(
      Object.fromEntries(
        Object.entries(detail.attribute_values).map(([k, v]) => [
          k,
          v == null ? "" : String(v),
        ]),
      ),
    );
    setSelectedCats(
      new Set(
        detail.matched_plans
          .map((p) => p.category_id)
          .filter((x): x is string => Boolean(x)),
      ),
    );
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [detail?.id]);

  if (!policyYearId) return null;
  const total = data?.total ?? 0;
  const pages = Math.ceil(total / PAGE_SIZE);
  const pending = matchData?.pending ?? true;
  const employeesTotal = matchData?.employees_total ?? 0;

  return (
    <div className="space-y-5">
      <RosterTabActions>
        <ReportDownloadButton
          path={`/employees/coverage-report/export?policy_year_id=${policyYearId}`}
          filename="employee-coverage.xlsx"
          label="Employee listing"
          disabled={!employeesTotal}
        />
        {/* "Invite all to portal" moved to Company settings → Authentication,
         * beside the sign-in policy that governs it (and where the rollout
         * counts + the "needs an email address" follow-up list now live). */}
        <Button
          variant={pending ? "default" : "outline"}
          onClick={() => setShowRunConfirm(true)}
          disabled={!employeesTotal || runMatching.isPending}
        >
          {pending ? <Play className="size-4" /> : <RefreshCw className="size-4" />}
          {pending ? "Run matching" : "Re-run matching"}
        </Button>
      </RosterTabActions>

      <ListingImportBar
        policyYearId={policyYearId}
        hasRows={matchData ? employeesTotal > 0 : undefined}
        stats={
          <ListingCount
            value={employeesTotal}
            noun={employeesTotal === 1 ? "employee" : "employees"}
          >
            <MatchState
              data={matchData}
              onShowUnmatched={() => {
                setPage(0);
                setMatchStatus("unmatched");
              }}
            />
          </ListingCount>
        }
      />

      {/* How the roster splits across legal entities — hidden when it carries
          no Entity column. */}
      <EntityBreakdownCard policyYearId={policyYearId} />

      {/* Entity-gate mismatches — the usual silent cause of "unmatched".
          Hidden when everything reconciles. */}
      <EntityReconciliationPanel policyYearId={policyYearId} />

      {/* Overrides stranded by a re-match — hidden when there are none. */}
      <OrphanOverridesPanel policyYearId={policyYearId} />

      <Card>
        <CardHeader>
          <div className="flex items-center justify-between gap-3 flex-wrap">
            <div>
              <CardTitle>Member listing</CardTitle>
              <CardDescription>
                {total.toLocaleString()} employee{total === 1 ? "" : "s"} on file
              </CardDescription>
            </div>
            <div className="flex items-center gap-2 flex-wrap">
              <div className="flex items-center gap-1">
                <FilterChip
                  label="All"
                  active={matchStatus === "all"}
                  onClick={() => {
                    setPage(0);
                    setMatchStatus("all");
                  }}
                />
                <FilterChip
                  label="Matched"
                  active={matchStatus === "matched"}
                  onClick={() => {
                    setPage(0);
                    setMatchStatus("matched");
                  }}
                />
                <FilterChip
                  label="Unmatched"
                  active={matchStatus === "unmatched"}
                  onClick={() => {
                    setPage(0);
                    setMatchStatus("unmatched");
                  }}
                />
              </div>
              <Input
                placeholder="Search by staff ID or name…"
                value={search}
                onChange={(e) => {
                  setPage(0);
                  setSearch(e.target.value);
                }}
                className="w-[240px]"
              />
              <Button
                variant="outline"
                size="sm"
                disabled={!total}
                onClick={() => setShowDeleteAll(true)}
                className="text-error hover:text-error"
              >
                <Trash2 className="size-4" /> Clear all
              </Button>
            </div>
          </div>
        </CardHeader>
        <CardContent>
          {isLoading ? (
            <SkeletonTable rows={8} columns={7} />
          ) : total === 0 ? (
            <div className="text-sm text-muted-foreground p-8 text-center border border-dashed border-border rounded-md">
              {matchStatus === "all"
                ? "No employees uploaded yet."
                : `No ${matchStatus} employees.`}
            </div>
          ) : (
            <>
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead>Staff ID</TableHead>
                    <TableHead>Name</TableHead>
                    <TableHead>Plan</TableHead>
                    <TableHead>Family status</TableHead>
                    <TableHead>Category (raw)</TableHead>
                    <TableHead>
                      <span className="inline-flex items-center gap-1">
                        Entity
                        <InfoHint>
                          The legal entity employing this member, from the
                          roster's Entity column. A category restricted to
                          specific entities only matches employees of those
                          entities.
                        </InfoHint>
                      </span>
                    </TableHead>
                    <TableHead>
                      <span className="inline-flex items-center gap-1">
                        Match
                        <InfoHint>
                          Matched = resolved to a category. Unmatched = no rule
                          applied — map manually or refine categories, then
                          re-run matching.
                        </InfoHint>
                      </span>
                    </TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {data?.items.map((e) => (
                    <TableRow
                      key={e.id}
                      className="cursor-pointer"
                      onClick={() => setSelectedId(e.id)}
                    >
                      <TableCell className="font-mono text-xs">{e.staff_id}</TableCell>
                      <TableCell>
                        <span className="font-medium">{e.employee_name ?? "—"}</span>
                      </TableCell>
                      <TableCell>
                        {e.matched_plans.length > 0 ? (
                          <div className="flex flex-wrap gap-1">
                            {e.matched_plans.map((p) => (
                              <span
                                key={p.product_code}
                                className="inline-flex items-center rounded-md bg-muted px-1.5 py-0.5 text-xs font-medium text-foreground"
                                title={p.product_name ?? p.product_code}
                              >
                                {p.product_code}
                              </span>
                            ))}
                          </div>
                        ) : (
                          <span className="text-muted-foreground">—</span>
                        )}
                      </TableCell>
                      <TableCell>
                        <FamilyStatusCell fa={flexByEmp.get(e.id)} />
                      </TableCell>
                      <TableCell className="max-w-[200px] truncate text-muted-foreground">
                        {(e.attribute_values.category as string) ?? "—"}
                      </TableCell>
                      <TableCell className="max-w-[220px] truncate text-muted-foreground">
                        {(e.attribute_values.entity as string) || "—"}
                      </TableCell>
                      <TableCell>
                        {e.matched_category_id ? (
                          <Badge variant="good">Matched</Badge>
                        ) : (
                          <Badge variant="warn">Unmatched</Badge>
                        )}
                      </TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
              <PaginationControls page={page} pages={pages} onPageChange={setPage} />
            </>
          )}
        </CardContent>
      </Card>

      <AlertDialog
        open={showDeleteAll}
        onOpenChange={setShowDeleteAll}
        title="Clear all employees?"
        description={
          <>
            This will permanently delete{" "}
            <strong>{employeesTotal.toLocaleString()} employee records</strong>{" "}
            for the current policy year (every employee, regardless of any active
            search or filter). Linked dependants will be unlinked. This cannot be
            undone.
          </>
        }
        confirmLabel={`Delete ${employeesTotal.toLocaleString()} employees`}
        loading={bulkDelete.isPending}
        onConfirm={async () => {
          try {
            const r = await bulkDelete.mutateAsync({
              policyYearId,
              confirm: false,
            });
            toast.success(`Deleted ${r.deleted} employees`);
            setShowDeleteAll(false);
            setPage(0);
          } catch (e) {
            if (
              e instanceof ConflictDetailError &&
              e.detail.code === "enrollment_data_at_risk"
            ) {
              setShowDeleteAll(false);
              setDeleteRisk({
                enrollments_at_risk: Number(e.detail.enrollments_at_risk ?? 0),
                leave_elections_at_risk: Number(
                  e.detail.leave_elections_at_risk ?? 0,
                ),
                claims_at_risk: Number(e.detail.claims_at_risk ?? 0),
                overrides_at_risk: Number(e.detail.overrides_at_risk ?? 0),
              });
              return;
            }
            toast.error(formatError(e));
          }
        }}
      />

      <AlertDialog
        open={!!deleteRisk}
        onOpenChange={(o) => !o && setDeleteRisk(null)}
        title="Enrollment & claims data will be lost"
        description={
          deleteRisk && (
            <>
              Deleting these employees will also permanently destroy the
              following data tied to them, which cannot be recovered by
              re-uploading the roster:
              <ul className="mt-2 list-disc pl-5">
                {[
                  [
                    deleteRisk.enrollments_at_risk,
                    "in-progress or confirmed enrollment(s)",
                  ],
                  [deleteRisk.leave_elections_at_risk, "leave selection(s)"],
                  [
                    deleteRisk.claims_at_risk,
                    "member claim(s), including retained receipts",
                  ],
                  [deleteRisk.overrides_at_risk, "active coverage override(s)"],
                ]
                  .filter(([n]) => (n as number) > 0)
                  .map(([n, label]) => (
                    <li key={label as string}>
                      <strong>{(n as number).toLocaleString()}</strong>{" "}
                      {label as string}
                    </li>
                  ))}
              </ul>
            </>
          )
        }
        confirmLabel={`Delete anyway (${employeesTotal.toLocaleString()} employees)`}
        loading={bulkDelete.isPending}
        onConfirm={async () => {
          try {
            const r = await bulkDelete.mutateAsync({
              policyYearId,
              confirm: true,
            });
            toast.success(`Deleted ${r.deleted} employees`);
            setDeleteRisk(null);
            setPage(0);
          } catch (e) {
            toast.error(formatError(e));
          }
        }}
      />

      <AlertDialog
        open={showRunConfirm}
        onOpenChange={setShowRunConfirm}
        title={pending ? "Run matching?" : "Re-run matching?"}
        description={
          <>
            This will re-derive structured attributes and re-match{" "}
            <strong>{employeesTotal.toLocaleString()} employees</strong>{" "}
            against the configured categories. Existing match assignments will
            be overwritten.
          </>
        }
        confirmLabel={pending ? "Run matching" : "Re-run matching"}
        confirmVariant="default"
        loading={runMatching.isPending}
        onConfirm={async () => {
          try {
            const result = await runMatching.mutateAsync(policyYearId);
            toast.success(
              `Matched ${result.employees_matched.toLocaleString()} of ${result.employees_total.toLocaleString()} employees in ${result.duration_ms}ms`,
            );
            if ((result.errors ?? 0) > 0) {
              toast.warning(
                `${result.errors} employee${result.errors === 1 ? "" : "s"} hit a matching error (not the same as unmatched) — retry or contact support`,
              );
            }
            setShowRunConfirm(false);
          } catch (err) {
            toast.error(formatError(err));
          }
        }}
      />

      <Sheet
        open={!!selectedId}
        onOpenChange={(o) => {
          if (!o) setSelectedId(null);
        }}
      >
        <SheetContent>
          {selectedId && !detail && (
            <div className="flex justify-center p-8">
              <Loader2 className="size-5 animate-spin text-muted-foreground" />
            </div>
          )}
          {detail && (
            <>
              <SheetHeader>
                <SheetTitle>{detail.employee_name ?? detail.staff_id}</SheetTitle>
                <p className="text-xs font-mono text-muted-foreground">
                  {detail.staff_id}
                </p>
              </SheetHeader>
              <SheetBody className="space-y-4">
                <div>
                  <div className="flex items-center gap-1 mb-2">
                    <div className="text-2xs uppercase tracking-wider text-muted-foreground">
                      Manual mapping
                    </div>
                    <InfoHint>
                      Select one plan per product. Saved as a manual override —
                      kept across re-runs until changed.
                    </InfoHint>
                  </div>
                  <div className="max-h-56 overflow-y-auto rounded-md border border-border divide-y divide-border">
                    {categoryGroups.length === 0 && (
                      <div className="p-3 text-xs text-muted-foreground">
                        No categories configured for this policy year.
                      </div>
                    )}
                    {categoryGroups.map((g) => (
                      <div key={g.product_code} className="p-2">
                        <div className="text-2xs uppercase tracking-wider text-muted-foreground mb-1">
                          {g.product_code} — {g.product_display_name}
                        </div>
                        {g.categories.map((c) => (
                          <label
                            key={c.id}
                            className="flex items-start gap-2 py-1 text-sm cursor-pointer"
                          >
                            <input
                              type="checkbox"
                              className="mt-1"
                              checked={selectedCats.has(c.id)}
                              onChange={(e) =>
                                setSelectedCats((prev) => {
                                  const next = new Set(prev);
                                  if (e.target.checked) next.add(c.id);
                                  else next.delete(c.id);
                                  return next;
                                })
                              }
                            />
                            <span className="text-foreground">{c.display_name}</span>
                          </label>
                        ))}
                      </div>
                    ))}
                  </div>
                  <div className="mt-2">
                    <Button
                      size="sm"
                      disabled={override.isPending}
                      onClick={async () => {
                        try {
                          await override.mutateAsync({
                            employeeId: detail.id,
                            categoryIds: Array.from(selectedCats),
                          });
                          toast.success(
                            selectedCats.size > 0
                              ? `Mapped ${selectedCats.size} plan${selectedCats.size === 1 ? "" : "s"}`
                              : "Match cleared",
                          );
                        } catch (err) {
                          toast.error(formatError(err));
                        }
                      }}
                    >
                      {override.isPending ? (
                        <Loader2 className="size-4 animate-spin" />
                      ) : (
                        <Save className="size-4" />
                      )}
                      Save mapping ({selectedCats.size})
                    </Button>
                  </div>
                </div>
                {detail.matched_plans.length > 0 && (
                  <div>
                    <div className="text-2xs uppercase tracking-wider text-muted-foreground mb-2">
                      Assigned plans
                    </div>
                    <div className="space-y-1.5">
                      {detail.matched_plans.map((p) => (
                        <div
                          key={p.product_code}
                          className="rounded-md border border-border p-2.5 bg-card"
                        >
                          <div className="flex items-start gap-2.5">
                            <span className="inline-flex shrink-0 items-center rounded-md bg-muted px-1.5 py-0.5 text-xs font-semibold text-foreground mt-0.5">
                              {p.product_code}
                            </span>
                            <div className="min-w-0">
                              <div className="flex items-center gap-1.5 text-sm font-medium">
                                <span className="truncate">
                                  {p.product_name ?? p.product_code}
                                </span>
                                {p.plan_overridden && (
                                  <Badge
                                    variant="warn"
                                    title={
                                      p.override_source
                                        ? `Override source: ${p.override_source.replace(/_/g, " ")}`
                                        : "Plan overridden from the category default"
                                    }
                                  >
                                    Overridden
                                  </Badge>
                                )}
                              </div>
                              {p.category_display && (
                                <div className="text-xs text-muted-foreground truncate">
                                  {p.category_display}
                                </div>
                              )}
                            </div>
                          </div>
                          {p.financials && (
                            <div className="mt-2 pt-2 border-t border-border grid grid-cols-2 gap-x-3 gap-y-1">
                              {p.financials.sum_insured != null && (
                                <div>
                                  <div className="text-2xs uppercase tracking-wider text-muted-foreground">
                                    Sum insured
                                  </div>
                                  <div className="text-xs font-medium">
                                    {fmtCurrency(p.financials.sum_insured)}
                                  </div>
                                </div>
                              )}
                              {p.financials.premium_rate != null && (
                                <div>
                                  <div className="text-2xs uppercase tracking-wider text-muted-foreground">
                                    Rate{p.financials.rate_basis === "per_1000_si" ? " (per $1k SI)" : ""}
                                  </div>
                                  <div className="text-xs font-medium">
                                    {p.financials.premium_rate}
                                  </div>
                                </div>
                              )}
                              {p.financials.annual_premium != null && (
                                <div>
                                  <div className="text-2xs uppercase tracking-wider text-muted-foreground">
                                    Annual premium
                                  </div>
                                  <div className="text-xs font-medium">
                                    {fmtCurrency(p.financials.annual_premium)}
                                  </div>
                                </div>
                              )}
                              {/* Show Basis only when it's a non-numeric expression
                                  (e.g. "12 times monthly salary"); a plain amount
                                  already shows under Sum insured (the member's own). */}
                              {p.financials.basis &&
                                isNaN(Number(p.financials.basis)) && (
                                <div>
                                  <div className="text-2xs uppercase tracking-wider text-muted-foreground">
                                    Basis
                                  </div>
                                  <div className="text-xs font-medium truncate" title={p.financials.basis}>
                                    {p.financials.basis}
                                  </div>
                                </div>
                              )}
                              {p.financials.rate_tiers && (
                                <div className="col-span-2 mt-1">
                                  <div className="text-2xs uppercase tracking-wider text-muted-foreground mb-1">
                                    Rate tiers
                                  </div>
                                  <div className="grid grid-cols-4 gap-1 text-xs">
                                    {Object.entries(p.financials.rate_tiers).map(([tier, vals]) => (
                                      <div key={tier} className="text-center rounded bg-muted px-1 py-0.5">
                                        <div className="font-medium">{tier}</div>
                                        <div className="text-muted-foreground">{fmtCurrency(vals.rate)}</div>
                                      </div>
                                    ))}
                                  </div>
                                </div>
                              )}
                            </div>
                          )}
                          {p.benefit_schedule?.items && p.benefit_schedule.items.length > 0 && (
                            <div className="mt-2 pt-2 border-t border-border">
                              <div className="text-2xs uppercase tracking-wider text-muted-foreground mb-1.5">
                                Schedule of Benefits
                              </div>
                              <div className="max-h-64 overflow-y-auto">
                                <BenefitScheduleView
                                  schedule={p.benefit_schedule}
                                  annualPolicyLimit={p.annual_policy_limit}
                                />
                              </div>
                            </div>
                          )}
                        </div>
                      ))}
                    </div>
                  </div>
                )}
                <MemberAccountActions
                  employeeId={detail.id}
                  staffId={detail.staff_id}
                />
                <FlexBenefitsDetail fa={flexByEmp.get(detail.id)} />
                <FlexPriceTagDetail flex={detailStatement?.flex ?? null} />
                <div className="rounded-lg border border-border bg-card p-4 space-y-3">
                  <div className="text-2xs uppercase tracking-wider text-muted-foreground">
                    Coverage flexibility
                  </div>
                  <CoverageRevertControls
                    employeeId={detail.id}
                    hasBaseline={coverageHistory?.has_baseline ?? false}
                  />
                  <div className="border-t border-border pt-3">
                    <CoverageHistory employeeId={selectedId} />
                  </div>
                </div>
                <div>
                  <div className="flex items-center justify-between gap-2 mb-2">
                    <div className="flex items-center gap-1">
                      <div className="text-2xs uppercase tracking-wider text-muted-foreground">
                        Details (editable)
                      </div>
                      <InfoHint>
                        Editing re-derives matching fields. Run matching to
                        re-evaluate plan assignments.
                      </InfoHint>
                    </div>
                    <Button
                      size="sm"
                      variant="outline"
                      disabled={updateEmployee.isPending}
                      onClick={async () => {
                        try {
                          await updateEmployee.mutateAsync({
                            employeeId: detail.id,
                            employee_name: editName,
                            attribute_values: coerceAttrs(editAttrs),
                          });
                          toast.success("Employee updated");
                        } catch (err) {
                          toast.error(formatError(err));
                        }
                      }}
                    >
                      {updateEmployee.isPending ? (
                        <Loader2 className="size-4 animate-spin" />
                      ) : (
                        <Save className="size-4" />
                      )}
                      Save changes
                    </Button>
                  </div>
                  <div className="grid grid-cols-2 gap-2">
                    <label className="block rounded-md border border-border p-2.5 bg-card">
                      <div className="text-2xs uppercase tracking-wider text-muted-foreground">
                        employee_name
                      </div>
                      <Input
                        value={editName}
                        onChange={(e) => setEditName(e.target.value)}
                        className="mt-1 h-8"
                      />
                    </label>
                    {Object.keys(editAttrs).map((k) => (
                      <label
                        key={k}
                        className="block rounded-md border border-border p-2.5 bg-card"
                      >
                        <div className="text-2xs uppercase tracking-wider text-muted-foreground">
                          {k}
                        </div>
                        <Input
                          value={editAttrs[k]}
                          onChange={(e) =>
                            setEditAttrs((prev) => ({ ...prev, [k]: e.target.value }))
                          }
                          className="mt-1 h-8"
                        />
                      </label>
                    ))}
                  </div>
                </div>
                <div>
                  <div className="flex items-center justify-between gap-2 mb-2">
                    <div className="text-2xs uppercase tracking-wider text-muted-foreground">
                      Derived attributes
                    </div>
                    {detail.matched_category_id && detail.match_method && (
                      <div className="flex items-center gap-1.5">
                        <Badge variant="outline">
                          {METHOD_LABEL[detail.match_method as MatchMethod]}
                        </Badge>
                        {detail.match_confidence !== null && (
                          <Badge variant="good">
                            {Math.round(detail.match_confidence * 100)}%
                          </Badge>
                        )}
                      </div>
                    )}
                  </div>
                  {Object.keys(detail.derived_attribute_values).length > 0 ? (
                    <div className="grid grid-cols-2 gap-2">
                      {Object.entries(detail.derived_attribute_values).map(
                        ([k, v]) => (
                          <div
                            key={k}
                            className="rounded-md border border-border p-2.5 bg-card"
                          >
                            <div className="text-2xs uppercase tracking-wider text-muted-foreground">
                              {k}
                            </div>
                            <div className="text-sm font-medium mt-0.5 break-words">
                              {String(v)}
                            </div>
                          </div>
                        ),
                      )}
                    </div>
                  ) : (
                    <div className="text-sm text-muted-foreground rounded-md border border-dashed border-border p-3">
                      No derived attributes — run matching to populate.
                    </div>
                  )}
                </div>
              </SheetBody>
            </>
          )}
        </SheetContent>
      </Sheet>

      <PageGuide
        purpose="Upload the member listing, then run category matching. Matching uses a tiered approach: exact name → fuzzy Jaccard (≥0.6) → rule evaluation. Click any row to view plans, financials, and derived attributes."
        connections={[
          { label: "← Listing upload", description: "Employees are imported from STM-format Excel files" },
          { label: "← Product categories", description: "Confirmed categories with rules drive the matching engine" },
          { label: "→ Dependants", description: "Dependants are linked to employees via staff ID or NRIC" },
        ]}
      />
    </div>
  );
}

/**
 * One line of match state under the headcount.
 *
 * It also surfaces `MatchResults.reason`, which nothing rendered before: the
 * page showed only a "Run matching" / "Re-run matching" button, so a broker
 * whose results had gone STALE (a category edited after the last run — see
 * `api/v1/matches.py`) saw the identical screen to one whose results were
 * current, and the counts beside it were quietly out of date.
 */
function MatchState({
  data,
  onShowUnmatched,
}: {
  data?: MatchResults;
  onShowUnmatched: () => void;
}) {
  if (!data) return <span>Loading…</span>;
  if (data.employees_total === 0) return <span>Nothing uploaded yet</span>;
  if (!data.last_run_at) return <span>Matching not run yet</span>;

  const stale = data.pending;
  return (
    <>
      {data.employees_unmatched > 0 ? (
        <>
          <span className="tabular-nums">
            {data.employees_matched.toLocaleString()}
          </span>{" "}
          matched ·
          <ListingExceptionLink
            count={data.employees_unmatched}
            label="unmatched"
            onClick={onShowUnmatched}
          />
        </>
      ) : (
        <span>All {data.employees_matched.toLocaleString()} matched</span>
      )}
      {stale && (
        // Amber lives in the glyph, never in 12px text: --color-warn is 3.19:1
        // on card — fine for a graphic (1.4.11), short of 4.5:1 for body copy.
        <span
          className="inline-flex items-center gap-1 text-foreground"
          title={data.reason ?? undefined}
        >
          <AlertTriangle className="size-3.5 text-warn" />
          may be stale
        </span>
      )}
    </>
  );
}

/** Roster-cell badge for an employee's resolved family status. */
function FamilyStatusCell({ fa }: { fa?: FlexEmployeeAssignment }) {
  if (!fa?.family_status) {
    return <span className="text-muted-foreground">—</span>;
  }
  return (
    <span
      className="inline-flex items-center rounded-md bg-muted px-1.5 py-0.5 text-xs font-medium text-foreground"
      title={
        fa.source === "dependants"
          ? "From the dependant listing"
          : "From the employee roster"
      }
    >
      {FAMILY_STATUS_LABELS[fa.family_status]}
    </span>
  );
}

/** Detail-sheet block: an employee's flex tier, wallet, and family makeup. */
function FlexBenefitsDetail({ fa }: { fa?: FlexEmployeeAssignment }) {
  if (!fa) return null;
  return (
    <div>
      <div className="flex items-center gap-1 mb-2">
        <div className="text-2xs uppercase tracking-wider text-muted-foreground">
          Flexible benefits
        </div>
        <InfoHint>
          Family status is taken from this employee's linked dependants when
          available, else the roster.
        </InfoHint>
      </div>
      <div className="rounded-md border border-border p-2.5 bg-card space-y-1.5">
        <div className="flex items-center justify-between gap-2">
          <span className="text-sm font-medium text-foreground">
            {fa.family_status
              ? FAMILY_STATUS_LABELS[fa.family_status]
              : "No family status"}
          </span>
          <Badge variant={fa.source === "dependants" ? "good" : "warn"}>
            {fa.source === "dependants"
              ? "from dependants"
              : fa.source === "roster"
                ? "from roster"
                : "unresolved"}
          </Badge>
        </div>
        <div className="grid grid-cols-2 gap-x-3 gap-y-1 text-xs">
          <div>
            <div className="text-2xs uppercase tracking-wider text-muted-foreground">
              Eligibility tier
            </div>
            <div className="font-medium">{fa.tier_name ?? "—"}</div>
          </div>
          <div>
            <div className="text-2xs uppercase tracking-wider text-muted-foreground">
              Flexi wallet
            </div>
            <div className="font-medium">
              {formatWallet(fa.wallet_amount, fa.currency)}
            </div>
          </div>
          <div>
            <div className="text-2xs uppercase tracking-wider text-muted-foreground">
              Spouse / children
            </div>
            <div className="font-medium">
              {fa.spouse_count} / {fa.child_count}
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}

/** Detail-sheet block: flex wallet spent on coverage (price tags) + net balance.
 *  Distinct from the insurer premium; only shown when a price-tag matrix exists. */
function FlexPriceTagDetail({ flex }: { flex: FlexCoverageLine | null }) {
  if (!flex || flex.price_tags_total == null) return null;
  return (
    <div className="rounded-md border border-border p-2.5 bg-card">
      <FlexPriceTagSummary flex={flex} />
    </div>
  );
}

function FilterChip({
  label,
  active,
  onClick,
}: {
  label: string;
  active: boolean;
  onClick: () => void;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      className={
        active
          ? "rounded-full bg-primary text-primary-foreground text-xs font-medium px-3 py-1"
          : "rounded-full bg-card text-foreground text-xs font-medium px-3 py-1 border border-border hover:bg-muted"
      }
    >
      {label}
    </button>
  );
}
