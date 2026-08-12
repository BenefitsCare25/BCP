import { useEffect, useMemo, useState } from "react";
import { Check, Link2, Loader2, Save, Sparkles, Trash2, Unlink } from "lucide-react";
import {
  useAutoMatchDependants,
  useBulkDeleteDependants,
  useUpdateDependant,
} from "@/api/hooks";
import { api } from "@/api/client";
import {
  useDependantFacets,
  useDependantQueryList,
} from "@/api/dependantQuery";
import { useMemberFacets } from "@/api/memberQuery";
import {
  DependantFilterBar,
  type DependantFilterState,
  EMPTY_DEPENDANT_FILTERS,
  dependantFiltersAreEmpty,
  toDependantQuery,
} from "@/components/operations/DependantFilterBar";
import { useDebouncedValue } from "@/lib/use-debounced-value";
import { useSession } from "@/stores/session";
import { AlertDialog } from "@/components/ui/alert-dialog";
import { Badge } from "@/components/ui/badge";
import { SkeletonTable } from "@/components/ui/skeleton";
import { Button } from "@/components/ui/button";
import { PaginationControls } from "@/components/ui/pagination-controls";
import { ReportDownloadButton } from "@/components/operations/ReportDownloadButton";
import { RosterTabActions } from "./rosterTabActions";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Input } from "@/components/ui/input";
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
import { DependantApprovals } from "@/components/operations/DependantApprovals";
import {
  DualCoverageBanner,
  DualCoverageSheet,
} from "@/components/operations/DualCoveragePanel";
import {
  type DualLifeRef,
  livesByDependant,
  useDualCoverage,
} from "@/api/dualCoverage";
import { PageGuide } from "@/components/ui/page-guide";
import { InfoHint } from "@/components/ui/tooltip";
import { coerceAttrs } from "@/lib/attrs";
import { ConflictDetailError, formatError } from "@/lib/errors";
import { fmtDate } from "@/lib/format";
import type { EmployeeList } from "@/types";
import { toast } from "sonner";

const PAGE_SIZE = 50;

// Employee-identifying fields stored for re-linking — shown read-only in link
// section, excluded from the editable attributes grid.
const LINK_HINT_KEYS = new Set(["employee_staff_id", "employee_name", "employee_id_no"]);

/**
 * The "Covered twice" cell. It NAMES both employees rather than saying the life
 * is doubled and leaving the broker to go looking: the table shows a
 * dependant's link method but never which employee they hang off, so "also
 * somewhere else" would be unanswerable from this page.
 *
 * A button, not a row click — the row itself opens the dependant's detail pane,
 * and these two destinations must not fight.
 */
function DualCell({
  life,
  onOpen,
}: {
  life: DualLifeRef | undefined;
  onOpen: (subjectKey: string) => void;
}) {
  if (!life) return <span className="text-subtle">—</span>;
  return (
    <button
      type="button"
      className="-m-1 block max-w-56 space-y-0.5 rounded p-1 text-left hover:bg-muted focus-ring"
      onClick={(e) => {
        e.stopPropagation();
        onOpen(life.subject_key);
      }}
    >
      {life.resolved ? (
        <span className="inline-flex items-center gap-1 text-2xs text-good">
          <Check className="size-3" aria-hidden /> Decided
        </span>
      ) : (
        <Badge variant={life.severity === "warn" ? "warn" : "info"}>
          {life.severity === "warn" ? "Same benefit twice" : "Two employees"}
        </Badge>
      )}
      {life.parties.map((p, i) => (
        <span
          key={`${p.employee_id}-${p.dependant_id}-${i}`}
          className="block truncate text-2xs text-muted-foreground"
        >
          <span className="font-mono">{p.staff_id || "—"}</span>{" "}
          {p.employee_name ?? "Unknown"}
        </span>
      ))}
    </button>
  );
}

