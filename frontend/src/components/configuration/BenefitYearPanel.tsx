import { useState } from "react";
import {
  Archive,
  CalendarPlus,
  CheckCircle2,
  Copy,
  FileText,
  Loader2,
  Rocket,
  Trash2,
  TriangleAlert,
} from "lucide-react";
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
import { downloadResponseAsFile } from "@/lib/download";
import { formatError } from "@/lib/errors";
import { notify } from "@/stores/notifications";
import type { PolicyYear } from "@/types";
import {
  BenefitYearDateFields,
  type BenefitYearDateField,
} from "./BenefitYearDateFields";

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

function dateRangeError(
  years: PolicyYear[],
  policyYear: PolicyYear,
  start: string,
  end: string,
  field: BenefitYearDateField,
): string | null {
  if (end < start) {
    return field === "start_date"
      ? "Start date must be on or before the end date."
      : "End date must be on or after the start date.";
  }
  const overlap = years.find(
    (candidate) =>
      candidate.id !== policyYear.id &&
      start <= candidate.end_date &&
      candidate.start_date <= end,
  );
  return overlap
    ? `Overlaps ${benefitYearId(overlap.start_date, overlap.end_date)} (${overlap.start_date} to ${overlap.end_date}).`
    : null;
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
  const [panelError, setPanelError] = useState<string | null>(null);
  const [dateDraft, setDateDraft] = useState<Record<string, {
    start_date?: string;
    end_date?: string;
  }>>({});
  const [dateErrors, setDateErrors] = useState<
    Record<string, Partial<Record<BenefitYearDateField, string>>>
  >({});
  const previousYear = years.length
    ? years.reduce((a, b) => (a.start_date > b.start_date ? a : b))
    : null;
  const viewingYear = years.find((year) => year.id === viewingId) ?? null;
  const readiness = usePolicyYearReadiness(viewingYear?.id);
  const deletionImpact = usePolicyYearDeletionImpact(confirmDelete?.id);

  const clearDateDraft = (id: string, field: BenefitYearDateField) =>
    setDateDraft((draft) => ({ ...draft, [id]: { ...draft[id], [field]: undefined } }));

  const setDateError = (id: string, field: BenefitYearDateField, message?: string) =>
    setDateErrors((errors) => ({
      ...errors,
      [id]: { ...errors[id], [field]: message },
    }));

  const clearDateErrors = (id: string) =>
    setDateErrors((errors) => ({ ...errors, [id]: {} }));

  const patchDate = async (
    policyYear: PolicyYear,
    field: BenefitYearDateField,
    value: string,
  ) => {
    if (!value || value === policyYear[field]) {
      clearDateDraft(policyYear.id, field);
      setDateError(policyYear.id, field);
      return;
    }
    const start = field === "start_date"
      ? value
      : dateDraft[policyYear.id]?.start_date ?? policyYear.start_date;
    const end = field === "end_date"
      ? value
      : dateDraft[policyYear.id]?.end_date ?? policyYear.end_date;
    const validationError = dateRangeError(years, policyYear, start, end, field);
    if (validationError) {
      setDateError(policyYear.id, field, validationError);
      return;
    }
    setPanelError(null);
    setDateError(policyYear.id, field);
    try {
      await update.mutateAsync({ policyYearId: policyYear.id, payload: { [field]: value } });
      clearDateDraft(policyYear.id, field);
    } catch (error) {
      const message = formatError(error);
      setDateError(policyYear.id, field, message);
      toast.error(message);
    }
  };

  const createYear = async (copyPrevious: boolean) => {
    const span = nextSpan(years);
    setPanelError(null);
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
      const message = formatError(error);
      setPanelError(message);
      toast.error(message);
    }
  };

  const download = (policyYear: PolicyYear, path: string, filename: string) => async () => {
    const response = await api.downloadResponse(`/policy-years/${policyYear.id}/${path}`);
    await downloadResponseAsFile(response, `${filename}-${policyYear.year}`);
  };

  const runLifecycle = async (action: "live" | "archive") => {
    if (!viewingYear) return;
    setPanelError(null);
    try {
      if (action === "live") {
        await setCurrent.mutateAsync(viewingYear.id);
        toast.success("Benefit year is now live");
      } else {
        await archive.mutateAsync(viewingYear.id);
        toast.success("Benefit year archived");
      }
    } catch (error) {
      const message = formatError(error);
      setPanelError(message);
      toast.error(message);
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
        {panelError && (
          <div
            role="alert"
            className="flex items-start gap-2 rounded-lg border border-error/30 bg-error-soft px-3 py-2.5 text-sm text-error"
          >
            <TriangleAlert className="mt-0.5 size-4 shrink-0" aria-hidden="true" />
            <span>{panelError}</span>
          </div>
        )}
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
                    <BenefitYearDateFields
                      policyYear={policyYear}
                      draft={dateDraft[policyYear.id]}
                      errors={dateErrors[policyYear.id]}
                      readOnly={readOnly}
                      onChange={(field, value) => {
                        setPanelError(null);
                        clearDateErrors(policyYear.id);
                        setDateDraft((draft) => ({
                          ...draft,
                          [policyYear.id]: {
                            ...draft[policyYear.id],
                            [field]: value,
                          },
                        }));
                      }}
                      onBlur={(field, value) => patchDate(policyYear, field, value)}
                    />
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
                ) : readiness.isError ? (
                  <p role="alert" className="text-sm text-error">
                    Could not check launch readiness. {formatError(readiness.error)}
                  </p>
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
            ) : deletionImpact.isError ? (
              <span role="alert" className="mt-3 block text-error">
                Could not check linked records. {formatError(deletionImpact.error)}
              </span>
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
            setPanelError(null);
            await remove.mutateAsync(confirmDelete.id);
            if (confirmDelete.id === viewingId) onViewYear(null);
            toast.success("Draft benefit year deleted");
            setConfirmDelete(null);
          } catch (error) {
            const message = formatError(error);
            setPanelError(message);
            setConfirmDelete(null);
            setDeleteConfirmation("");
            toast.error(message);
          }
        }}
      />
    </Card>
  );
}
