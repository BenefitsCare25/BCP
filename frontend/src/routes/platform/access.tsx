import { useState } from "react";
import { toast } from "sonner";
import {
  type AccessGrant,
  useAccessGrants,
  usePlatformFirms,
  useRevokeAccessGrant,
} from "@/api/platform";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { GrantsTable } from "@/components/platform/GrantsTable";
import { PlatformPageHeader } from "@/components/platform/PlatformPageHeader";
import { ListError, ListLoading } from "@/components/platform/QueryStates";
import { StartAccessCard } from "@/components/platform/StartAccessCard";
import { formatError } from "@/lib/errors";

export function PlatformAccessPage() {
  const firms = usePlatformFirms();
  const active = useAccessGrants(true);
  const history = useAccessGrants(false);
  const revoke = useRevokeAccessGrant();
  const [revokingId, setRevokingId] = useState<string | null>(null);

  const onRevoke = async (grant: AccessGrant) => {
    setRevokingId(grant.id);
    try {
      await revoke.mutateAsync(grant.id);
      toast.success(`Access to ${grant.firm_name} ended`);
    } catch (e) {
      toast.error(formatError(e));
    } finally {
      setRevokingId(null);
    }
  };

  return (
    <>
      <PlatformPageHeader
        title="Firm access"
        description="Open another broker's data for a stated reason and a limited time. Its companies join your company list while access lasts; read-only access refuses every change."
      />
      {firms.isPending ? (
        <ListLoading label="Loading broker firms…" />
      ) : firms.isError ? (
        <ListError what="broker firms" error={firms.error} onRetry={() => void firms.refetch()} />
      ) : (
        <StartAccessCard firms={firms.data} />
      )}

      <Card>
        <CardHeader>
          <CardTitle className="text-sm">Active access</CardTitle>
          <CardDescription>Access ends by itself when its time runs out.</CardDescription>
        </CardHeader>
        <CardContent>
          {active.isPending ? (
            <ListLoading label="Loading active access…" />
          ) : active.isError ? (
            <ListError what="active access" error={active.error} onRetry={() => void active.refetch()} />
          ) : active.data.length === 0 ? (
            <p className="text-sm text-muted-foreground">No access is running.</p>
          ) : (
            <div className="rounded-md border border-border">
              <GrantsTable
                grants={active.data}
                caption="Active access to broker firms"
                onRevoke={(grant) => void onRevoke(grant)}
                revokingId={revokingId}
              />
            </div>
          )}
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle className="text-sm">History</CardTitle>
          <CardDescription>Access that has expired or was revoked.</CardDescription>
        </CardHeader>
        <CardContent>
          {history.isPending ? (
            <ListLoading label="Loading access history…" />
          ) : history.isError ? (
            <ListError what="access history" error={history.error} onRetry={() => void history.refetch()} />
          ) : history.data.length === 0 ? (
            <p className="text-sm text-muted-foreground">No earlier access.</p>
          ) : (
            <div className="rounded-md border border-border">
              <GrantsTable grants={history.data} caption="Ended access to broker firms" />
            </div>
          )}
        </CardContent>
      </Card>
    </>
  );
}
