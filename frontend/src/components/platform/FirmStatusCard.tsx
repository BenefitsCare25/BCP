import { useId, useState } from "react";
import { toast } from "sonner";
import { type PlatformFirm, usePatchPlatformFirm } from "@/api/platform";
import { AlertDialog } from "@/components/ui/alert-dialog";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Label } from "@/components/ui/label";
import { Separator } from "@/components/ui/separator";
import { Switch } from "@/components/ui/switch";
import { formatError } from "@/lib/errors";

/** A firm's status (suspend / reactivate) and the attribution entitlement.
 *  Suspending signs every user of the firm out and closes its sites, so it is
 *  confirmed; the platform owner firm cannot be suspended. */
export function FirmStatusCard({ firm }: { firm: PlatformFirm }) {
  const patch = usePatchPlatformFirm();
  const id = useId();
  const [confirm, setConfirm] = useState<"suspend" | "reactivate" | null>(null);
  const suspended = firm.status === "suspended";

  const apply = async (
    body: { status?: PlatformFirm["status"]; allow_hide_attribution?: boolean },
    success: string,
  ) => {
    try {
      await patch.mutateAsync({ id: firm.id, ...body });
      toast.success(success);
      setConfirm(null);
    } catch (e) {
      toast.error(formatError(e));
    }
  };

  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-sm">Status and entitlements</CardTitle>
      </CardHeader>
      <CardContent className="space-y-4">
        <div className="flex flex-wrap items-start justify-between gap-4">
          <div className="space-y-1">
            <div className="flex items-center gap-2">
              <span className="text-sm font-medium text-foreground">Status</span>
              <Badge variant={suspended ? "error" : "good"}>
                {suspended ? "Suspended" : "Active"}
              </Badge>
            </div>
            <p className="max-w-xl text-xs text-muted-foreground">
              {firm.is_platform_owner
                ? "The platform owner firm cannot be suspended."
                : suspended
                  ? "No one can sign in to this firm's staff site, HR portal or employee portal. Its data is kept."
                  : "This firm's staff site, HR portal and employee portal are open."}
            </p>
          </div>
          {!firm.is_platform_owner &&
            (suspended ? (
              <Button variant="outline" onClick={() => setConfirm("reactivate")} disabled={patch.isPending}>
                Reactivate firm
              </Button>
            ) : (
              <Button variant="destructiveOutline" onClick={() => setConfirm("suspend")} disabled={patch.isPending}>
                Suspend firm
              </Button>
            ))}
        </div>
        <Separator />
        <div className="flex items-start justify-between gap-4">
          <div>
            <Label htmlFor={`${id}-attribution`} className="text-sm">
              May hide &ldquo;Powered by Inspro&rdquo;
            </Label>
            <p id={`${id}-attribution-help`} className="max-w-xl text-xs text-muted-foreground">
              Lets this firm&apos;s admins turn off the platform attribution on
              their branded sites. Off, the attribution always shows.
            </p>
          </div>
          <Switch
            id={`${id}-attribution`}
            aria-describedby={`${id}-attribution-help`}
            checked={firm.allow_hide_attribution}
            disabled={patch.isPending}
            onCheckedChange={(on) =>
              void apply(
                { allow_hide_attribution: on },
                on
                  ? `${firm.name} may now hide the platform attribution`
                  : `${firm.name} always shows the platform attribution`,
              )
            }
          />
        </div>
      </CardContent>
      <AlertDialog
        open={confirm === "suspend"}
        onOpenChange={(open) => !open && !patch.isPending && setConfirm(null)}
        title={`Suspend ${firm.name}?`}
        description={`Everyone at ${firm.name} is signed out at once, and no one can sign in to its staff site, HR portal or employee portal until it is reactivated. Its companies, members and settings are kept.`}
        confirmLabel="Suspend firm"
        loading={patch.isPending}
        onConfirm={() =>
          void apply({ status: "suspended" }, `${firm.name} suspended. Its users have been signed out.`)
        }
      />
      <AlertDialog
        open={confirm === "reactivate"}
        onOpenChange={(open) => !open && !patch.isPending && setConfirm(null)}
        tone="info"
        confirmVariant="default"
        title={`Reactivate ${firm.name}?`}
        description="Its sites open again and its users can sign in with their existing accounts."
        confirmLabel="Reactivate firm"
        loading={patch.isPending}
        onConfirm={() => void apply({ status: "active" }, `${firm.name} reactivated`)}
      />
    </Card>
  );
}
