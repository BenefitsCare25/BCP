import { useState } from "react";
import { CheckCircle2, FileText, Lock, Play, Plus } from "lucide-react";
import { toast } from "sonner";
import {
  useActivatePolicyYear,
  useActivationReadiness,
  useCreatePolicyYear,
  usePolicyYears,
} from "@/api/hooks";
import { api } from "@/api/client";
import { useSession } from "@/stores/session";
import { AlertDialog } from "@/components/ui/alert-dialog";
import { SkeletonTable } from "@/components/ui/skeleton";
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
import { Label } from "@/components/ui/label";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import {
  Sheet,
  SheetBody,
  SheetClose,
  SheetContent,
  SheetFooter,
  SheetHeader,
  SheetTitle,
  SheetTrigger,
} from "@/components/ui/sheet";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { formatError } from "@/lib/errors";
import { triggerDownload } from "@/lib/download";
import { PageGuide } from "@/components/ui/page-guide";
import { InfoHint } from "@/components/ui/tooltip";
import {
  MONTHS,
  formatPolicyRange,
  lastDayOfMonth,
  toIsoDate,
} from "@/lib/policy-year";

interface RangeDraft {
  startYear: number;
  startMonth: number;
  startDay: number;
  endYear: number;
  endMonth: number;
  endDay: number;
}

function defaultDraft(years: { year: number }[]): RangeDraft {
  const nextYear = years.length
    ? Math.max(...years.map((y) => y.year)) + 1
    : new Date().getFullYear();
  return {
    startYear: nextYear,
    startMonth: 1,
    startDay: 1,
    endYear: nextYear,
    endMonth: 12,
    endDay: 31,
  };
}

/** Clamp a day to the valid range for its month (handles month/leap-year shrink). */
function clampDay(year: number, month: number, day: number): number {
  return Math.min(Math.max(day, 1), lastDayOfMonth(year, month));
}

function draftIsValid(draft: RangeDraft): boolean {
  if (draft.startYear < 2000 || draft.startYear > 2100) return false;
  if (draft.endYear < 2000 || draft.endYear > 2100) return false;
  if (draft.startDay < 1 || draft.startDay > lastDayOfMonth(draft.startYear, draft.startMonth))
    return false;
  if (draft.endDay < 1 || draft.endDay > lastDayOfMonth(draft.endYear, draft.endMonth))
    return false;
  const start = toIsoDate(draft.startYear, draft.startMonth, draft.startDay);
  const end = toIsoDate(draft.endYear, draft.endMonth, draft.endDay);
  return end >= start;
}

/** Open the activation snapshot JSON in a new tab via the authed client —
 * a raw <a href> to the API would bypass the bearer token + tenant header. */
