import { History, RotateCcw } from "lucide-react";
import { useAuditLog } from "@/api/hooks";
import { Button } from "@/components/ui/button";
import { SectionLabel } from "@/components/ui/section-label";
import type { AuditLogEntry } from "@/types";

function versionSnapshot(entry: AuditLogEntry): Record<string, unknown> | null {
  return entry.action.endsWith(".deleted") ? entry.before : entry.after;
}

function actionLabel(action: string): string {
  if (action.endsWith(".created")) return "Created";
  if (action.endsWith(".deleted")) return "Reverted";
  if (action.endsWith(".duplicated")) return "Duplicated";
  if (action.endsWith(".imported")) return "Imported";
  return "Updated";
}

export function ConfigurationHistory({
  entityType,
  entityId,
  onRestore,
}: {
  entityType: string;
  entityId: string | null;
  onRestore: (snapshot: Record<string, unknown>) => void;
}) {
  const history = useAuditLog(entityType, entityId, Boolean(entityId));
  if (!entityId) {
    return (
      <p className="text-xs text-subtle">
        Version history starts after this default setup is saved.
      </p>
    );
  }
  if (history.isLoading) {
    return <p className="text-xs text-subtle">Loading version history…</p>;
  }
  if (history.isError) {
    return <p className="text-xs text-error">Version history is unavailable.</p>;
  }
  const entries = history.data?.items ?? [];
  return (
    <section className="space-y-3 rounded-lg border border-border bg-muted/30 p-4">
      <div className="flex items-center gap-2">
        <History className="size-4 text-muted-foreground" aria-hidden />
        <SectionLabel as="h3">Version history</SectionLabel>
      </div>
      {entries.length === 0 ? (
        <p className="text-xs text-subtle">No saved versions yet.</p>
      ) : (
        <ol className="space-y-2">
          {entries.slice(0, 8).map((entry, index) => {
            const snapshot = versionSnapshot(entry);
            return (
              <li
                key={entry.id}
                className="flex flex-wrap items-center gap-x-3 gap-y-1 rounded-md border border-border bg-card px-3 py-2"
              >
                <div className="min-w-0 flex-1">
                  <p className="text-xs font-medium text-foreground">
                    {actionLabel(entry.action)}
                    {index === 0 ? " · Current audit entry" : ""}
                  </p>
                  <p className="text-xs text-subtle">
                    {entry.actor_name ?? "System"} ·{" "}
                    {new Intl.DateTimeFormat(undefined, {
                      dateStyle: "medium",
                      timeStyle: "short",
                    }).format(new Date(entry.created_at))}
                  </p>
                </div>
                {snapshot && (
                  <Button
                    type="button"
                    variant="ghost"
                    size="sm"
                    onClick={() => onRestore(snapshot)}
                  >
                    <RotateCcw className="size-3.5" aria-hidden />
                    <span className="ml-1">Load version</span>
                  </Button>
                )}
              </li>
            );
          })}
        </ol>
      )}
    </section>
  );
}
