import { useState } from "react";
import { Link } from "@tanstack/react-router";
import { Plus } from "lucide-react";
import { usePlatformFirms } from "@/api/platform";
import { Badge } from "@/components/ui/badge";
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
import { NewFirmSheet } from "@/components/platform/NewFirmSheet";
import { PlatformPageHeader } from "@/components/platform/PlatformPageHeader";
import { ListError, ListLoading } from "@/components/platform/QueryStates";
import { fmtDateTime } from "@/lib/format";

export function PlatformFirmsPage() {
  const firms = usePlatformFirms();
  const [creating, setCreating] = useState(false);
  // A fresh sheet per opening, so a finished creation never reappears and
  // closing does not reset the step mid-animation.
  const [sheetKey, setSheetKey] = useState(0);

  return (
    <>
      <PlatformPageHeader
        title="Broker firms"
        description="Every broker on the platform. Open a firm to change its status and entitlements, manage its web addresses or invite its firm admin."
        actions={
          <Button
            onClick={() => {
              setSheetKey((k) => k + 1);
              setCreating(true);
            }}
          >
            <Plus className="size-4" /> New broker firm
          </Button>
        }
      />
      <Card className="overflow-hidden">
        {firms.isPending ? (
          <div className="p-5">
            <ListLoading label="Loading broker firms…" />
          </div>
        ) : firms.isError ? (
          <div className="p-5">
            <ListError what="broker firms" error={firms.error} onRetry={() => void firms.refetch()} />
          </div>
        ) : firms.data.length === 0 ? (
          <p className="p-5 text-sm text-muted-foreground">No broker firms yet.</p>
        ) : (
          <Table>
            <caption className="sr-only">Broker firms on the platform</caption>
            <TableHeader>
              <TableRow>
                <TableHead>Firm</TableHead>
                <TableHead>Alias</TableHead>
                <TableHead>Status</TableHead>
                <TableHead className="text-right">Companies</TableHead>
                <TableHead className="text-right">Web addresses</TableHead>
                <TableHead>Created</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {firms.data.map((firm) => (
                <TableRow key={firm.id}>
                  <TableCell>
                    <div className="flex flex-wrap items-center gap-2">
                      <Link
                        to="/platform/firms/$firmId"
                        params={{ firmId: firm.id }}
                        className="font-medium text-primary underline-offset-4 hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring/50"
                      >
                        {firm.name}
                      </Link>
                      {firm.is_platform_owner && <Badge variant="info">Platform owner</Badge>}
                    </div>
                  </TableCell>
                  <TableCell className="text-muted-foreground">{firm.slug}</TableCell>
                  <TableCell>
                    <Badge variant={firm.status === "active" ? "good" : "error"}>
                      {firm.status === "active" ? "Active" : "Suspended"}
                    </Badge>
                  </TableCell>
                  <TableCell className="text-right tabular-nums">{firm.client_count}</TableCell>
                  <TableCell className="text-right tabular-nums">{firm.domain_count}</TableCell>
                  <TableCell className="whitespace-nowrap text-muted-foreground">
                    {fmtDateTime(firm.created_at)}
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        )}
      </Card>
      <NewFirmSheet key={sheetKey} open={creating} onOpenChange={setCreating} />
    </>
  );
}
