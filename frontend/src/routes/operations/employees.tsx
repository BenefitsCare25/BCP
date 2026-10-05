import { SystemAdminOnly } from "@/components/auth/SystemAdminOnly";
/** The Employees TAB of the roster page — NOT a route of its own.
 *
 * This file is imported by `roster.tsx:8` and rendered as that tab, so
 * everything here IS reachable — at `/policy-admin/member-listing?tab=employees`,
 * under the nav item labelled **Member Listing**. The folder name is historical:
 * routes live under `routes/operations/` but no longer serve an `/operations/*`
 * URL, so never infer a path from this file's location.
 *
 * The file previously carried a banner claiming it was orphaned. It was not:
 * the check that produced that conclusion excluded this file from its own
 * search. Run matching, match results, the per-employee override and bulk
 * delete are all live on the tab.
 *
 * WHAT THIS PAGE OWNS (2026-08-07). Member Listing owns the data going IN —
 * who is on file, what the roster says about them, and which category they
 * matched. **Member Coverage owns everything matching PRODUCES**: cover,
 * financials, schedules of benefits, the flex wallet, claims and portal access.
 *
 * The row sheet used to rebuild the entire benefit statement inside a 480px
 * drawer — assigned plans with sum insured / rate / premium / rate tiers, a
 * schedule of benefits for each of a CDL employee's eight products (each in its
 * own 64px-tall scroller, with no claims merged in), the flex wallet twice, and
 * portal access. All of it renders better on `coverage.tsx`. One of those
 * copies was not merely redundant: `FlexPriceTagSummary` computed
 * `wallet − price tags = balance` while `FlexPanel` computes
 * `allowance − tags ± leave − claims approved = left`, so the same member's
 * wallet read differently on the two pages by whatever they had claimed.
 *
 * So the sheet is now: what matching CONCLUDED (a status strip of product
 * codes, linking to the coverage record) → the matching controls → the roster
 * fields those controls run on. Nothing here prices anything.
 *
 * Bulk portal invites live on Company settings → Authentication;
 * `MemberAccountActions` renders on `routes/operations/coverage.tsx` only.
 */
