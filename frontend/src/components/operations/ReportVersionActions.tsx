import { useState } from "react";
import {
  ArrowLeftRight,
  Download,
  History,
  Loader2,
} from "lucide-react";
import { toast } from "sonner";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Sheet,
  SheetBody,
  SheetContent,
  SheetDescription,
  SheetHeader,
  SheetTitle,
} from "@/components/ui/sheet";
import {
  type CreateReportVersionInput,
  downloadMovement,
  downloadReportVersion,
  useCreateReportVersion,
  useReportVersions,
  useMovementSummary,
  useReportVersionStatus,
} from "@/api/reports";
import { parseServerDate } from "@/lib/attention";
import { formatError } from "@/lib/errors";

const MAX_HISTORY = 50;

/** Short relative time: "just now", "5m ago", "3h ago", "2d ago", else a date. */
function relTime(iso: string | null): string {
  if (!iso) return "";
  const diff = Date.now() - parseServerDate(iso).getTime();
  const mins = Math.round(diff / 60_000);
  if (mins < 1) return "just now";
  if (mins < 60) return `${mins}m ago`;
  const hrs = Math.round(mins / 60);
  if (hrs < 24) return `${hrs}h ago`;
  const days = Math.round(hrs / 24);
  if (days < 30) return `${days}d ago`;
  return parseServerDate(iso).toLocaleDateString();
}

interface LatestProps {
  policyYearId: string;
  reportType: string;
  scopeKey: string | null;
  createInput: CreateReportVersionInput;
  disabled?: boolean;
}

/* ── Latest-mode: one auto-kept copy; Download refreshes + downloads it ──────── */
export function ReportVersionActions({
  policyYearId,
  reportType,
  scopeKey,
  createInput,
  disabled,
}: LatestProps) {
  const status = useReportVersionStatus(
    policyYearId,
    reportType,
    scopeKey,
    !disabled,
  );
  const create = useCreateReportVersion(policyYearId);
  const latest = status.data?.latest ?? null;

  const onDownload = () =>
    create.mutate(createInput, {
      onSuccess: (v) =>
        downloadReportVersion(v.id, v.file_name).catch((e) =>
          toast.error(formatError(e)),
        ),
      onError: (e) => toast.error(formatError(e)),
    });

  return (
    <div className="flex flex-col items-end gap-1">
      <Button
        type="button"
        size="sm"
        variant="outline"
        onClick={onDownload}
        disabled={disabled || create.isPending}
      >
        {create.isPending ? (
          <Loader2 className="size-4 animate-spin" />
        ) : (
          <Download className="size-4" />
        )}
        Download
      </Button>
      <span className="text-xs text-muted-foreground">
        {latest
          ? `Latest copy · ${relTime(latest.created_at)}`
          : "Keeps the latest copy for this year"}
      </span>
    </div>
  );
}

/**
 * What was last SENT, and what has moved since. A status line, not an action.
 *
 * There is no "Save version" button any more, and its absence is the design:
 * downloading a submission-grade report retains what it produced (see
 * `report_registry.RETAINED_ON_DOWNLOAD`), so the archive is complete by
 * construction. A retention step a broker had to remember recorded only the
 * submissions somebody thought to press a button for, and nothing at all about
 * the ones they did not — while the button itself read as the way to GET the
 * report, which it never was.
 *
 * Two rules this line depends on:
 *
 * - **It reports; it never asks.** The stale badge names what changed since the
 *   file went out. It used to be a nag to press Save, which is why it read as
 *   an error state on a page where nothing was wrong.
 * - **Only an UNMASKED download files a copy**, so the record only ever names
 *   files an insurer could act on. A masked preview filed into the same series
 *   made "Last sent v5" name something nobody sent, and — pulled after a roster
 *   change — cleared the changed-since badge, asserting the insurer held a
 *   roster it had never seen.
 * - **The history merges the SUPERSEDED series.** Retiring a report type must
 *   not orphan the record of what was submitted under it — the bytes are the
 *   whole point and a broker can reach them from nowhere else.
 */