export function DependantsPage() {
  const policyYearId = useSession((s) => s.currentPolicyYearId);
  const bulkDelete = useBulkDeleteDependants();
  const updateDependant = useUpdateDependant();
  const autoMatch = useAutoMatchDependants();
  const [page, setPage] = useState(0);
  const [filters, setFilters] = useState<DependantFilterState>(
    EMPTY_DEPENDANT_FILTERS,
  );
  const debouncedSearch = useDebouncedValue(filters.q, 300);
  const [showDeleteAll, setShowDeleteAll] = useState(false);
  const [deleteRisk, setDeleteRisk] = useState<number | null>(null);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [editAttrs, setEditAttrs] = useState<Record<string, string>>({});
  const [linkStaffId, setLinkStaffId] = useState("");
  // The review sheet is opened from two places — the banner (everything) and a
  // row's "Covered twice" cell (that one life) — so its state lives here rather
  // than inside either of them.
  const [dualOpen, setDualOpen] = useState(false);
  const [dualFocus, setDualFocus] = useState<string | null>(null);
  // Only the SEARCH text is debounced; a picker click should move the table
  // at once.
  const query = useMemo(
    () => toDependantQuery({ ...filters, q: debouncedSearch }),
    [filters, debouncedSearch],
  );
  const { data, isLoading } = useDependantQueryList(
    policyYearId ?? undefined,
    query,
    { offset: page * PAGE_SIZE, limit: PAGE_SIZE },
  );
  // Unfiltered counts for the header bar. `data.total` follows the active
  // filters, so it cannot state what is on file — the facets carry both
  // unfiltered totals in one request (the page used to spend two extra
  // limit=1 queries on them).
  const { data: facets } = useDependantFacets(policyYearId ?? undefined);
  const { data: memberFacets } = useMemberFacets(policyYearId ?? undefined);
  // Same query the banner and sheet read — one detection pass serves the marker
  // on every row and the review list, so a row can never disagree with the
  // sheet it opens.
  const { data: dual } = useDualCoverage(policyYearId ?? undefined);
  const dualLives = useMemo(() => livesByDependant(dual), [dual]);
  const dependantsTotal = facets?.active_total ?? 0;
  const unlinkedTotal = facets?.unlinked ?? 0;
  // Whether anything has EVER been uploaded spans every status: a roster
  // whose dependants were all soft-terminated by an ADC run still has rows,
  // and gating on the active count alone flips the page back into its
  // first-upload state and reports "Nothing uploaded yet".
  const anyOnFile = facets?.all_statuses_total ?? 0;
  const selected = useMemo(
    () => data?.items.find((d) => d.id === selectedId) ?? null,
    [data, selectedId],
  );

  useEffect(() => {
    setPage(0);
  }, [debouncedSearch]);

  useEffect(() => {
    if (!selected) return;
    setEditAttrs(
      Object.fromEntries(
        Object.entries(selected.attribute_values)
          .filter(([k]) => !LINK_HINT_KEYS.has(k))
          .map(([k, v]) => [k, v == null ? "" : String(v)]),
      ),
    );
    setLinkStaffId("");
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selectedId]);

  if (!policyYearId) return null;
  const total = data?.total ?? 0;
  const pages = Math.ceil(total / PAGE_SIZE);

  return (
    <div className="space-y-5">
      <DependantApprovals policyYearId={policyYearId} />

      {/* Lives insured twice — renders nothing when the roster is clean. */}
      <DualCoverageBanner
        policyYearId={policyYearId}
        onReview={() => {
          setDualFocus(null);
          setDualOpen(true);
        }}
      />
      <DualCoverageSheet
        policyYearId={policyYearId}
        open={dualOpen}
        onOpenChange={setDualOpen}
        focusKey={dualFocus}
        onClearFocus={() => setDualFocus(null)}
      />

      <ListingImportBar
        policyYearId={policyYearId}
        hasRows={facets ? anyOnFile > 0 : undefined}
        stats={
          <ListingCount
            value={dependantsTotal}
            noun={dependantsTotal === 1 ? "dependant" : "dependants"}
          >
            {anyOnFile === 0 ? (
              <span>Nothing uploaded yet</span>
            ) : !facets ? (
              // "All linked" must be a RESULT, never the default while the
              // counts are still in flight (or failed) — this line is the only
              // surface reporting that exception, so guessing it away hides it.
              <span>Checking links…</span>
            ) : unlinkedTotal > 0 ? (
              <>
                <span className="tabular-nums">
                  {(dependantsTotal - unlinkedTotal).toLocaleString()}
                </span>{" "}
                linked ·
                <ListingExceptionLink
                  count={unlinkedTotal}
                  label="unlinked"
                  onClick={() =>
                    setFilters((f) => ({ ...f, linkState: "unlinked" }))
                  }
                />
              </>
            ) : (
              <span>All linked to an employee</span>
            )}
          </ListingCount>
        }
      />

      <RosterTabActions>
        <ReportDownloadButton
          path={`/dependants/coverage-report/export?policy_year_id=${policyYearId}`}
          filename="dependant-coverage.xlsx"
          label="Dependant listing"
          disabled={!total}
        />
        <Button
          variant="outline"
          disabled={autoMatch.isPending || !total}
          onClick={async () => {
            if (!policyYearId) return;
            try {
              const r = await autoMatch.mutateAsync(policyYearId);
              if (r.matched === 0) {
                toast.info(`No new matches found (${r.unmatched} unlinked remain)`);
              } else {
                toast.success(`Auto-matched ${r.matched} dependant${r.matched === 1 ? "" : "s"}${r.unmatched ? ` · ${r.unmatched} still unlinked` : ""}`);
              }
            } catch (err) {
              toast.error(formatError(err));
            }
          }}
        >
          {autoMatch.isPending ? (
            <Loader2 className="size-4 animate-spin" />
          ) : (
            <Sparkles className="size-4" />
          )}
          Auto-match
        </Button>
      </RosterTabActions>

      <Card>
        <CardHeader>
          <div className="flex items-start justify-between gap-3">
            <div>
              <CardTitle>Dependants</CardTitle>
              <CardDescription>
                {dependantsTotal.toLocaleString()} active dependant
                {dependantsTotal === 1 ? "" : "s"} on file
              </CardDescription>
            </div>
            <Button
              variant="outline"
              size="sm"
              disabled={!total}
              onClick={() => setShowDeleteAll(true)}
              className="text-error hover:text-error shrink-0"
            >
              <Trash2 className="size-4" /> Clear all
            </Button>
          </div>
        </CardHeader>
        <CardContent className="space-y-4">
          <DependantFilterBar
            state={filters}
            onChange={(next) => {
              setPage(0);
              setFilters(next);
            }}
            facets={facets}
            memberFacets={memberFacets}
            total={data?.total}
          />
          {isLoading ? (
            <SkeletonTable rows={6} columns={5} />
          ) : total === 0 ? (
            <div className="text-sm text-muted-foreground p-8 text-center border border-dashed border-border rounded-md">
              {dependantFiltersAreEmpty(filters)
                ? "No dependants uploaded yet."
                : "No dependants match these filters."}
            </div>
          ) : (
            <>
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead>Dependant</TableHead>
                    <TableHead>Relationship</TableHead>
                    <TableHead>DOB</TableHead>
                    <TableHead>
                      <span className="inline-flex items-center gap-1">
                        Link to employee
                        <InfoHint>
                          Linked dependants inherit an employee's coverage
                          (matched via staff ID, NRIC, or name). Unlinked rows
                          aren't covered — use Auto-match or link manually.
                        </InfoHint>
                      </span>
                    </TableHead>
                    <TableHead>
                      <span className="inline-flex items-center gap-1">
                        Covered twice
                        <InfoHint>
                          The same person also reaches this company through
                          another employee — usually a child whose parents both
                          work here. Both employees are named; select one to
                          record which of them keeps the cover.
                        </InfoHint>
                      </span>
                    </TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {data?.items.map((d) => (
                    <TableRow
                      key={d.id}
                      className="cursor-pointer"
                      onClick={() => setSelectedId(d.id)}
                    >
                      <TableCell className="font-medium">
                        {(d.attribute_values.dependant_name as string) ?? "—"}
                      </TableCell>
                      <TableCell>
                        {(d.attribute_values.relationship as string) ?? "—"}
                      </TableCell>
                      <TableCell className="text-muted-foreground">
                        {fmtDate(d.attribute_values.date_of_birth as string)}
                      </TableCell>
                      <TableCell>
                        {d.employee_id ? (
                          <Badge variant="good">via {d.link_method}</Badge>
                        ) : (
                          <Badge variant="error">Unlinked</Badge>
                        )}
                      </TableCell>
                      <TableCell>
                        <DualCell
                          life={dualLives.get(d.id)}
                          onOpen={(key) => {
                            setDualFocus(key);
                            setDualOpen(true);
                          }}
                        />
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
        title="Clear all dependants?"
        description={
          <>
            This will permanently delete{" "}
            <strong>{total.toLocaleString()} dependant records</strong> for
            the current policy year. This cannot be undone.
          </>
        }
        confirmLabel={`Delete ${total.toLocaleString()} dependants`}
        loading={bulkDelete.isPending}
        onConfirm={async () => {
          try {
            const r = await bulkDelete.mutateAsync({
              policyYearId,
              confirm: false,
            });
            toast.success(`Deleted ${r.deleted} dependants`);
            setShowDeleteAll(false);
            setPage(0);
          } catch (e) {
            if (
              e instanceof ConflictDetailError &&
              e.detail.code === "member_data_at_risk"
            ) {
              setShowDeleteAll(false);
              setDeleteRisk(Number(e.detail.member_added_at_risk ?? 0));
              return;
            }
            toast.error(formatError(e));
          }
        }}
      />

      <AlertDialog
        open={deleteRisk !== null}
        onOpenChange={(o) => !o && setDeleteRisk(null)}
        title="Member-submitted dependants will be lost"
        description={
          <>
            <strong>{(deleteRisk ?? 0).toLocaleString()}</strong> of these
            dependants were self-added by members through the portal (pending or
            approved). Deleting them is permanent and the member will have to
            re-submit. This cannot be undone.
          </>
        }
        confirmLabel={`Delete anyway (${total.toLocaleString()} dependants)`}
        loading={bulkDelete.isPending}
        onConfirm={async () => {
          try {
            const r = await bulkDelete.mutateAsync({
              policyYearId,
              confirm: true,
            });
            toast.success(`Deleted ${r.deleted} dependants`);
            setDeleteRisk(null);
            setPage(0);
          } catch (e) {
            toast.error(formatError(e));
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
          {selected && (
            <>
              <SheetHeader>
                <SheetTitle>
                  {(selected.attribute_values.dependant_name as string) ??
                    "Dependant"}
                </SheetTitle>
              </SheetHeader>
              <SheetBody className="space-y-4">
                <div>
                  <div className="text-2xs uppercase tracking-wider text-muted-foreground mb-2">
                    Employee link
                  </div>
                  <div className="flex items-center gap-2 mb-2">
                    {selected.employee_id ? (
                      <Badge variant="good">
                        Linked via {selected.link_method}
                      </Badge>
                    ) : (
                      <Badge variant="error">Unlinked</Badge>
                    )}
                    {selected.employee_id && (
                      <Button
                        size="sm"
                        variant="outline"
                        disabled={updateDependant.isPending}
                        onClick={async () => {
                          try {
                            await updateDependant.mutateAsync({
                              dependantId: selected.id,
                              employee_id: null,
                              relink: true,
                            });
                            toast.success("Unlinked");
                            // With the Unlinked filter active the row set
                            // changes under the sheet — close it so it can't
                            // go blank.
                            if (filters.linkState === "linked") setSelectedId(null);
                          } catch (err) {
                            toast.error(formatError(err));
                          }
                        }}
                      >
                        <Unlink className="size-4" /> Unlink
                      </Button>
                    )}
                  </div>
                  {/* Show roster-sourced hints when unlinked */}
                  {!selected.employee_id && (() => {
                    const sid = selected.attribute_values.employee_staff_id as string | undefined;
                    const ename = selected.attribute_values.employee_name as string | undefined;
                    if (!sid && !ename) return null;
                    return (
                      <div className="text-xs text-muted-foreground mb-2 rounded-md bg-muted px-2.5 py-1.5 space-y-0.5">
                        {sid && <div>Roster staff ID: <span className="font-mono">{sid}</span></div>}
                        {ename && <div>Roster name: {ename}</div>}
                      </div>
                    );
                  })()}
                  <div className="flex items-center gap-2">
                    <Input
                      placeholder="Link to staff ID…"
                      value={linkStaffId}
                      onChange={(e) => setLinkStaffId(e.target.value)}
                      className="h-8"
                    />
                    <Button
                      size="sm"
                      variant="outline"
                      disabled={!linkStaffId.trim() || updateDependant.isPending}
                      onClick={async () => {
                        const staff = linkStaffId.trim();
                        try {
                          // limit=200 (server MAX_LIMIT): a top-10 substring
                          // page can miss the exact staff-id match and falsely
                          // report "no employee".
                          const res = await api.get<EmployeeList>(
                            `/employees?policy_year_id=${policyYearId}&q=${encodeURIComponent(staff)}&limit=200`,
                          );
                          const match = res.items.find(
                            (e) =>
                              e.staff_id.toLowerCase() === staff.toLowerCase(),
                          );
                          if (!match) {
                            toast.error(
                              res.total > res.items.length
                                ? `No exact match for staff ID ${staff} in the first ${res.items.length} results — refine the ID`
                                : `No employee with staff ID ${staff}`,
                            );
                            return;
                          }
                          await updateDependant.mutateAsync({
                            dependantId: selected.id,
                            employee_id: match.id,
                            relink: true,
                          });
                          toast.success(
                            `Linked to ${match.employee_name ?? match.staff_id}`,
                          );
                          setLinkStaffId("");
                          // Linking removes the row from an Unlinked-filtered
                          // list — close the sheet so it can't go blank.
                          if (filters.linkState === "unlinked") setSelectedId(null);
                        } catch (err) {
                          toast.error(formatError(err));
                        }
                      }}
                    >
                      <Link2 className="size-4" /> Link
                    </Button>
                  </div>
                </div>

                <div>
                  <div className="flex items-center justify-between gap-2 mb-2">
                    <div className="text-2xs uppercase tracking-wider text-muted-foreground">
                      Details (editable)
                    </div>
                    <Button
                      size="sm"
                      variant="outline"
                      disabled={updateDependant.isPending}
                      onClick={async () => {
                        try {
                          await updateDependant.mutateAsync({
                            dependantId: selected.id,
                            attribute_values: coerceAttrs(editAttrs),
                          });
                          toast.success("Dependant updated");
                        } catch (err) {
                          toast.error(formatError(err));
                        }
                      }}
                    >
                      {updateDependant.isPending ? (
                        <Loader2 className="size-4 animate-spin" />
                      ) : (
                        <Save className="size-4" />
                      )}
                      Save changes
                    </Button>
                  </div>
                  <div className="grid grid-cols-2 gap-2">
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
                            setEditAttrs((prev) => ({
                              ...prev,
                              [k]: e.target.value,
                            }))
                          }
                          className="mt-1 h-8"
                        />
                      </label>
                    ))}
                  </div>
                </div>
              </SheetBody>
            </>
          )}
        </SheetContent>
      </Sheet>

      <PageGuide
        purpose="Upload and manage the dependant listing. Dependants are automatically linked to employees via staff ID, NRIC, or name matching. Only products flagged 'has dependants' include dependant coverage."
        connections={[
          { label: "← Employees", description: "Dependants link to employee records for family plan eligibility" },
          { label: "← Products catalog", description: "Only products with 'has dependants' enabled support dependant enrolment" },
          { label: "→ Flexible benefits", description: "A linked spouse + children set each employee's family-status tier and Flexi wallet" },
          { label: "→ Activation", description: "Dependant data is included in the policy year snapshot on activation" },
        ]}
      />
    </div>
  );
}
