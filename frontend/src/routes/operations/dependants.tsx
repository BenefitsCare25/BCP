import { useEffect, useMemo, useState } from "react";
import { Link2, Loader2, Save, Sparkles, Trash2, Unlink } from "lucide-react";
import {
  useAutoMatchDependants,
  useBulkDeleteDependants,
  useDependants,
  useUpdateDependant,
  useUploadDependants,
} from "@/api/hooks";
import { api } from "@/api/client";
import { useDebouncedValue } from "@/lib/use-debounced-value";
import { useSession } from "@/stores/session";
import { AlertDialog } from "@/components/ui/alert-dialog";
import { Badge } from "@/components/ui/badge";
import { SkeletonTable } from "@/components/ui/skeleton";
import { Button } from "@/components/ui/button";
import { PaginationControls } from "@/components/ui/pagination-controls";
import { ReportDownloadButton } from "@/components/operations/ReportDownloadButton";
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
import { UploadRoster } from "@/components/operations/UploadRoster";
import { DependantApprovals } from "@/components/operations/DependantApprovals";
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

export function DependantsPage() {
  const policyYearId = useSession((s) => s.currentPolicyYearId);
  const upload = useUploadDependants();
  const bulkDelete = useBulkDeleteDependants();
  const updateDependant = useUpdateDependant();
  const autoMatch = useAutoMatchDependants();
  const [page, setPage] = useState(0);
  const [unlinkedOnly, setUnlinkedOnly] = useState(false);
  const [search, setSearch] = useState("");
  const debouncedSearch = useDebouncedValue(search, 300);
  const [showDeleteAll, setShowDeleteAll] = useState(false);
  const [deleteRisk, setDeleteRisk] = useState<number | null>(null);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [editAttrs, setEditAttrs] = useState<Record<string, string>>({});
  const [linkStaffId, setLinkStaffId] = useState("");
  const { data, isLoading } = useDependants(
    policyYearId ?? undefined,
    page * PAGE_SIZE,
    PAGE_SIZE,
    unlinkedOnly,
    debouncedSearch,
  );
  const selected = useMemo(
    () => data?.items.find((d) => d.id === selectedId) ?? null,
    [data, selectedId],
  );

  useEffect(() => {
    setPage(0);
  }, [unlinkedOnly, debouncedSearch]);

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
    <div className="space-y-5 max-w-7xl">
      <DependantApprovals policyYearId={policyYearId} />

      <UploadRoster
        title="Upload dependant roster"
        description="STM template — Staff ID, Employee Name, Dependant Name, Relationship, DOB."
        policyYearId={policyYearId}
        upload={upload}
      />

      <Card>
        <CardHeader>
          <div className="flex items-start justify-between gap-3">
            <div>
              <CardTitle>Dependants</CardTitle>
              <CardDescription>
                {total.toLocaleString()} dependant{total === 1 ? "" : "s"}{unlinkedOnly ? " unlinked" : " on file"}
              </CardDescription>
            </div>
            <div className="flex items-center gap-2 flex-wrap justify-end">
              <Input
                value={search}
                onChange={(e) => setSearch(e.target.value)}
                placeholder="Search name, NRIC, staff ID…"
                aria-label="Search dependants"
                className="h-8 w-52"
              />
              <ReportDownloadButton
                path={`/dependants/coverage-report/export?policy_year_id=${policyYearId}`}
                filename="dependant-coverage.xlsx"
                label="Dependant report"
                size="sm"
                disabled={!total}
              />
              <Button
                variant={unlinkedOnly ? "default" : "outline"}
                size="sm"
                onClick={() => setUnlinkedOnly((v) => !v)}
              >
                Unlinked only
              </Button>
              <Button
                variant="outline"
                size="sm"
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
            <SkeletonTable rows={6} columns={5} />
          ) : total === 0 ? (
            <div className="text-sm text-muted-foreground p-8 text-center border border-dashed border-border rounded-md">
              {debouncedSearch.trim() || unlinkedOnly
                ? "No dependants match the current search or filter."
                : "No dependants uploaded yet."}
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
                  <div className="text-xs uppercase tracking-wider text-muted-foreground mb-2">
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
                            // With "Unlinked only" active the row set changes
                            // under the sheet — close it so it can't go blank.
                            if (unlinkedOnly) setSelectedId(null);
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
                          // Linking removes the row from the "Unlinked only"
                          // list — close the sheet so it can't go blank.
                          if (unlinkedOnly) setSelectedId(null);
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
                    <div className="text-xs uppercase tracking-wider text-muted-foreground">
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
                        <div className="text-[10px] uppercase tracking-wider text-muted-foreground">
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
        purpose="Upload and manage the dependant roster. Dependants are automatically linked to employees via staff ID, NRIC, or name matching. Only products flagged 'has dependants' include dependant coverage."
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
