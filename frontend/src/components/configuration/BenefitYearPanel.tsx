import { useState } from "react";
import { Archive, CalendarPlus, CheckCircle2, Copy, FileText, Loader2, Rocket, Trash2 } from "lucide-react";
import { toast } from "sonner";
import { api } from "@/api/client";
import {
  useArchivePolicyYear,
  useCopyPolicyYear,
  useCreatePolicyYear,
  useDeletePolicyYear,
  usePolicyYearDeletionImpact,
  usePolicyYearReadiness,
  useSetCurrentPolicyYear,
  useUpdatePolicyYear,
} from "@/api/hooks";
import { AlertDialog } from "@/components/ui/alert-dialog";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { triggerDownload } from "@/lib/download";
import { formatError } from "@/lib/errors";
import { notify } from "@/stores/notifications";
import type { PolicyYear } from "@/types";

function benefitYearId(startIso: string, endIso: string): string {
  return `${startIso.replaceAll("-", "").slice(0, 6)}-${endIso.replaceAll("-", "").slice(0, 6)}`;
}

function addDays(iso: string, days: number): string {
  const [year, month, day] = iso.split("-").map(Number);
  const date = new Date(Date.UTC(year, month - 1, day));
  date.setUTCDate(date.getUTCDate() + days);
  return date.toISOString().slice(0, 10);
}

function oneYearMinusDay(startIso: string): string {
  const [year, month, day] = startIso.split("-").map(Number);
  const date = new Date(Date.UTC(year, month - 1, day));
  date.setUTCFullYear(date.getUTCFullYear() + 1);
  date.setUTCDate(date.getUTCDate() - 1);
  return date.toISOString().slice(0, 10);
}

function nextSpan(years: PolicyYear[]): { start: string; end: string } {
  if (!years.length) {
    const year = new Date().getFullYear();
    return { start: `${year}-01-01`, end: `${year}-12-31` };
  }
  const latestEnd = years.map((year) => year.end_date).sort().at(-1)!;
  const start = addDays(latestEnd, 1);
  return { start, end: oneYearMinusDay(start) };
}

function DownloadButton({ label, title, onDownload }: {
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
        } catch (error) {
          notify({ message: formatError(error), tone: "error" });
        } finally {
          setBusy(false);
        }
      }}
    >
      {busy ? <Loader2 className="size-3.5 animate-spin" /> : <FileText className="size-3.5" />}
      {label}
    </Button>
  );
}

