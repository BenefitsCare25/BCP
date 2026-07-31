import { useState } from "react";
import { CalendarPlus, Copy, FileText, Loader2, Star, Trash2 } from "lucide-react";
import { toast } from "sonner";
import {
  useCopyPolicyYear,
  useCreatePolicyYear,
  useDeletePolicyYear,
  useSetCurrentPolicyYear,
  useUpdatePolicyYear,
} from "@/api/hooks";
import { api } from "@/api/client";
import { AlertDialog } from "@/components/ui/alert-dialog";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { formatError } from "@/lib/errors";
import { triggerDownload } from "@/lib/download";
import {
  formatPolicyRange,
  isPastPolicyPeriod,
  isWithinPolicyPeriod,
} from "@/lib/policy-year";
import type { PolicyYear } from "@/types";

/** `2026-01-01` + `2026-12-31` → `202601-202612` (insurer-style benefit-year id). */
function benefitYearId(startIso: string, endIso: string): string {
  const s = startIso.replaceAll("-", "").slice(0, 6);
  const e = endIso.replaceAll("-", "").slice(0, 6);
  return `${s}-${e}`;
}

/** Add whole days to an ISO date, returning an ISO date. UTC-based so a
 *  timezone offset (e.g. SGT +8) can't shift the result across a day. */
function addDays(iso: string, days: number): string {
  const [y, m, d] = iso.split("-").map(Number);
  const dt = new Date(Date.UTC(y, m - 1, d));
  dt.setUTCDate(dt.getUTCDate() + days);
  return dt.toISOString().slice(0, 10);
}

/** The day one year on, minus a day: `2026-01-01` → `2026-12-31`. */
function oneYearMinusDay(startIso: string): string {
  const [y, m, d] = startIso.split("-").map(Number);
  const dt = new Date(Date.UTC(y, m - 1, d));
  dt.setUTCFullYear(dt.getUTCFullYear() + 1);
  dt.setUTCDate(dt.getUTCDate() - 1);
  return dt.toISOString().slice(0, 10);
}

function nextSpan(years: PolicyYear[]): { start: string; end: string } {
  // Default a new year to the span right after the latest existing one, so it
  // never overlaps (the server rejects overlaps).
  if (years.length === 0) {
    const y = new Date().getFullYear();
    return { start: `${y}-01-01`, end: `${y}-12-31` };
  }
  const latestEnd = years
    .map((y) => y.end_date)
    .reduce((a, b) => (a > b ? a : b));
  const start = addDays(latestEnd, 1);
  return { start, end: oneYearMinusDay(start) };
}

function DownloadButton({
  label,
  title,
  onDownload,
}: {
  label: string;
  title: string;
  onDownload: () => Promise<void>;
}) {
  const [busy, setBusy] = useState(false);
  return (
    <Button
      size="sm"
      variant="outline"
      disabled={busy}
      title={title}
      onClick={async () => {
        setBusy(true);
        try {
          await onDownload();
        } catch (e) {
          toast.error(formatError(e));
        } finally {
          setBusy(false);
        }
      }}
    >
      {busy ? (
        <Loader2 className="size-3.5 animate-spin" />
      ) : (
        <FileText className="size-3.5" />
      )}
      {label}
    </Button>
  );
}

