import { useState } from "react";
import { toast } from "sonner";
import { type PlatformFirm, useClearFirmSender, useFirmSender, useVerifyFirmSender } from "@/api/platform";
import { AlertDialog } from "@/components/ui/alert-dialog";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { ListError, ListLoading } from "@/components/platform/QueryStates";
import { formatError } from "@/lib/errors";
import { fmtDateTime } from "@/lib/format";

/** Records that a firm's email From address passed SPF/DKIM at its domain, so
 *  system emails go from it; clearing reverts them to the platform sender.
 *  The firm sets the address in its brand settings; changing it there clears
 *  the verification. */
export function FirmSenderCard({ firm }: { firm: PlatformFirm }) {
  const sender = useFirmSender(firm.id);
  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-sm">Email sender</CardTitle>
        <CardDescription>
          Verify {firm.name}&apos;s From address once SPF and DKIM for it are set up at their domain. Until then,
          their system emails come from the platform&apos;s address with their sender name.
        </CardDescription>
      </CardHeader>
      <CardContent>
        {sender.isPending ? (
          <ListLoading label="Loading email sender…" />
        ) : sender.isError ? (
          <ListError what="the email sender" error={sender.error} onRetry={() => void sender.refetch()} />
        ) : sender.data.address ? (
          <SenderControls firm={firm} address={sender.data.address} verifiedAt={sender.data.verified_at} />
        ) : (
          <p className="text-sm text-muted-foreground">{firm.name} has not set a From address.</p>
        )}
      </CardContent>
    </Card>
  );
}

function SenderControls({
  firm,
  address,
  verifiedAt,
}: {
  firm: PlatformFirm;
  address: string;
  verifiedAt: string | null;
}) {
  const verify = useVerifyFirmSender(firm.id);
  const clear = useClearFirmSender(firm.id);
  const [confirm, setConfirm] = useState<"verify" | "clear" | null>(null);
  const pending = verify.isPending || clear.isPending;
  const error = verify.error ?? clear.error;
  const done = () => setConfirm(null);

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center gap-2 text-sm">
        <span className="font-medium text-foreground">{address}</span>
        {verifiedAt ? (
          <Badge variant="good">Verified {fmtDateTime(verifiedAt)}</Badge>
        ) : (
          <Badge variant="warn">Waiting for verification</Badge>
        )}
      </div>
      {error && (
        <p role="alert" className="text-sm text-error">
          {formatError(error)}
        </p>
      )}
      <div className="flex flex-wrap gap-2">
        {verifiedAt ? (
          <Button type="button" variant="outline" disabled={pending} onClick={() => setConfirm("clear")}>
            Clear verification
          </Button>
        ) : (
          <Button type="button" disabled={pending} onClick={() => setConfirm("verify")}>
            Verify address
          </Button>
        )}
      </div>
      <AlertDialog
        open={confirm === "verify"}
        onOpenChange={(open) => !open && !pending && setConfirm(null)}
        tone="info"
        confirmVariant="default"
        title={`Verify ${address}?`}
        description={`Confirm that SPF and DKIM for ${address} are set up at ${firm.name}'s domain. ${firm.name}'s system emails will then be sent from this address; if it is not set up, they may be rejected as spam.`}
        confirmLabel="Verify address"
        loading={verify.isPending}
        onConfirm={() =>
          verify.mutate(address, {
            onSuccess: () => toast.success(`${address} verified for ${firm.name}`),
            onSettled: done,
          })
        }
      />
      <AlertDialog
        open={confirm === "clear"}
        onOpenChange={(open) => !open && !pending && setConfirm(null)}
        title={`Clear ${firm.name}'s sender verification?`}
        description={`${firm.name}'s system emails go from the platform's address again until a From address is verified.`}
        confirmLabel="Clear verification"
        loading={clear.isPending}
        onConfirm={() =>
          clear.mutate(undefined, {
            onSuccess: () => toast.success(`${firm.name}'s emails go from the platform sender again`),
            onSettled: done,
          })
        }
      />
    </div>
  );
}