export function BenefitYearPanel({ years, viewingId, onViewYear, readOnly = false }: {
  years: PolicyYear[];
  viewingId: string | null;
  onViewYear: (id: string | null) => void;
  readOnly?: boolean;
}) {
  const create = useCreatePolicyYear();
  const update = useUpdatePolicyYear();
  const remove = useDeletePolicyYear();
  const copy = useCopyPolicyYear();
  const setCurrent = useSetCurrentPolicyYear();
  const archive = useArchivePolicyYear();
  const [confirmDelete, setConfirmDelete] = useState<PolicyYear | null>(null);
  const [deleteConfirmation, setDeleteConfirmation] = useState("");
  const [dateDraft, setDateDraft] = useState<Record<string, {
    start_date?: string;
    end_date?: string;
  }>>({});
  const previousYear = years.length
    ? years.reduce((a, b) => (a.start_date > b.start_date ? a : b))
    : null;
  const viewingYear = years.find((year) => year.id === viewingId) ?? null;
  const readiness = usePolicyYearReadiness(viewingYear?.id);
  const deletionImpact = usePolicyYearDeletionImpact(confirmDelete?.id);

  const clearDateDraft = (id: string, field: "start_date" | "end_date") =>
    setDateDraft((draft) => ({ ...draft, [id]: { ...draft[id], [field]: undefined } }));

  const patchDate = async (
    policyYear: PolicyYear,
    field: "start_date" | "end_date",
    value: string,
  ) => {
    if (!value || value === policyYear[field]) {
      clearDateDraft(policyYear.id, field);
      return;
    }
    try {
      await update.mutateAsync({ policyYearId: policyYear.id, payload: { [field]: value } });
    } catch (error) {
      toast.error(formatError(error));
    } finally {
      clearDateDraft(policyYear.id, field);
    }
  };

  const createYear = async (copyPrevious: boolean) => {
    const span = nextSpan(years);
    try {
      if (copyPrevious && previousYear) {
        const result = await copy.mutateAsync({
          sourceId: previousYear.id,
          payload: { start_date: span.start, end_date: span.end },
        });
        onViewYear(result.policy_year.id);
        toast.success("Benefit year copied as a review-ready draft");
      } else {
        const created = await create.mutateAsync({ start_date: span.start, end_date: span.end });
        onViewYear(created.id);
        toast.success("Benefit year added");
      }
    } catch (error) {
      toast.error(formatError(error));
    }
  };

  const download = (policyYear: PolicyYear, path: string, filename: string) => async () => {
    const response = await api.downloadResponse(`/policy-years/${policyYear.id}/${path}`);
    triggerDownload(await response.blob(), `${filename}-${policyYear.year}`);
  };

  const runLifecycle = async (action: "live" | "archive") => {
    if (!viewingYear) return;
    try {
      if (action === "live") {
        await setCurrent.mutateAsync(viewingYear.id);
        toast.success("Benefit year is now live");
      } else {
        await archive.mutateAsync(viewingYear.id);
        toast.success("Benefit year archived");
      }
    } catch (error) {
      toast.error(formatError(error));
    }
  };

  return (
    <Card>
      <CardHeader className="flex-col items-stretch gap-3 space-y-0 sm:flex-row sm:items-start sm:justify-between">
        <CardTitle>Benefit years</CardTitle>
        {!readOnly && <div className="flex flex-col gap-2 sm:flex-row">
          <Button
            size="sm"
            variant="outline"
            disabled={!previousYear || copy.isPending}
            onClick={() => createYear(true)}
          >
            <Copy className="size-4" />
            Copy previous
          </Button>
          <Button size="sm" disabled={create.isPending} onClick={() => createYear(false)}>
            <CalendarPlus className="size-4" />
            Add benefit year
          </Button>
        </div>}
      </CardHeader>
      <CardContent className="space-y-4">
        {!years.length ? (
          <div className="rounded-lg border border-border bg-muted/30 p-6 text-center text-sm text-muted-foreground">
            Add the first period to begin configuration.
          </div>
        ) : (
          <div className="space-y-3">
            {years.map((policyYear) => {
              const selected = policyYear.id === viewingId;
              const period = benefitYearId(policyYear.start_date, policyYear.end_date);
              return (
                <section
                  key={policyYear.id}
                  aria-label={`${period} benefit year`}
                  className={`rounded-lg border p-3 sm:p-4 ${
                    selected ? "border-primary bg-primary/5" : "border-border bg-card"
                  }`}
                >
                  <div className="flex flex-col gap-3 xl:flex-row xl:items-end">
                    <div className="flex min-w-44 items-center gap-2 self-start xl:self-center">
                      <button
                        type="button"
                        className="rounded-sm text-left font-semibold text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring/50"
                        onClick={() => onViewYear(policyYear.id)}
                        aria-pressed={selected}
                      >
                        {period}
                      </button>
                      <Badge variant={
                        policyYear.status === "active" ? "good" : policyYear.status === "draft" ? "info" : "default"
                      }>
                        {policyYear.status === "active" ? "Live" : policyYear.status === "draft" ? "Draft" : "Archived"}
                      </Badge>
                    </div>
                    <div className="grid flex-1 grid-cols-1 gap-2 sm:grid-cols-2 xl:max-w-[320px]">
                      {(["start_date", "end_date"] as const).map((field) => (
                        <label key={field} className="space-y-1 text-xs text-muted-foreground">
                          {field === "start_date" ? "Start date" : "End date"}
                          <Input
                            type="date"
                            className="h-8"
                            disabled={readOnly}
                            value={dateDraft[policyYear.id]?.[field] ?? policyYear[field]}
                            onChange={(event) => setDateDraft((draft) => ({
                              ...draft,
                              [policyYear.id]: { ...draft[policyYear.id], [field]: event.target.value },
                            }))}
                            onBlur={(event) => patchDate(policyYear, field, event.target.value)}
                          />
                        </label>
                      ))}
                    </div>
                    <div className="flex flex-wrap gap-1.5 xl:ml-auto">
                      <DownloadButton
                        label="Fact-find"
                        title="Download the auto-filled Fact-Find Form"
                        onDownload={download(policyYear, "fact-find-form", "fact-find")}
                      />
                      <DownloadButton
                        label="Quotation"
                        title="Download the quotation slip"
                        onDownload={download(policyYear, "reports/quotation-slip", "quotation-slip")}
                      />
                      <DownloadButton
                        label="Placement"
                        title="Download the placement slip"
                        onDownload={download(policyYear, "reports/placement-slip", "placement-slip")}
                      />
                      {!readOnly && <Button
                        size="sm"
                        variant={selected ? "secondary" : "outline"}
                        onClick={() => onViewYear(policyYear.id)}
                      >
                        {selected ? "Viewing" : "View setup"}
                      </Button>}
                      {!readOnly && <Button
                        size="icon-sm"
                        variant="ghost"
                        className="text-error"
                        disabled={policyYear.status === "active"}
                        title={policyYear.status === "active"
                          ? "The live benefit year cannot be deleted"
                          : "Check whether this draft can be deleted"}
                        onClick={() => {
                          setDeleteConfirmation("");
                          setConfirmDelete(policyYear);
                        }}
                      >
                        <Trash2 className="size-4" />
                        <span className="sr-only">Delete benefit year</span>
                      </Button>}
                    </div>
                  </div>
                </section>
              );
            })}
          </div>
        )}

        {viewingYear && (
          <section className="rounded-lg border border-border bg-muted/30 p-4">
            <div className="flex flex-col gap-3 lg:flex-row lg:items-start lg:justify-between">
              <div className="space-y-2">
                <div className="flex items-center gap-2">
                  <CheckCircle2 className="size-4 text-primary" />
                  <h3 className="text-sm font-semibold text-foreground">Launch readiness</h3>
                </div>
                {readiness.isLoading ? (
                  <p className="text-sm text-muted-foreground">Checking configuration…</p>
                ) : readiness.data?.ready ? (
                  <p className="text-sm text-good">Required configuration is complete.</p>
                ) : (
                  <ul className="list-disc space-y-1 pl-5 text-sm text-error">
                    {(readiness.data?.blockers ?? ["Readiness could not be confirmed."]).map(
                      (blocker) => <li key={blocker}>{blocker}</li>,
                    )}
                  </ul>
                )}
                {readiness.data?.warnings.map((warning) => (
                  <p key={warning} className="text-sm text-warn">{warning}</p>
                ))}
              </div>
              {!readOnly && <div className="flex flex-wrap gap-2">
                {viewingYear.status !== "active" && (
                  <Button
                    size="sm"
                    loading={setCurrent.isPending}
                    disabled={!readiness.data?.ready}
                    onClick={() => runLifecycle("live")}
                  >
                    <Rocket className="size-4" />
                    Make live
                  </Button>
                )}
                {viewingYear.status === "draft" && (
                  <Button
                    size="sm"
                    variant="outline"
                    loading={archive.isPending}
                    onClick={() => runLifecycle("archive")}
                  >
                    <Archive className="size-4" />
                    Archive
                  </Button>
                )}
              </div>}
            </div>
          </section>
        )}
      </CardContent>

      <AlertDialog
        open={Boolean(confirmDelete)}
        onOpenChange={(open) => {
          if (!open) {
            setConfirmDelete(null);
            setDeleteConfirmation("");
          }
        }}
        title="Delete this draft benefit year?"
        description={confirmDelete ? (
          <>
            This permanently deletes <strong>{benefitYearId(
              confirmDelete.start_date,
              confirmDelete.end_date,
            )}</strong> and its configuration. Operational years are retained and must be archived instead.
            {deletionImpact.isLoading ? (
              <span className="mt-3 block">Checking linked records…</span>
            ) : deletionImpact.data?.deletable ? (
              <label className="mt-3 block space-y-1 text-foreground">
                Type the benefit-year ID to confirm.
                <Input
                  value={deleteConfirmation}
                  placeholder={benefitYearId(confirmDelete.start_date, confirmDelete.end_date)}
                  onChange={(event) => setDeleteConfirmation(event.target.value)}
                  autoComplete="off"
                />
              </label>
            ) : (
              <span className="mt-3 block text-error">
                {deletionImpact.data?.reason ?? "This benefit year cannot be deleted."}
              </span>
            )}
          </>
        ) : null}
        confirmLabel="Delete draft"
        confirmVariant="destructive"
        loading={remove.isPending}
        confirmDisabled={
          !deletionImpact.data?.deletable ||
          !confirmDelete ||
          deleteConfirmation !== benefitYearId(confirmDelete.start_date, confirmDelete.end_date)
        }
        onConfirm={async () => {
          if (!confirmDelete) return;
          try {
            await remove.mutateAsync(confirmDelete.id);
            if (confirmDelete.id === viewingId) onViewYear(null);
            toast.success("Draft benefit year deleted");
            setConfirmDelete(null);
          } catch (error) {
            toast.error(formatError(error));
          }
        }}
      />
    </Card>
  );
}
