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
import { Label } from "@/components/ui/label";
import { InfoHint } from "@/components/ui/tooltip";
import { formatError } from "@/lib/errors";
import { triggerDownload } from "@/lib/download";
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

export function BenefitYearPanel({ years }: { years: PolicyYear[] }) {
  const currentId = useSession((s) => s.currentPolicyYearId);
  const setPolicyYear = useSession((s) => s.setPolicyYear);

  const create = useCreatePolicyYear();
  const update = useUpdatePolicyYear();
  const remove = useDeletePolicyYear();
  const setCurrent = useSetCurrentPolicyYear();
  const copy = useCopyPolicyYear();

  const [confirmDelete, setConfirmDelete] = useState<PolicyYear | null>(null);
  // Grace-period edit buffer keyed by year id, so typing doesn't fight the
  // query cache; committed on blur.
  const [graceDraft, setGraceDraft] = useState<Record<string, string>>({});

  const selected = years.find((y) => y.id === currentId) ?? years[0] ?? null;
  // "Copy" seeds the new year from the PREVIOUS period — the most recent
  // existing year by date — since the new year's span sits right after it. That
  // carries config forward at renewal even when building several years ahead
  // (2027 → 2028), not just from the current/active year.
  const previousYear = years.length
    ? years.reduce((a, b) => (a.start_date > b.start_date ? a : b))
    : null;

  const patchDates = async (
    py: PolicyYear,
    field: "start_date" | "end_date",
    value: string,
  ) => {
    if (!value || value === py[field]) return;
    try {
      await update.mutateAsync({
        policyYearId: py.id,
        payload: { [field]: value },
      });
    } catch (e) {
      toast.error(formatError(e));
    }
  };

  const commitGrace = async (py: PolicyYear) => {
    const raw = graceDraft[py.id];
    if (raw === undefined) return;
    const trimmed = raw.trim();
    const next = trimmed === "" ? null : Number.parseInt(trimmed, 10);
    if (next !== null && (Number.isNaN(next) || next < 0)) {
      toast.error("Grace period must be a whole number of days (or blank).");
      return;
    }
    if (next === py.claim_grace_period_days) return;
    try {
      await update.mutateAsync({
        policyYearId: py.id,
        payload: { claim_grace_period_days: next },
      });
      toast.success("Claim grace period updated");
    } catch (e) {
      toast.error(formatError(e));
    }
  };

  const onAdd = async () => {
    const span = nextSpan(years);
    try {
      const created = await create.mutateAsync({
        start_date: span.start,
        end_date: span.end,
      });
      setPolicyYear(created.id);
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
      setPolicyYear(result.policy_year.id);
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
          <CardDescription>
            Each benefit year holds one version of this client's configuration.
            The <strong>current</strong> year is what the employee portal shows.
          </CardDescription>
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
              <tr className="border-b border-border text-left text-xs uppercase tracking-wider text-muted-foreground">
                <th className="pb-2 pr-3 font-medium">Start date</th>
                <th className="pb-2 pr-3 font-medium">End date</th>
                <th className="pb-2 pr-3 font-medium">ID</th>
                <th className="pb-2 pr-3 font-medium">Current</th>
                <th className="pb-2 pr-3 font-medium">Documents</th>
                <th className="pb-2 font-medium text-right">Remove</th>
              </tr>
            </thead>
            <tbody>
              {years.map((py) => {
                const isCurrent = py.status === "active";
                const isSelected = py.id === selected?.id;
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
                        defaultValue={py.start_date}
                        onBlur={(e) => patchDates(py, "start_date", e.target.value)}
                      />
                    </td>
                    <td className="py-2 pr-3">
                      <Input
                        type="date"
                        className="h-8 w-[150px]"
                        defaultValue={py.end_date}
                        onBlur={(e) => patchDates(py, "end_date", e.target.value)}
                      />
                    </td>
                    <td className="py-2 pr-3 font-mono text-xs text-muted-foreground">
                      {benefitYearId(py.start_date, py.end_date)}
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
                          onClick={async () => {
                            try {
                              await setCurrent.mutateAsync(py.id);
                              toast.success("Set as current benefit year");
                            } catch (e) {
                              toast.error(formatError(e));
                            }
                          }}
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

        {selected && (
          <div className="flex flex-col gap-1.5 border-t border-border pt-4 sm:max-w-md">
            <div className="flex items-center gap-1">
              <Label htmlFor="grace">Claim submission grace period (days)</Label>
              <InfoHint>
                Days after this year's coverage period ends during which members
                may still submit claims. Leave blank for no submission deadline.
              </InfoHint>
            </div>
            <Input
              id="grace"
              type="number"
              min={0}
              placeholder="No deadline"
              className="h-9 w-40"
              value={
                graceDraft[selected.id] ??
                (selected.claim_grace_period_days?.toString() ?? "")
              }
              onChange={(e) =>
                setGraceDraft((d) => ({ ...d, [selected.id]: e.target.value }))
              }
              onBlur={() => commitGrace(selected)}
            />
            <p className="text-xs text-muted-foreground">
              Applies to the selected benefit year (
              {benefitYearId(selected.start_date, selected.end_date)}).
            </p>
          </div>
        )}
      </CardContent>

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
            if (confirmDelete.id === currentId) setPolicyYear(null);
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