export function SubmissionRecord({
  policyYearId,
  reportType,
  supersededTypes = [],
  scopeKey,
  hasMovement,
  disabled,
  scopeLabel,
  filesOnDownload = true,
}: {
  policyYearId: string;
  reportType: string;
  /** Retired series merged into the history drawer, newest-first by date. */
  supersededTypes?: string[];
  scopeKey: string | null;
  hasMovement: boolean;
  disabled?: boolean;
  /** Names what the record is scoped to ("AIA"), for the drawer title. */
  scopeLabel?: string;
  /** Whether a download in the row's CURRENT state files a copy. Only an
   *  unmasked pull does: the masked copy is an internal preview, and an insurer
   *  matches members on the identification number. Saying "downloading files a
   *  copy here" under a masked toggle would be a promise the server does not
   *  keep. */
  filesOnDownload?: boolean;
}) {
  const status = useReportVersionStatus(
    policyYearId,
    reportType,
    scopeKey,
    !disabled,
  );
  const [historyOpen, setHistoryOpen] = useState(false);

  const latest = status.data?.latest ?? null;
  const isStale = status.data?.is_stale ?? false;
  // Only asked for once the badge is already showing — see useMovementSummary.
  const movement = useMovementSummary(latest?.id ?? null, isStale && hasMovement);
  const moved = movement.data;
  // Zeros are omitted rather than printed: "0 left" is noise, and the counts can
  // legitimately be all-zero (a change the diff does not model), in which case
  // the unqualified wording is still true.
  const movedLabel =
    moved &&
    [
      moved.added ? `${moved.added} added` : null,
      moved.removed ? `${moved.removed} left` : null,
      moved.changed ? `${moved.changed} updated` : null,
    ]
      .filter(Boolean)
      .join(" · ");

  const onMovementSinceLive = () => {
    if (!latest) return;
    downloadMovement(
      latest.id,
      `${reportType}-changes-since-v${latest.version_no}.xlsx`,
      "live",
    ).catch((e) => toast.error(formatError(e)));
  };

  if (disabled) return null;

  return (
    <div className="flex flex-wrap items-center gap-x-3 gap-y-1 border-t border-border px-4 py-2 text-xs">
      {latest ? (
        <span className="text-muted-foreground">
          Last sent{" "}
          <span className="font-medium text-foreground">
            v{latest.version_no}
          </span>{" "}
          · {relTime(latest.created_at)}
          {latest.generated_by ? ` by ${latest.generated_by}` : ""}
        </span>
      ) : (
        <span className="text-muted-foreground">
          Not sent yet —{" "}
          {filesOnDownload
            ? "downloading files a copy here."
            : "an unmasked download files the copy an insurer receives."}
        </span>
      )}

      {latest && isStale && (
        hasMovement ? (
          <button
            type="button"
            onClick={onMovementSinceLive}
            title={
              "Roster membership changes since the file that went out. Plan and " +
              "category edits also count as changed but are not totalled here. " +
              "Click to download them."
            }
          >
            {/* Prefixed "Roster" because the counts are membership diffs only,
                while staleness also fires on plan/category edits — an
                unqualified "2 updated" would assert more than it knows. */}
            <Badge variant="warn">
              {movedLabel ? `Roster: ${movedLabel}` : "Roster changed"} ›
            </Badge>
          </button>
        ) : (
          <Badge variant="warn">Changed since</Badge>
        )
      )}

      {latest && (
        <button
          type="button"
          className="ml-auto inline-flex items-center gap-1 text-muted-foreground hover:text-foreground"
          onClick={() => setHistoryOpen(true)}
        >
          <History className="size-3" /> History
        </button>
      )}

      <HistorySheet
        open={historyOpen}
        onOpenChange={setHistoryOpen}
        policyYearId={historyOpen ? policyYearId : null}
        reportType={reportType}
        supersededTypes={supersededTypes}
        scopeKey={scopeKey}
        scopeLabel={scopeLabel}
        hasMovement={hasMovement}
      />
    </div>
  );
}