import { Fragment, useEffect, useMemo, useRef, useState } from "react";
import { Link, useNavigate, useSearch } from "@tanstack/react-router";
import {
  AlertTriangle,
  ArrowUpRight,
  Loader2,
  Play,
  RefreshCw,
  Trash2,
} from "lucide-react";
import {
  useBulkDeleteEmployees,
  useCategoriesGrouped,
  useEmployee,
  useFlexMembership,
  useMatchResults,
  useRunMatching,
} from "@/api/hooks";
import { useMemberFacets, useMemberQueryList } from "@/api/memberQuery";
import {
  EMPTY_MEMBER_FILTERS,
  type MemberFilterState,
  memberFiltersAreEmpty,
  toMemberQuery,
} from "@/lib/memberFilters";
import { useDebouncedValue } from "@/lib/use-debounced-value";
import { useSession } from "@/stores/session";
import { AlertDialog } from "@/components/ui/alert-dialog";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardHeader,
} from "@/components/ui/card";
import { PaginationControls } from "@/components/ui/pagination-controls";
import { SkeletonTable } from "@/components/ui/skeleton";
import {
  Sheet,
  SheetBody,
  SheetContent,
  SheetHeader,
  SheetFooter,
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
import { RosterFilterBar } from "@/components/operations/RosterFilterBar";
import { RosterTabActions } from "./rosterTabActions";
import { EntityBreakdownCard } from "@/components/configuration/EntityBreakdownCard";
import { EntityReconciliationPanel } from "@/components/configuration/EntityReconciliationPanel";
import { OrphanOverridesPanel } from "@/components/enrollment/OrphanOverridesPanel";
import { InfoHint } from "@/components/ui/tooltip";
import { EmployeeMappingEditor } from "@/components/operations/EmployeeMappingEditor";
import { EmployeeRosterEditor } from "@/components/operations/EmployeeRosterEditor";
import { ConflictDetailError, formatError } from "@/lib/errors";
import { FAMILY_STATUS_LABELS } from "@/types";
import type {
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

/** Separator between facts in the identity strip's meta run. Decorative, so it
 *  is hidden from assistive tech — a screen reader announcing "middle dot"
 *  between every value is noise, and the values read fine as a list without it. */
function MetaDot() {
  return (
    <span aria-hidden className="mx-1.5 text-subtle">
      ·
    </span>
  );
}

export function EmployeesPage() {
  const policyYearId = useSession((s) => s.currentPolicyYearId);
  const bulkDelete = useBulkDeleteEmployees();
  const runMatching = useRunMatching();
  const navigate = useNavigate();
  // The open row rides the URL, so a roster record is a shareable link and
  // Member Coverage can link back to the row it came from. `replace` because
  // clicking down a table shouldn't fill the back stack with people.
  const search = useSearch({ strict: false }) as { employee?: string; product?: string; match?: "matched" | "unmatched" };
  const selectedId = search.employee ?? null;
  const setSelectedId = (id: string | null) =>
    void navigate({
      to: "/policy-admin/member-listing",
      search: { ...search, tab: "employees", employee: id ?? undefined },
      replace: true,
    });
  const previousPolicyYearId = useRef(policyYearId);
  useEffect(() => {
    if (previousPolicyYearId.current === policyYearId) return;
    previousPolicyYearId.current = policyYearId;
    setSelectedId(null);
  }, [policyYearId]);
  const [page, setPage] = useState(0);
  // One filter state for the whole bar, serialized to the SAME `MemberFilters`
  // the bulk tool sends — so the roster view and a bulk selection can never
  // describe different populations for the same rule.
  const [filters, setFilters] = useState<MemberFilterState>(() => ({
    ...EMPTY_MEMBER_FILTERS, productCodes: search.product ? [search.product] : [], matchStatus: search.match ?? "any",
  }));
  useEffect(() => {
    setPage(0);
    setFilters({ ...EMPTY_MEMBER_FILTERS, productCodes: search.product ? [search.product] : [], matchStatus: search.match ?? "any" });
  }, [policyYearId, search.product, search.match]);
  const debouncedQ = useDebouncedValue(filters.q, 250);
  const [mappingDirty, setMappingDirty] = useState(false);
  const [rosterEditing, setRosterEditing] = useState(false);
  const [actionContainer, setActionContainer] = useState<HTMLDivElement | null>(null);
  const [showDeleteAll, setShowDeleteAll] = useState(false);
  const [deleteRisk, setDeleteRisk] = useState<{
    enrollments_at_risk: number;
    leave_elections_at_risk: number;
    claims_at_risk: number;
    overrides_at_risk: number;
  } | null>(null);
  const [showRunConfirm, setShowRunConfirm] = useState(false);
  // Only the SEARCH text is debounced; a picker change is a deliberate click
  // and should move the table at once.
  const query = useMemo(
    () => toMemberQuery({ ...filters, q: debouncedQ }),
    [filters, debouncedQ],
  );
  const soleProduct =
    filters.productCodes.length === 1 ? filters.productCodes[0] : undefined;
  const { data, isLoading } = useMemberQueryList(
    policyYearId ?? undefined,
    query,
    { offset: page * PAGE_SIZE, limit: PAGE_SIZE },
    soleProduct,
  );
  const { data: facets, isLoading: facetsLoading } = useMemberFacets(
    policyYearId ?? undefined,
  );
  const { data: matchData } = useMatchResults(policyYearId ?? undefined, 0, 1);
  const { data: membership } = useFlexMembership(policyYearId ?? undefined);
  const flexByEmp = useMemo(() => {
    const m = new Map<string, FlexEmployeeAssignment>();
    membership?.assignments.forEach((a) => m.set(a.employee_id, a));
    return m;
  }, [membership]);
  // `isError` is load-bearing now that the open row comes from the URL: a
  // shared link can name a member this roster no longer has.
  const { data: detail, isError: detailFailed } = useEmployee(selectedId);
  const { data: categoryGroups = [] } = useCategoriesGrouped(
    policyYearId ?? undefined,
  );
  useEffect(() => {
    setMappingDirty(false);
    setRosterEditing(false);
  }, [selectedId]);

  if (!policyYearId) return null;
  // `data.total` follows the active filters, so it cannot state what is on
  // file — the facets carry the unfiltered headcount. Conflating the two is
  // what makes a filtered roster claim the company shrank.
  const total = data?.total ?? 0;
  const onFile = facets?.employees_total ?? total;
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
                // Same state the bar drives, so the link and the chips agree.
                setPage(0);
                setFilters((f) => ({ ...f, matchStatus: "unmatched" }));
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
        <CardHeader className="flex-row flex-wrap items-center justify-between gap-3">
          <p className="text-xs tabular-nums text-muted-foreground">
            {onFile.toLocaleString()} employee{onFile === 1 ? "" : "s"} on file
          </p>
          <SystemAdminOnly><Button
            variant="outline"
            size="sm"
            disabled={!total}
            onClick={() => setShowDeleteAll(true)}
            className="shrink-0 text-error hover:text-error"
          >
            <Trash2 className="size-4" /> Clear all
          </Button></SystemAdminOnly>
        </CardHeader>
        <CardContent className="space-y-4">
          <RosterFilterBar
            state={filters}
            onChange={(next) => {
              setPage(0);
              setFilters(next);
            }}
            facets={facets}
            facetsLoading={facetsLoading}
            total={data?.total}
          />
          {isLoading ? (
            <SkeletonTable rows={8} columns={7} />
          ) : total === 0 ? (
            <div className="text-sm text-muted-foreground p-8 text-center border border-dashed border-border rounded-md">
              {memberFiltersAreEmpty(filters)
                ? "No employees uploaded yet."
                : "No employees match these filters."}
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
                          Unmatched includes a missing category for any applicable product.
                          Review the product's employee categories and roster grades, then re-run matching.
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
                        {e.unmatched_product_codes?.length ? (
                          <div className="space-y-1"><Badge variant="warn">Unmatched</Badge>
                            <p className="max-w-56 text-xs text-muted-foreground">Missing {e.unmatched_product_codes.join(", ")}</p>
                          </div>
                        ) : e.matched_plans.length || e.matched_category_id ? (
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

      <SystemAdminOnly><AlertDialog
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
      /></SystemAdminOnly>

      <SystemAdminOnly><AlertDialog
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
      /></SystemAdminOnly>

      <AlertDialog
        open={showRunConfirm}
        onOpenChange={setShowRunConfirm}
        title={pending ? "Run matching?" : "Re-run matching?"}
        description={
          <>
            This refreshes the matching rules from the placement slip (rules
            you edited are kept), then re-matches{" "}
            <strong>{employeesTotal.toLocaleString()} employees</strong>{" "}
            against them. Existing match assignments will be overwritten.
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
          {/* A FAILED fetch must have its own branch. The open row rides the
            * URL, so the id can arrive from a shared link, a bookmark or a
            * browser Back after a listing sync — and `retryQuery` deliberately
            * doesn't retry a 4xx, so a 404 settles with `data === undefined`
            * forever. Without this the drawer sat on "Loading member…"
            * indefinitely, describing a fetch that had already finished. */}
          {selectedId && detailFailed ? (
            <>
              <SheetHeader className="pr-12">
                <SheetTitle>Member not on this roster</SheetTitle>
              </SheetHeader>
              <SheetBody className="space-y-3">
                <p className="text-sm text-muted-foreground">
                  This link points at an employee the current benefit year
                  doesn&rsquo;t have. They may have been removed by a listing
                  sync, or the link belongs to a different company.
                </p>
                <Button variant="outline" onClick={() => setSelectedId(null)}>
                  Back to the listing
                </Button>
              </SheetBody>
              <SheetFooter className="shrink-0"><div className="w-full" ref={setActionContainer} /></SheetFooter>
            </>
          ) : selectedId && !detail ? (
            /* The loading state carries a TITLE. Without one the open dialog
             * has no accessible name — Radix warns, and a screen reader
             * announces an unnamed dialog for however long the fetch takes. */
            <>
              <SheetHeader className="pr-12">
                <SheetTitle>Loading member…</SheetTitle>
              </SheetHeader>
              <SheetBody className="flex justify-center pt-10">
                <Loader2 className="size-5 animate-spin text-muted-foreground" />
              </SheetBody>
              <SheetFooter className="shrink-0"><div className="w-full" ref={setActionContainer} /></SheetFooter>
            </>
          ) : null}
          {detail && (
            <>
              {/* ONE identity strip: who this is, what matching READ (the
                * derived values), and what it CONCLUDED (the plans it resolved
                * to). They were three stacked blocks — a sheet header, a
                * bordered status card directly beneath it, and a grid of
                * derived-attribute boxes further down — which is one fact told
                * in three places, and a box inside a box at the top of a drawer.
                *
                * The language is `components/benefits/StatementHeader`'s, so the
                * same person reads the same way on both pages: name + verdict
                * badge, then a dot-separated meta run, then the list. Facts are
                * meta, NOT pills — bordering every value is what turned this
                * into a field of lozenges.
                *
                * It sits in the HEADER, not the body, so the person and their
                * match stay on screen while the mapping list below scrolls.
                * `pr-12` clears the sheet's own close button. */}
              <SheetHeader className="gap-2.5 pr-12">
                <div className="flex flex-wrap items-center gap-x-2.5 gap-y-1">
                  <SheetTitle>{detail.employee_name ?? detail.staff_id}</SheetTitle>
                  {!detail.unmatched_product_codes?.length && detail.matched_plans.length > 0 ? (
                    <Badge variant="good">Matched</Badge>
                  ) : (
                    <Badge variant="warn">Unmatched</Badge>
                  )}
                </div>

                {!!detail.unmatched_product_codes?.length && <p className="text-sm text-warn">
                  Missing product mapping: {detail.unmatched_product_codes.join(", ")}
                </p>}
                <p className="text-xs text-muted-foreground">
                  <span className="font-mono">{detail.staff_id}</span>
                  {detail.match_method && (
                    <>
                      <MetaDot />
                      {METHOD_LABEL[detail.match_method as MatchMethod] ??
                        detail.match_method}
                      {detail.match_confidence !== null &&
                        ` ${Math.round(detail.match_confidence * 100)}%`}
                    </>
                  )}
                  {/* The derived values the rules evaluated. The EMPTY case is
                    * printed too: on an unmatched row "nothing was derived" is
                    * usually the reason, and silence there reads as no fact. */}
                  {Object.keys(detail.derived_attribute_values).length > 0 ? (
                    Object.entries(detail.derived_attribute_values).map(
                      ([k, v]) => (
                        <Fragment key={k}>
                          <MetaDot />
                          {k}{" "}
                          <span className="text-foreground">{String(v)}</span>
                        </Fragment>
                      ),
                    )
                  ) : (
                    <>
                      <MetaDot />
                      no derived attributes
                    </>
                  )}
                </p>

                {detail.matched_plans.length > 0 ? (
                  <div className="flex flex-wrap gap-1">
                    {detail.matched_plans.map((p, i) => (
                      <span
                        // `product_code` repeats across matched categories,
                        // so the position disambiguates the key.
                        key={`${p.product_code}~${i}`}
                        className="inline-flex items-center gap-1 rounded-md bg-muted px-1.5 py-0.5 text-xs font-medium text-foreground"
                        title={[
                          p.product_name ?? p.product_code,
                          p.plan_code ? `Plan ${p.plan_code}` : null,
                          p.category_display,
                          // `override_source` has no other render site in the
                          // app, and "which enrolment/bulk run put them here?"
                          // is the first question an overridden plan raises.
                          p.plan_overridden
                            ? p.override_source
                              ? `Overridden · ${p.override_source.replace(/_/g, " ")}`
                              : "Overridden"
                            : null,
                        ]
                          .filter(Boolean)
                          .join(" · ")}
                      >
                        {p.product_code}
                        {p.plan_code && (
                          // Separated, or "GCGP 2" reads as a quantity of
                          // GCGP rather than the plan the member is on.
                          <span className="text-muted-foreground">
                            <span aria-hidden className="mr-1 text-subtle">
                              ·
                            </span>
                            {p.plan_code}
                          </span>
                        )}
                        {p.plan_overridden && (
                          // `aria-label` on a bare span is not exposed — the
                          // element has no role for it to name, so the marker
                          // was announced as an asterisk or not at all. The
                          // glyph is decorative; the word is the content.
                          <>
                            <span aria-hidden className="text-warn">
                              *
                            </span>
                            <span className="sr-only">(overridden)</span>
                          </>
                        )}
                      </span>
                    ))}
                  </div>
                ) : (
                  <p className="text-xs text-muted-foreground">
                    No product categories matched.
                  </p>
                )}

                <div>
                  <Button asChild size="sm" variant="outline">
                    <Link
                      to="/policy-admin/member-coverage"
                      search={{ employee: detail.id, view: "broker" as const }}
                    >
                      <ArrowUpRight className="size-4" />
                      Open coverage record
                    </Link>
                  </Button>
                </div>
              </SheetHeader>

              <SheetBody className="space-y-4">
                <EmployeeMappingEditor key={`mapping-${detail.id}`} employee={detail}
                  groups={categoryGroups} actionContainer={actionContainer} disabled={rosterEditing}
                  onDirtyChange={setMappingDirty} />
                <EmployeeRosterEditor key={detail.id} employee={detail} actionContainer={actionContainer}
                  disabled={mappingDirty} onEditingChange={setRosterEditing} />
              </SheetBody>
              <SheetFooter className="shrink-0"><div className="w-full" ref={setActionContainer} /></SheetFooter>
            </>
          )}
        </SheetContent>
      </Sheet>

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
  const gaps = data.employees_with_product_gaps ?? data.employees_unmatched;
  return (
    <>
      {gaps > 0 ? (
        <>
          <span className="tabular-nums">
            {(data.employees_total - gaps).toLocaleString()}
          </span>{" "}
          matched across applicable products ·
          <ListingExceptionLink
            count={gaps}
            label="unmatched"
            onClick={onShowUnmatched}
          />
        </>
      ) : (
        <span>All {data.employees_matched.toLocaleString()} matched across applicable products</span>
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
