import { useMemo } from "react";
import { usePlatformAudit, usePlatformFirms } from "@/api/platform";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { PlatformPageHeader } from "@/components/platform/PlatformPageHeader";
import { ListError, ListLoading } from "@/components/platform/QueryStates";
import { fmtDateTime } from "@/lib/format";

/** "access_grant.revoke" → "Access grant revoke". */
function humanise(code: string): string {
  const words = code.replace(/[._]+/g, " ").trim();
  return words ? words.charAt(0).toUpperCase() + words.slice(1) : code;
}

const DETAIL_LIMIT = 160;

function detailText(detail: unknown): string {
  if (detail === null || detail === undefined || detail === "") return "";
  if (typeof detail === "string") return detail;
  if (typeof detail === "object" && !Array.isArray(detail)) {
    return Object.entries(detail as Record<string, unknown>)
      .map(([key, value]) => {
        const shown =
          value !== null && typeof value === "object" ? JSON.stringify(value) : String(value);
        return `${humanise(key)}: ${shown}`;
      })
      .join(" · ");
  }
  return JSON.stringify(detail);
}

export function PlatformAuditPage() {
  const audit = usePlatformAudit();
  const firms = usePlatformFirms();
  const firmNames = useMemo(
    () => new Map((firms.data ?? []).map((f) => [f.id, f.name])),
    [firms.data],
  );
  const entries = audit.data?.pages.flat() ?? [];

  return (
    <>
      <PlatformPageHeader
        title="Audit trail"
        description="Everything done in the platform console and under access grants, newest first. Entries cannot be edited or removed."
      />
      <Card className="overflow-hidden">
        {audit.isPending ? (
          <div className="p-5">
            <ListLoading label="Loading the audit trail…" />
          </div>
        ) : entries.length === 0 && audit.isError ? (
          <div className="p-5">
            <ListError what="the audit trail" error={audit.error} onRetry={() => void audit.refetch()} />
          </div>
        ) : entries.length === 0 ? (
          <p className="p-5 text-sm text-muted-foreground">Nothing has been recorded yet.</p>
        ) : (
          <Table>
            <caption className="sr-only">Platform audit trail</caption>
            <TableHeader>
              <TableRow>
                <TableHead>When</TableHead>
                <TableHead>Who</TableHead>
                <TableHead>Action</TableHead>
                <TableHead>Firm</TableHead>
                <TableHead>Detail</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {entries.map((entry) => {
                const text = detailText(entry.detail);
                return (
                  <TableRow key={entry.id}>
                    <TableCell className="whitespace-nowrap">{fmtDateTime(entry.occurred_at)}</TableCell>
                    <TableCell className="break-all">{entry.actor_email ?? "System"}</TableCell>
                    <TableCell>
                      <span className="font-medium text-foreground">{humanise(entry.action)}</span>
                      {entry.entity_type && (
                        <span className="block text-xs text-muted-foreground">
                          {humanise(entry.entity_type)}
                        </span>
                      )}
                    </TableCell>
                    <TableCell>
                      {entry.broker_firm_id
                        ? (firmNames.get(entry.broker_firm_id) ?? "Unknown firm")
                        : "Platform"}
                    </TableCell>
                    <TableCell className="max-w-md break-words text-muted-foreground" title={text || undefined}>
                      {text
                        ? text.length > DETAIL_LIMIT
                          ? `${text.slice(0, DETAIL_LIMIT)}…`
                          : text
                        : "—"}
                    </TableCell>
                  </TableRow>
                );
              })}
            </TableBody>
          </Table>
        )}
      </Card>
      {audit.hasNextPage && (
        <div className="flex justify-center">
          <Button
            variant="outline"
            loading={audit.isFetchingNextPage}
            onClick={() => void audit.fetchNextPage()}
          >
            Load older entries
          </Button>
        </div>
      )}
      {audit.isFetchNextPageError && (
        <p role="alert" className="text-center text-sm text-error">
          Could not load older entries. Try again.
        </p>
      )}
    </>
  );
}