function SnapshotLink({ policyYearId }: { policyYearId: string }) {
  const [busy, setBusy] = useState(false);

  async function handleView() {
    setBusy(true);
    try {
      const blob = await api.download(`/policy-years/${policyYearId}/snapshot`);
      const url = URL.createObjectURL(
        new Blob([blob], { type: "application/json" }),
      );
      window.open(url, "_blank", "noopener");
      setTimeout(() => URL.revokeObjectURL(url), 60_000);
    } catch (e) {
      toast.error(formatError(e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <button
      type="button"
      className="text-sm underline text-primary disabled:opacity-50"
      disabled={busy}
      onClick={() => void handleView()}
    >
      View JSON
    </button>
  );
}

/** Download the auto-filled Fact-Find Form (.docx) for one policy year. */
function FactFindDownload({
  policyYearId,
  year,
}: {
  policyYearId: string;
  year: number;
}) {
  const [busy, setBusy] = useState(false);

  async function handleDownload() {
    setBusy(true);
    try {
      const res = await api.downloadResponse(
        `/policy-years/${policyYearId}/fact-find-form`,
      );
      triggerDownload(await res.blob(), `fact-find-${year}.docx`);

      // The backend reports which best-effort fields it could not fully fill.
      const notes = decodeURIComponent(
        res.headers.get("X-FactFind-Notes") ?? "",
      ).trim();
      if (notes) {
        toast.warning("Fact-find downloaded — some fields need manual entry", {
          description: notes.split(" | ").join("\n"),
        });
      } else {
        toast.success("Fact-find form downloaded");
      }
    } catch (e) {
      toast.error(formatError(e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <Button
      size="sm"
      variant="outline"
      disabled={busy}
      onClick={handleDownload}
      title="Download the auto-filled Fact-Find Form (.docx)"
    >
      <FileText className="size-3.5" /> Fact-find
    </Button>
  );
}

/** Download the quotation slip (.xlsx) that accompanies the fact-find when
 * shopping the risk — rates left blank for the quoting insurer. */
function QuotationSlipDownload({
  policyYearId,
  year,
}: {
  policyYearId: string;
  year: number;
}) {
  const [busy, setBusy] = useState(false);

  async function handleDownload() {
    setBusy(true);
    try {
      const res = await api.downloadResponse(
        `/policy-years/${policyYearId}/reports/quotation-slip`,
      );
      triggerDownload(await res.blob(), `quotation-slip-${year}.xlsx`);
    } catch (e) {
      toast.error(formatError(e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <Button
      size="sm"
      variant="outline"
      disabled={busy}
      onClick={handleDownload}
      title="Download the quotation slip (.xlsx) — rates blank for insurers to quote"
    >
      <FileText className="size-3.5" /> Quotation slip
    </Button>
  );
}

export function ActivationsPage() {
  const { data: years = [], isLoading } = usePolicyYears();
  const currentPolicyYearId = useSession((s) => s.currentPolicyYearId);
  const setPolicyYear = useSession((s) => s.setPolicyYear);
  const { data: readiness } = useActivationReadiness(currentPolicyYearId ?? undefined);
  const activate = useActivatePolicyYear();
  const createYear = useCreatePolicyYear();
  const [confirmId, setConfirmId] = useState<string | null>(null);
  const [createOpen, setCreateOpen] = useState(false);
  const [draft, setDraft] = useState<RangeDraft>(() => defaultDraft(years));

  const onSheetOpenChange = (next: boolean) => {
    setCreateOpen(next);
    if (next) setDraft(defaultDraft(years));
  };

  const confirmTarget = years.find((y) => y.id === confirmId);
  const canActivate = readiness?.ready ?? false;
  const blockerReason = !readiness
    ? "Loading…"
    : readiness.total_categories === 0
      ? "No categories — upload a placement slip first."
      : readiness.unconfirmed_categories > 0
        ? `${readiness.unconfirmed_categories} of ${readiness.total_categories} categories still need to be confirmed.`
        : "";

  const previewRange = draftIsValid(draft)
    ? formatPolicyRange(
        toIsoDate(draft.startYear, draft.startMonth, draft.startDay),
        toIsoDate(draft.endYear, draft.endMonth, draft.endDay),
      )
    : null;

  return (
    <div className="space-y-5 max-w-7xl">
      <div className="flex justify-end">
        <Sheet open={createOpen} onOpenChange={onSheetOpenChange}>
          <SheetTrigger asChild>
            <Button>
              <Plus className="size-4" /> New policy year
            </Button>
          </SheetTrigger>
          <SheetContent side="right">
            <SheetHeader>
              <div className="flex items-center gap-1">
                <SheetTitle>Create policy year</SheetTitle>
                <InfoHint>
                  Group benefits policies don't always run Jan–Dec. Pick the
                  coverage window — typically the date the policy is placed
                  through to the renewal date.
                </InfoHint>
              </div>
            </SheetHeader>
            <SheetBody className="space-y-4">
              <div className="flex flex-col gap-1.5">
                <Label>Coverage starts</Label>
                <div className="grid grid-cols-[1fr_80px_110px] gap-2">
                  <Select
                    value={String(draft.startMonth)}
                    onValueChange={(v) => {
                      const startMonth = Number.parseInt(v, 10);
                      setDraft({
                        ...draft,
                        startMonth,
                        startDay: clampDay(draft.startYear, startMonth, draft.startDay),
                      });
                    }}
                  >
                    <SelectTrigger>
                      <SelectValue />
                    </SelectTrigger>
                    <SelectContent>
                      {MONTHS.map((m) => (
                        <SelectItem key={m.value} value={String(m.value)}>
                          {m.label}
                        </SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                  <Input
                    type="number"
                    aria-label="Start day"
                    min={1}
                    max={lastDayOfMonth(draft.startYear, draft.startMonth)}
                    value={draft.startDay}
                    onChange={(e) =>
                      setDraft({
                        ...draft,
                        startDay: Number.parseInt(e.target.value, 10) || 0,
                      })
                    }
                  />
                  <Input
                    type="number"
                    aria-label="Start year"
                    min={2000}
                    max={2100}
                    value={draft.startYear}
                    onChange={(e) => {
                      const startYear = Number.parseInt(e.target.value, 10) || 0;
                      setDraft({
                        ...draft,
                        startYear,
                        startDay: clampDay(startYear, draft.startMonth, draft.startDay),
                      });
                    }}
                  />
                </div>
              </div>
              <div className="flex flex-col gap-1.5">
                <Label>Coverage ends</Label>
                <div className="grid grid-cols-[1fr_80px_110px] gap-2">
                  <Select
                    value={String(draft.endMonth)}
                    onValueChange={(v) => {
                      const endMonth = Number.parseInt(v, 10);
                      setDraft({
                        ...draft,
                        endMonth,
                        endDay: clampDay(draft.endYear, endMonth, draft.endDay),
                      });
                    }}
                  >
                    <SelectTrigger>
                      <SelectValue />
                    </SelectTrigger>
                    <SelectContent>
                      {MONTHS.map((m) => (
                        <SelectItem key={m.value} value={String(m.value)}>
                          {m.label}
                        </SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                  <Input
                    type="number"
                    aria-label="End day"
                    min={1}
                    max={lastDayOfMonth(draft.endYear, draft.endMonth)}
                    value={draft.endDay}
                    onChange={(e) =>
                      setDraft({
                        ...draft,
                        endDay: Number.parseInt(e.target.value, 10) || 0,
                      })
                    }
                  />
                  <Input
                    type="number"
                    aria-label="End year"
                    min={2000}
                    max={2100}
                    value={draft.endYear}
                    onChange={(e) => {
                      const endYear = Number.parseInt(e.target.value, 10) || 0;
                      setDraft({
                        ...draft,
                        endYear,
                        endDay: clampDay(endYear, draft.endMonth, draft.endDay),
                      });
                    }}
                  />
                </div>
              </div>
              <div className="rounded-md border border-border bg-muted/40 p-3 text-sm">
                <div className="text-[10px] uppercase tracking-wider text-muted-foreground">
                  Preview
                </div>
                <div className="font-medium mt-0.5">
                  {previewRange ?? (
                    <span className="text-error">
                      End date must be on or after start date.
                    </span>
                  )}
                </div>
              </div>
            </SheetBody>
            <SheetFooter>
              <SheetClose asChild>
                <Button variant="outline">Cancel</Button>
              </SheetClose>
              <Button
                disabled={createYear.isPending || !draftIsValid(draft)}
                onClick={async () => {
                  try {
                    const created = await createYear.mutateAsync({
                      start_date: toIsoDate(
                        draft.startYear,
                        draft.startMonth,
                        draft.startDay,
                      ),
                      end_date: toIsoDate(
                        draft.endYear,
                        draft.endMonth,
                        draft.endDay,
                      ),
                    });
                    toast.success(
                      `Created ${formatPolicyRange(created.start_date, created.end_date)}`,
                    );
                    setPolicyYear(created.id);
                    setCreateOpen(false);
                  } catch (err) {
                    toast.error(formatError(err));
                  }
                }}
              >
                Create
              </Button>
            </SheetFooter>
          </SheetContent>
        </Sheet>
      </div>

      <Card>
        <CardHeader>
          <CardTitle>Policy years</CardTitle>
          <CardDescription>
            {years.length} year{years.length === 1 ? "" : "s"} for this client
          </CardDescription>
        </CardHeader>
        <CardContent>
          {isLoading ? (
            <SkeletonTable rows={3} columns={4} />
          ) : (
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Coverage</TableHead>
                  <TableHead>Status</TableHead>
                  <TableHead>Activated</TableHead>
                  <TableHead>Snapshot</TableHead>
                  <TableHead>Documents</TableHead>
                  <TableHead className="text-right">Action</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {years.map((y) => {
                  const isCurrent = y.id === currentPolicyYearId;
                  const isDraft = y.status === "draft";
                  return (
                    <TableRow key={y.id}>
                      <TableCell className="font-medium">
                        {formatPolicyRange(y.coverage_start, y.coverage_end)}
                        {(y.coverage_start !== y.start_date ||
                          y.coverage_end !== y.end_date) && (
                          <div className="text-xs font-normal text-muted-foreground mt-0.5">
                            Year span{" "}
                            {formatPolicyRange(y.start_date, y.end_date)} ·
                            product periods vary
                          </div>
                        )}
                      </TableCell>
                      <TableCell>
                        {y.status === "active" ? (
                          <Badge variant="good">Active</Badge>
                        ) : y.status === "archived" ? (
                          <Badge variant="default">Archived</Badge>
                        ) : (
                          <Badge variant="warn">Draft</Badge>
                        )}
                      </TableCell>
                      <TableCell className="text-muted-foreground">
                        {y.activated_at
                          ? new Date(y.activated_at).toLocaleString()
                          : "—"}
                      </TableCell>
                      <TableCell>
                        {y.status === "active" ? (
                          <SnapshotLink policyYearId={y.id} />
                        ) : (
                          <span className="text-muted-foreground">—</span>
                        )}
                      </TableCell>
                      <TableCell>
                        <div className="flex items-center gap-1.5">
                          <FactFindDownload policyYearId={y.id} year={y.year} />
                          <QuotationSlipDownload
                            policyYearId={y.id}
                            year={y.year}
                          />
                        </div>
                      </TableCell>
                      <TableCell className="text-right">
                        {isDraft && isCurrent ? (
                          <Button
                            size="sm"
                            variant={canActivate ? "default" : "outline"}
                            disabled={!canActivate || activate.isPending}
                            onClick={() => setConfirmId(y.id)}
                            title={canActivate ? "Activate policy year" : blockerReason}
                          >
                            {canActivate ? (
                              <Play className="size-3.5" />
                            ) : (
                              <Lock className="size-3.5" />
                            )}
                            Activate
                          </Button>
                        ) : y.status === "active" ? (
                          <span className="inline-flex items-center gap-1 text-good text-sm">
                            <CheckCircle2 className="size-3.5" /> Snapshot saved
                          </span>
                        ) : null}
                      </TableCell>
                    </TableRow>
                  );
                })}
              </TableBody>
            </Table>
          )}
        </CardContent>
      </Card>

      <AlertDialog
        open={!!confirmId}
        onOpenChange={(o) => !o && setConfirmId(null)}
        title={
          confirmTarget
            ? `Activate ${formatPolicyRange(confirmTarget.coverage_start, confirmTarget.coverage_end)}?`
            : "Activate policy year?"
        }
        description={
          <>
            This snapshots the current configuration —{" "}
            <strong>{readiness?.total_categories ?? 0} categories</strong>{" "}
            and all matched employees — and flips the policy year to{" "}
            <strong>active</strong>. Activation is one-way; further edits will
            need a new policy year.
          </>
        }
        confirmLabel="Activate"
        confirmVariant="default"
        loading={activate.isPending}
        onConfirm={async () => {
          if (!confirmId) return;
          try {
            const result = await activate.mutateAsync(confirmId);
            toast.success(
              `Activated — ${result.snapshot_counts.categories} categories, ${result.snapshot_counts.employees} employees snapshotted`,
            );
            setConfirmId(null);
          } catch (err) {
            toast.error(formatError(err));
          }
        }}
      />

      <PageGuide
        purpose="Create and activate policy years. Activation freezes the current configuration (categories, employees, dependants) into an immutable snapshot. Once activated, further changes require a new policy year."
        connections={[
          { label: "← Product categories", description: "All categories must be confirmed before activation is allowed" },
          { label: "← Employees", description: "Employee-to-category match assignments are captured in the snapshot" },
          { label: "← Dependants", description: "Linked dependant records are included in the activation snapshot" },
        ]}
      />
    </div>
  );
}
