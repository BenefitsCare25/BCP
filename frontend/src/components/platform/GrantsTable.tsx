import { useEffect, useState } from "react";
import type { AccessGrant } from "@/api/platform";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { fmtDateTime, parseServerDate } from "@/lib/format";

/** The current time, refreshed every half minute — enough for "time left"
 *  on grants that last hours. */
function useNow(intervalMs = 30_000): number {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const id = window.setInterval(() => setNow(Date.now()), intervalMs);
    return () => window.clearInterval(id);
  }, [intervalMs]);
  return now;
}

function formatRemaining(ms: number): string {
  const minutes = Math.ceil(ms / 60_000);
  if (minutes <= 1) return "under a minute left";
  const hours = Math.floor(minutes / 60);
  const rest = minutes % 60;
  if (hours === 0) return `${rest} min left`;
  return rest === 0 ? `${hours} h left` : `${hours} h ${rest} min left`;
}

export function scopeLabel(scope: AccessGrant["scope"]): string {
  return scope === "write" ? "Read and write" : "Read only";
}

function GrantEnd({ grant, now }: { grant: AccessGrant; now: number }) {
  if (grant.revoked_at) {
    return (
      <span className="text-muted-foreground">Revoked {fmtDateTime(grant.revoked_at)}</span>
    );
  }
  const remaining = parseServerDate(grant.expires_at).getTime() - now;
  if (remaining <= 0) {
    return (
      <span className="text-muted-foreground">Expired {fmtDateTime(grant.expires_at)}</span>
    );
  }
  return (
    <span className="flex flex-col gap-0.5">
      <span className="font-medium text-foreground">{formatRemaining(remaining)}</span>
      <span className="text-xs text-muted-foreground">until {fmtDateTime(grant.expires_at)}</span>
    </span>
  );
}

/** Platform access to a broker's data: who, which firm, why, at what scope
 *  and until when. Read-only unless `onRevoke` is given; only grants that are
 *  still running offer Revoke. */
export function GrantsTable({
  grants,
  showFirm = true,
  onRevoke,
  revokingId = null,
  caption,
}: {
  grants: AccessGrant[];
  showFirm?: boolean;
  onRevoke?: (grant: AccessGrant) => void;
  revokingId?: string | null;
  caption: string;
}) {
  const now = useNow();
  return (
    <Table>
      <caption className="sr-only">{caption}</caption>
      <TableHeader>
        <TableRow>
          {showFirm && <TableHead>Firm</TableHead>}
          <TableHead>Who</TableHead>
          <TableHead>Access</TableHead>
          <TableHead>Reason</TableHead>
          <TableHead>Started</TableHead>
          <TableHead>Ends</TableHead>
          {onRevoke && <TableHead><span className="sr-only">Actions</span></TableHead>}
        </TableRow>
      </TableHeader>
      <TableBody>
        {grants.map((grant) => {
          const running =
            !grant.revoked_at && parseServerDate(grant.expires_at).getTime() > now;
          return (
            <TableRow key={grant.id}>
              {showFirm && <TableCell className="font-medium">{grant.firm_name}</TableCell>}
              <TableCell className="break-all">{grant.user_email ?? "Unknown user"}</TableCell>
              <TableCell>
                <Badge variant={grant.scope === "write" ? "warn" : "default"}>
                  {scopeLabel(grant.scope)}
                </Badge>
              </TableCell>
              <TableCell className="max-w-sm whitespace-pre-wrap break-words">
                {grant.reason}
              </TableCell>
              <TableCell className="whitespace-nowrap">{fmtDateTime(grant.created_at)}</TableCell>
              <TableCell className="whitespace-nowrap">
                <GrantEnd grant={grant} now={now} />
              </TableCell>
              {onRevoke && (
                <TableCell className="text-right">
                  {running && (
                    <Button
                      size="sm"
                      variant="outline"
                      loading={revokingId === grant.id}
                      disabled={revokingId !== null}
                      aria-label={`Revoke access to ${grant.firm_name} for ${grant.user_email ?? "this user"}`}
                      onClick={() => onRevoke(grant)}
                    >
                      Revoke
                    </Button>
                  )}
                </TableCell>
              )}
            </TableRow>
          );
        })}
      </TableBody>
    </Table>
  );
}
