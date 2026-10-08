import type { ReactNode } from "react";
import type { TenantDomain } from "@/api/platform";
import { Badge } from "@/components/ui/badge";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { fmtDateTime } from "@/lib/format";
import { DOMAIN_STATUS, surfaceLabel } from "./domainMeta";

/** A firm's web addresses. `actions` adds a trailing cell per row (the
 *  platform console's activate/disable/primary/delete controls). */
export function DomainsTable({
  domains,
  caption,
  actions,
}: {
  domains: TenantDomain[];
  caption: string;
  actions?: (domain: TenantDomain) => ReactNode;
}) {
  return (
    <Table>
      <caption className="sr-only">{caption}</caption>
      <TableHeader>
        <TableRow>
          <TableHead>Address</TableHead>
          <TableHead>Serves</TableHead>
          <TableHead>Status</TableHead>
          <TableHead>Validated</TableHead>
          {actions && <TableHead><span className="sr-only">Actions</span></TableHead>}
        </TableRow>
      </TableHeader>
      <TableBody>
        {domains.map((domain) => {
          const status = DOMAIN_STATUS[domain.status];
          return (
            <TableRow key={domain.id}>
              <TableCell>
                <div className="flex flex-wrap items-center gap-2">
                  <span className="break-all font-medium">{domain.hostname}</span>
                  {domain.is_primary && <Badge variant="info">Primary</Badge>}
                </div>
              </TableCell>
              <TableCell>{surfaceLabel(domain.surface)}</TableCell>
              <TableCell>
                <Badge variant={status?.variant ?? "default"}>
                  {status?.label ?? domain.status}
                </Badge>
              </TableCell>
              <TableCell className="whitespace-nowrap text-muted-foreground">
                {domain.verified_at ? fmtDateTime(domain.verified_at) : "Not yet"}
              </TableCell>
              {actions && <TableCell className="text-right">{actions(domain)}</TableCell>}
            </TableRow>
          );
        })}
      </TableBody>
    </Table>
  );
}
