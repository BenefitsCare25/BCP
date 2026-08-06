import { useState } from "react";
import {
  ArrowLeftRight,
  Download,
  History,
  Loader2,
  Save,
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
import { api } from "@/api/client";
import { triggerDownload } from "@/lib/download";
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

interface LiveDownload {
  path: string;
  filename: string;
}

interface Props {
  policyYearId: string;
  reportType: string;
  scopeKey: string | null;
  createInput: CreateReportVersionInput;
  mode: "versioned" | "latest";
  hasMovement: boolean;
  /** Live "download current (unsaved)" — versioned reports only. */
  liveDownload?: LiveDownload;
  /** Disable when the report can't be generated (e.g. no insurer selected). */
  disabled?: boolean;
}

/** Retained-copy controls for a Reports Center report. Two compact lines: a
 * primary action, and a muted status line that opens the history drawer.
 * Versioned reports keep a series (+ staleness → movement); latest-mode reports
 * keep one auto-refreshed copy per year. */
export function ReportVersionActions(props: Props) {
  return props.mode === "latest" ? (
    <LatestActions {...props} />
  ) : (
    <VersionedActions {...props} />
  );
}

/* ── Latest-mode: one auto-kept copy; Download refreshes + downloads it ──────── */
function LatestActions({
  policyYearId,
  reportType,
  scopeKey,
  createInput,
  disabled,
}: Props) {
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

/* ── Versioned: explicit submissions + history + movement ───────────────────── */
function VersionedActions({
  policyYearId,
  reportType,
  scopeKey,
  createInput,
  hasMovement,
  liveDownload,
  disabled,
}: Props) {
  const status = useReportVersionStatus(
    policyYearId,
    reportType,
    scopeKey,
    !disabled,
  );
  const create = useCreateReportVersion(policyYearId);
  const [historyOpen, setHistoryOpen] = useState(false);

  const latest = status.data?.latest ?? null;
  const isStale = status.data?.is_stale ?? false;
  // Only asked for once the badge is already showing — see useMovementSummary.
  const movement = useMovementSummary(latest?.id ?? null, isStale && hasMovement);
  const moved = movement.data;
  // Zeros are omitted rather than printed: "0 left" is noise on a badge, and
  // the counts can legitimately be all-zero (a change the diff does not model),
  // in which case the original wording is still true.
  const movedLabel =
    moved &&
    [
      moved.added ? `${moved.added} added` : null,
      moved.removed ? `${moved.removed} left` : null,
      moved.changed ? `${moved.changed} updated` : null,
    ]
      .filter(Boolean)
      .join(" · ");

  const onSave = () =>
    create.mutate(createInput, {
      onSuccess: (v) =>
        v.unchanged
          ? toast.info(`No changes since v${v.version_no} — nothing to save`)
          : toast.success(`Saved version ${v.version_no}`),
      onError: (e) => toast.error(formatError(e)),
    });

  const onMovementSinceLive = () => {
    if (!latest) return;
    downloadMovement(
      latest.id,
      `${reportType}-changes-since-v${latest.version_no}.xlsx`,
      "live",
    ).catch((e) => toast.error(formatError(e)));
  };

  return (
    <div className="flex flex-col items-end gap-1">
      <div className="flex items-center gap-2">
        {isStale &&
          (hasMovement ? (
            <button
              type="button"
              onClick={onMovementSinceLive}
              title={
                movedLabel
                  ? "Roster membership changes since this version. Plan and " +
                    "category edits also mark it changed but are not counted " +
                    "here. Click to download."
                  : "Download what changed"
              }
            >
              {/* Prefixed "Roster" because the counts are membership diffs
                  only, while `is_stale` also fires on plan/category edits —
                  an unqualified "2 updated" would assert more than it knows. */}
              <Badge variant="warn">
                {movedLabel ? `Roster: ${movedLabel}` : "Roster changed"} ›
              </Badge>
            </button>
          ) : (
            <Badge variant="warn">Changed</Badge>
          ))}
        <Button
          type="button"
          size="sm"
          variant="outline"
          onClick={onSave}
          disabled={disabled || create.isPending}
        >
          {create.isPending ? (
            <Loader2 className="size-4 animate-spin" />
          ) : (
            <Save className="size-4" />
          )}
          Save version
        </Button>
      </div>
      {latest ? (
        <button
          type="button"
          className="inline-flex items-center gap-1 text-xs text-muted-foreground hover:text-foreground"
          onClick={() => setHistoryOpen(true)}
        >
          v{latest.version_no} · {relTime(latest.created_at)}
          <History className="size-3" /> History
        </button>
      ) : (
        <span className="text-xs text-muted-foreground">Not saved yet</span>
      )}

      <HistorySheet
        open={historyOpen}
        onOpenChange={setHistoryOpen}
        policyYearId={historyOpen ? policyYearId : null}
        reportType={reportType}
        scopeKey={scopeKey}
        hasMovement={hasMovement}
        liveDownload={liveDownload}
      />
    </div>
  );
}

function HistorySheet({
  open,
  onOpenChange,
  policyYearId,
  reportType,
  scopeKey,
  hasMovement,
  liveDownload,
}: {
  open: boolean;
  onOpenChange: (v: boolean) => void;
  policyYearId: string | null;
  reportType: string;
  scopeKey: string | null;
  hasMovement: boolean;
  liveDownload?: LiveDownload;
}) {
  const versions = useReportVersions(policyYearId, reportType, scopeKey);
  const all = versions.data ?? [];
  const shown = all.slice(0, MAX_HISTORY);
  const linkCls =
    "inline-flex items-center gap-1 text-foreground hover:underline";

  const onLive = () => {
    if (!liveDownload) return;
    api
      .download(liveDownload.path)
      .then((b) => triggerDownload(b, liveDownload.filename))
      .catch((e) => toast.error(formatError(e)));
  };

  return (
    <Sheet open={open} onOpenChange={onOpenChange}>
      <SheetContent>
        <SheetHeader>
          <SheetTitle>Version history</SheetTitle>
          <SheetDescription>
            Every saved submission for this report, newest first.
          </SheetDescription>
          {liveDownload && (
            <button
              type="button"
              className={`${linkCls} mt-1 text-sm`}
              onClick={onLive}
            >
              <Download className="size-4" /> Download current (unsaved)
            </button>
          )}
        </SheetHeader>
        <SheetBody>
          {shown.length === 0 ? (
            <p className="text-sm text-muted-foreground">No versions saved yet.</p>
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
                    </div>
                    <div className="text-xs text-muted-foreground">
                      {relTime(v.created_at)}
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
                    {hasMovement && v.version_no > 1 && (
                      <button
                        type="button"
                        className={linkCls}
                        onClick={() =>
                          downloadMovement(
                            v.id,
                            `${reportType}-v${v.version_no}-changes.xlsx`,
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
                  Showing the {MAX_HISTORY} most recent of {all.length} versions.
                </p>
              )}
            </div>
          )}
        </SheetBody>
      </SheetContent>
    </Sheet>
  );
}