export function BenefitYearPanel({
  years,
  viewingId,
  onViewYear,
}: {
  years: PolicyYear[];
  // The year currently being viewed on the Configuration page (highlighted).
  viewingId: string | null;
  // Switch which year the config page views (add/copy select the new year;
  // deleting the viewed year clears it back to the current one).
  onViewYear: (id: string | null) => void;
}) {
  const create = useCreatePolicyYear();
  const update = useUpdatePolicyYear();
  const remove = useDeletePolicyYear();
  const setCurrent = useSetCurrentPolicyYear();
  const copy = useCopyPolicyYear();

  const [confirmDelete, setConfirmDelete] = useState<PolicyYear | null>(null);
  // Set-current outside the year's coverage period: confirmed, never blocked
  // (see the button below).
  const [confirmCurrent, setConfirmCurrent] = useState<PolicyYear | null>(null);
  // Date edit buffer, keyed by year id → field. Makes the date inputs
  // controlled so a rejected PATCH (e.g. an overlap 409) reverts to the server
  // value instead of leaving the invalid typed date on screen.
  const [dateDraft, setDateDraft] = useState<
    Record<string, { start_date?: string; end_date?: string }>
  >({});

  // "Copy" seeds the new year from the PREVIOUS period — the most recent
  // existing year by date — since the new year's span sits right after it. That
  // carries config forward at renewal even when building several years ahead
  // (2027 → 2028), not just from the current/active year.
  const previousYear = years.length
    ? years.reduce((a, b) => (a.start_date > b.start_date ? a : b))
    : null;

  /** Returns whether it actually landed — the confirm dialog must stay open on
   *  failure, and the error is reported as a toast rather than thrown. */
  const promote = async (py: PolicyYear): Promise<boolean> => {
    try {
      await setCurrent.mutateAsync(py.id);
      toast.success("Set as current benefit year");
      return true;
    } catch (e) {
      toast.error(formatError(e));
      return false;
    }
  };

  const clearDateDraft = (id: string, field: "start_date" | "end_date") =>
    setDateDraft((d) => ({ ...d, [id]: { ...d[id], [field]: undefined } }));

  const patchDates = async (
    py: PolicyYear,
    field: "start_date" | "end_date",
    value: string,
  ) => {
    if (!value || value === py[field]) {
      clearDateDraft(py.id, field);
      return;
    }
    try {
      await update.mutateAsync({
        policyYearId: py.id,
        payload: { [field]: value },
      });
    } catch (e) {
      toast.error(formatError(e));
    } finally {
      // Drop the local buffer so the input reflects the server value —
      // reverted on failure, refreshed on success.
      clearDateDraft(py.id, field);
    }
  };

  const onAdd = async () => {
    const span = nextSpan(years);
    try {
      const created = await create.mutateAsync({
        start_date: span.start,
        end_date: span.end,
      });
      onViewYear(created.id);
      toast.success("Benefit year added");
    } catch (e) {
      toast.error(formatError(e));
    }
  };

  const onCopy = async () => {
    if (!previousYear) return;
    const span = nextSpan(years);
    try {
      const result = await copy.mutateAsync({
        sourceId: previousYear.id,
        payload: { start_date: span.start, end_date: span.end },
      });
      onViewYear(result.policy_year.id);
      const n = Object.values(result.copied).reduce((a, b) => a + b, 0);
      toast.success(
        `Copied ${n} configuration rows from ${benefitYearId(
          previousYear.start_date,
          previousYear.end_date,
        )} into the new benefit year`,
      );
    } catch (e) {
      toast.error(formatError(e));
    }
  };

  const downloadFactFind = (py: PolicyYear) => async () => {
    const res = await api.downloadResponse(
      `/policy-years/${py.id}/fact-find-form`,
    );
    triggerDownload(await res.blob(), `fact-find-${py.year}.docx`);
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
  };

  const downloadQuotation = (py: PolicyYear) => async () => {
    const res = await api.downloadResponse(
      `/policy-years/${py.id}/reports/quotation-slip`,
    );
    triggerDownload(await res.blob(), `quotation-slip-${py.year}.xlsx`);
  };

  const downloadPlacement = (py: PolicyYear) => async () => {
    const res = await api.downloadResponse(
      `/policy-years/${py.id}/reports/placement-slip`,
    );
    triggerDownload(await res.blob(), `placement-slip-${py.year}.xlsx`);
  };

  return (
    <Card>
      <CardHeader className="flex-row items-start justify-between gap-3 space-y-0">
        <div>
          <CardTitle>Benefit years</CardTitle>
        </div>
        <div className="flex shrink-0 gap-2">
          <Button
            size="sm"
            variant="outline"
            disabled={!previousYear || copy.isPending}
            onClick={onCopy}
            title={
              previousYear
                ? `Create a new benefit year and copy ${benefitYearId(
                    previousYear.start_date,
                    previousYear.end_date,
                  )}'s configuration into it`
                : "Create a new benefit year seeded from the previous year"
            }
          >
            {copy.isPending ? (
              <Loader2 className="size-4 animate-spin" />
            ) : (
              <Copy className="size-4" />
            )}
            Copy from previous year
          </Button>
          <Button size="sm" disabled={create.isPending} onClick={onAdd}>
            {create.isPending ? (
              <Loader2 className="size-4 animate-spin" />
            ) : (
              <CalendarPlus className="size-4" />
            )}
            Add benefit year
          </Button>
        </div>
      </CardHeader>
      <CardContent className="space-y-4">
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-border text-left text-2xs uppercase tracking-wider text-muted-foreground">
                <th className="pb-2 pr-3 font-medium">Start date</th>
                <th className="pb-2 pr-3 font-medium">End date</th>
                <th className="pb-2 pr-3 font-medium">Current</th>
                <th className="pb-2 pr-3 font-medium">Documents</th>
                <th className="pb-2 font-medium text-right">Remove</th>
              </tr>
            </thead>
            <tbody>
              {years.length === 0 && (
                <tr>
                  <td
                    colSpan={5}
                    className="py-4 text-center text-sm text-muted-foreground"
                  >
                    No benefit years yet — use “Add benefit year” to create the
                    first one.
                  </td>
                </tr>
              )}
              {years.map((py) => {
                const isCurrent = py.status === "active";
                const isSelected = py.id === viewingId;
                // Today inside the coverage period is the NORMAL case, not a
                // precondition. Disabling the button outside it was a dead end:
                // the server imposes no such rule, so a company onboarded on a
                // forward-dated year (say 1 Sep 2026 – 31 Aug 2027) could not be
                // made current at all before 1 Sep — its portal, claims and AI
                // review all dark, with no in-app way out. Confirm instead.
                const inPeriod = isWithinPolicyPeriod(
                  py.coverage_start,
                  py.coverage_end,
                );
                return (
                  <tr
                    key={py.id}
                    className={`border-b border-border/60 ${
                      isSelected ? "bg-muted/40" : ""
                    }`}
                  >
                    <td className="py-2 pr-3">
                      <Input
                        type="date"
                        className="h-8 w-[150px]"
                        value={dateDraft[py.id]?.start_date ?? py.start_date}
                        onChange={(e) =>
                          setDateDraft((d) => ({
                            ...d,
                            [py.id]: { ...d[py.id], start_date: e.target.value },
                          }))
                        }
                        onBlur={(e) => patchDates(py, "start_date", e.target.value)}
                      />
                    </td>
                    <td className="py-2 pr-3">
                      <Input
                        type="date"
                        className="h-8 w-[150px]"
                        value={dateDraft[py.id]?.end_date ?? py.end_date}
                        onChange={(e) =>
                          setDateDraft((d) => ({
                            ...d,
                            [py.id]: { ...d[py.id], end_date: e.target.value },
                          }))
                        }
                        onBlur={(e) => patchDates(py, "end_date", e.target.value)}
                      />
                    </td>
                    <td className="py-2 pr-3">
                      {isCurrent ? (
                        <Badge variant="good">
                          <Star className="size-3" /> Current
                        </Badge>
                      ) : (
                        <Button
                          size="sm"
                          variant="ghost"
                          className="h-7 text-xs"
                          disabled={setCurrent.isPending}
                          title={
                            inPeriod
                              ? "Make this the year the member portal reads and claims are submitted against"
                              : isPastPolicyPeriod(py.coverage_end)
                                ? "This benefit year has already ended — you'll be asked to confirm."
                                : "This benefit year hasn't started yet — you'll be asked to confirm."
                          }
                          onClick={() =>
                            inPeriod ? promote(py) : setConfirmCurrent(py)
                          }
                        >
                          Set current
                        </Button>
                      )}
                    </td>
                    <td className="py-2 pr-3">
                      <div className="flex gap-1.5">
                        <DownloadButton
                          label="Fact-find"
                          title="Download the auto-filled Fact-Find Form (.docx)"
                          onDownload={downloadFactFind(py)}
                        />
                        <DownloadButton
                          label="Quotation"
                          title="Download the quotation slip (.xlsx) — rates blank for insurers to quote"
                          onDownload={downloadQuotation(py)}
                        />
                        <DownloadButton
                          label="Placement"
                          title="Download the placement slip (.xlsx) — the configured rates + premiums"
                          onDownload={downloadPlacement(py)}
                        />
                      </div>
                    </td>
                    <td className="py-2 text-right">
                      <Button
                        size="icon"
                        variant="ghost"
                        className="size-8 text-error"
                        disabled={isCurrent}
                        title={
                          isCurrent
                            ? "Set another year as current before deleting this one"
                            : "Delete this benefit year and its configuration"
                        }
                        onClick={() => setConfirmDelete(py)}
                      >
                        <Trash2 className="size-4" />
                      </Button>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>

      </CardContent>

      {/* Two different situations share `inPeriod === false`, and only one of
          them is routine — a year that has ENDED points the portal at expired
          coverage and fails every claim's grace-period check, so it must not be
          described as normal onboarding. Tone is `info`: promoting a year is a
          confirmation, not a destructive act. */}
      <AlertDialog
        open={!!confirmCurrent}
        onOpenChange={(o) => !o && setConfirmCurrent(null)}
        tone="info"
        confirmVariant="default"
        title={
          confirmCurrent && isPastPolicyPeriod(confirmCurrent.coverage_end)
            ? "Set an ended benefit year as current?"
            : "Set a benefit year before its start date as current?"
        }
        description={
          confirmCurrent ? (
            isPastPolicyPeriod(confirmCurrent.coverage_end) ? (
              <>
                <strong>
                  {formatPolicyRange(
                    confirmCurrent.coverage_start,
                    confirmCurrent.coverage_end,
                  )}
                </strong>{" "}
                has already ended. Members would see expired coverage, and new
                claims would be rejected once the submission grace period runs
                out. Set the year that is actually in force instead, unless you
                are deliberately reopening this one.
              </>
            ) : (
              <>
                Today is before{" "}
                <strong>
                  {formatPolicyRange(
                    confirmCurrent.coverage_start,
                    confirmCurrent.coverage_end,
                  )}
                </strong>{" "}
                starts. Members will immediately see this year as their current
                coverage, and claims will be submitted against it. This is
                normal when onboarding a company ahead of its policy start.
              </>
            )
          ) : null
        }
        confirmLabel="Set as current"
        loading={setCurrent.isPending}
        onConfirm={async () => {
          if (!confirmCurrent) return;
          // Only dismiss on success — a failed promotion must not look like it
          // worked (`promote` reports the error as a toast, never throws).
          if (await promote(confirmCurrent)) setConfirmCurrent(null);
        }}
      />

      <AlertDialog
        open={!!confirmDelete}
        onOpenChange={(o) => !o && setConfirmDelete(null)}
        title="Delete this benefit year?"
        description={
          confirmDelete ? (
            <>
              This permanently deletes the{" "}
              <strong>
                {benefitYearId(
                  confirmDelete.start_date,
                  confirmDelete.end_date,
                )}
              </strong>{" "}
              benefit year and all its configuration (categories, plans,
              product setups, flex scheme). This cannot be undone.
            </>
          ) : null
        }
        confirmLabel="Delete"
        confirmVariant="destructive"
        loading={remove.isPending}
        onConfirm={async () => {
          if (!confirmDelete) return;
          try {
            await remove.mutateAsync(confirmDelete.id);
            if (confirmDelete.id === viewingId) onViewYear(null);
            toast.success("Benefit year deleted");
            setConfirmDelete(null);
          } catch (e) {
            toast.error(formatError(e));
          }
        }}
      />
    </Card>
  );
}
