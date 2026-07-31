import {
  History,
  RotateCcw,
  ArrowUpRight,
  ArrowDownRight,
  CheckCircle2,
  PencilLine,
} from "lucide-react";
import { useCoverageHistory, type CoverageHistoryEntry } from "@/api/enrollment";

/**
 * Per-employee coverage change timeline (the 'track' view). Reads the audit
 * trail (override edits, enrollment actions, reverts) and renders newest-first.
 * Shared by the employee detail sheet and the enrollment elections panel.
 */
function actionIcon(action: string) {
  if (action.startsWith("revert") || action === "delete_plan_override" || action === "reset_enrollment")
    return <RotateCcw className="size-3.5 text-muted-foreground" />;
  if (action === "confirm_enrollment" || action === "submit_enrollment")
    return <CheckCircle2 className="size-3.5 text-good" />;
  return <PencilLine className="size-3.5 text-muted-foreground" />;
}

function planChange(e: CoverageHistoryEntry) {
  const from = e.from_plan ?? null;
  const to = e.declined ? "Declined" : e.to_plan ?? null;
  if (from == null && to == null) return null;
  const richer = from != null && to != null && to !== from;
  return (
    <span className="inline-flex items-center gap-1 text-xs">
      <span className="text-muted-foreground">{from ?? "—"}</span>
      {richer ? (
        <ArrowUpRight className="size-3 text-muted-foreground" />
      ) : (
        <ArrowDownRight className="size-3 text-muted-foreground" />
      )}
      <span className="font-medium text-foreground">{to ?? "—"}</span>
    </span>
  );
}

function whenLabel(iso: string): string {
  if (!iso) return "";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "";
  return d.toLocaleString(undefined, {
    year: "numeric",
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}

export function CoverageHistory({
  employeeId,
  limit,
}: {
  employeeId: string | null;
  /** Cap rendered rows (e.g. a compact strip in the elections panel). */
  limit?: number;
}) {
  const { data, isLoading, isError } = useCoverageHistory(employeeId);
  const entries = data?.entries ?? [];
  const shown = limit ? entries.slice(0, limit) : entries;

  return (
    <div>
      <div className="mb-2 flex items-center gap-2 text-2xs uppercase tracking-wider text-muted-foreground">
        <History className="size-3.5" />
        Coverage history
      </div>
      {isLoading ? (
        <p className="text-xs text-muted-foreground">Loading…</p>
      ) : isError ? (
        <p className="text-xs text-muted-foreground">Couldn't load history.</p>
      ) : shown.length === 0 ? (
        <p className="text-xs text-muted-foreground">No changes recorded yet.</p>
      ) : (
        <ol className="space-y-2">
          {shown.map((e) => (
            <li key={e.id} className="flex items-start gap-2">
              <span className="mt-0.5 shrink-0">{actionIcon(e.action)}</span>
              <div className="min-w-0 flex-1">
                <div className="flex flex-wrap items-center gap-x-2 gap-y-0.5">
                  <span className="text-sm text-foreground">{e.label}</span>
                  {e.product_code && (
                    <span className="rounded border border-border bg-muted/40 px-1.5 py-0.5 text-2xs text-muted-foreground">
                      {e.product_code}
                    </span>
                  )}
                  {planChange(e)}
                </div>
                <div className="text-2xs text-muted-foreground">
                  {whenLabel(e.at)}
                  {e.actor ? ` · ${e.actor}` : ""}
                </div>
              </div>
            </li>
          ))}
        </ol>
      )}
      {limit != null && entries.length > limit && (
        <p className="mt-1.5 text-2xs text-muted-foreground">
          Showing {limit} of {entries.length} changes.
        </p>
      )}
    </div>
  );
}