function HistorySheet({
  open,
  onOpenChange,
  policyYearId,
  reportType,
  supersededTypes,
  scopeKey,
  scopeLabel,
  hasMovement,
}: {
  open: boolean;
  onOpenChange: (v: boolean) => void;
  policyYearId: string | null;
  reportType: string;
  supersededTypes: string[];
  scopeKey: string | null;
  scopeLabel?: string;
  hasMovement: boolean;
}) {
  // One request for the live series plus the retired ones; the server merges
  // and orders them by date, since version numbers restart per series.
  const types = [reportType, ...supersededTypes].join(",");
  const versions = useReportVersions(policyYearId, types, scopeKey);
  const all = versions.data ?? [];
  const shown = all.slice(0, MAX_HISTORY);
  // A version can only be diffed when its OWN predecessor is still retained.
  // `version_no > 1` is not that test: pruning drops the oldest of a series, so
  // the surviving bottom row has a number above 1 and no baseline — and the
  // server reads a missing predecessor as "initial submission", which would
  // list the entire roster under ADDITIONS. Offer the button only when the
  // predecessor is present (it 409s `baseline_pruned` either way).
  const diffable = new Set(
    all
      .filter((v) =>
        all.some(
          (o) => o.report_type === v.report_type && o.version_no === v.version_no - 1,
        ),
      )
      .map((v) => v.id),
  );
  const linkCls =
    "inline-flex items-center gap-1 text-foreground hover:underline";

  return (
    <Sheet open={open} onOpenChange={onOpenChange}>
      <SheetContent>
        <SheetHeader>
          <SheetTitle>
            Submission history{scopeLabel ? ` — ${scopeLabel}` : ""}
          </SheetTitle>
          <SheetDescription>
            Every copy of this report that has left the building, newest first.
            One is filed each time the content differs from the last.
          </SheetDescription>
        </SheetHeader>
        <SheetBody>
          {shown.length === 0 ? (
            <p className="text-sm text-muted-foreground">
              Nothing sent yet. Downloading the report files the first copy.
            </p>
          ) : (
            <div className="divide-y divide-border">
              {shown.map((v) => (
                <div
                  key={v.id}
                  className="flex items-center justify-between gap-4 py-3"
                >
                  <div className="min-w-0">
                    <div className="font-medium text-foreground">
                      v{v.version_no}
                      {v.label ? ` · ${v.label}` : ""}
                      {/* Named only on rows from a retired series — on the live
                          one it would be the sheet's own title on every row. */}
                      {v.report_type !== reportType && (
                        <span className="ml-2 text-2xs uppercase tracking-wide text-subtle">
                          {v.report_label}
                        </span>
                      )}
                    </div>
                    <div className="text-xs text-muted-foreground">
                      {relTime(v.created_at)}
                      {v.generated_by ? ` · ${v.generated_by}` : ""}
                      {v.summary?.member_count != null
                        ? ` · ${v.summary.member_count} members`
                        : ""}
                      {v.summary?.masked === false ? " · unmasked" : ""}
                    </div>
                  </div>
                  <div className="flex shrink-0 gap-3 text-xs">
                    <button
                      type="button"
                      className={linkCls}
                      onClick={() =>
                        downloadReportVersion(v.id, v.file_name).catch((e) =>
                          toast.error(formatError(e)),
                        )
                      }
                    >
                      <Download className="size-3" /> Download
                    </button>
                    {hasMovement && diffable.has(v.id) && (
                      <button
                        type="button"
                        className={linkCls}
                        onClick={() =>
                          downloadMovement(
                            v.id,
                            `${v.report_type}-v${v.version_no}-changes.xlsx`,
                          ).catch((e) => toast.error(formatError(e)))
                        }
                        title={`Changes vs v${v.version_no - 1}`}
                      >
                        <ArrowLeftRight className="size-3" /> Changes
                      </button>
                    )}
                  </div>
                </div>
              ))}
              {all.length > MAX_HISTORY && (
                <p className="pt-3 text-xs text-muted-foreground">
                  Showing the {MAX_HISTORY} most recent of {all.length} copies.
                </p>
              )}
            </div>
          )}
        </SheetBody>
      </SheetContent>
    </Sheet>
  );
}
