import { useId, useState, type FormEvent } from "react";
import { toast } from "sonner";
import {
  type GrantScope,
  type PlatformFirm,
  useCreateAccessGrant,
} from "@/api/platform";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Label } from "@/components/ui/label";
import { NativeSelect } from "@/components/ui/native-select";
import { SectionLabel } from "@/components/ui/section-label";
import { Segmented } from "@/components/ui/segmented";
import { formatError } from "@/lib/errors";
import { fmtDateTime } from "@/lib/format";
import { cn } from "@/lib/cn";

export const REASON_MIN = 15;
const HOURS = [1, 2, 3, 4, 5, 6, 7, 8];
const SCOPES: { value: GrantScope; label: string }[] = [
  { value: "read", label: "Read only" },
  { value: "write", label: "Read and write" },
];

/** Start time-limited access to another broker's data. The reason is shown to
 *  that firm's admins and kept in both audit trails, so it must say why. The
 *  platform owner firm needs no grant and is not offered. */
export function StartAccessCard({ firms }: { firms: PlatformFirm[] }) {
  const create = useCreateAccessGrant();
  const id = useId();
  const eligible = firms.filter((f) => !f.is_platform_owner);
  const [firmId, setFirmId] = useState("");
  const [reason, setReason] = useState("");
  const [scope, setScope] = useState<GrantScope>("read");
  const [hours, setHours] = useState(1);
  const [error, setError] = useState<string | null>(null);

  const reasonLength = reason.trim().length;
  const reasonShort = reasonLength < REASON_MIN;

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    if (!firmId) {
      setError("Choose the broker firm to open.");
      return;
    }
    if (reasonShort) {
      setError(`Give a reason of at least ${REASON_MIN} characters.`);
      return;
    }
    setError(null);
    try {
      const grant = await create.mutateAsync({
        broker_firm_id: firmId,
        reason: reason.trim(),
        scope,
        hours,
      });
      toast.success(
        `${scope === "write" ? "Read and write" : "Read-only"} access to ${grant.firm_name} started until ${fmtDateTime(grant.expires_at)}. Its companies are now in your company list.`,
      );
      setFirmId("");
      setReason("");
      setScope("read");
      setHours(1);
    } catch (e) {
      setError(formatError(e));
    }
  };

  const errorId = `${id}-error`;
  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-sm">Start access</CardTitle>
        <CardDescription>
          Your standing access covers the platform owner firm only. Access to
          any other broker lasts at most eight hours and is recorded in the
          platform audit trail and that firm&apos;s own log.
        </CardDescription>
      </CardHeader>
      <CardContent>
        {eligible.length === 0 ? (
          <p className="text-sm text-muted-foreground">
            There is no other broker firm on the platform yet.
          </p>
        ) : (
          <form onSubmit={(event) => void submit(event)} className="space-y-4">
            <div className="grid gap-4 md:grid-cols-[minmax(0,1fr)_auto_auto]">
              <div className="flex flex-col gap-1.5">
                <Label htmlFor={`${id}-firm`}>Broker firm</Label>
                <NativeSelect
                  id={`${id}-firm`}
                  className="h-9"
                  value={firmId}
                  onChange={(e) => {
                    setFirmId(e.target.value);
                    setError(null);
                  }}
                >
                  <option value="">Choose a firm</option>
                  {eligible.map((f) => (
                    <option key={f.id} value={f.id}>
                      {f.name}
                      {f.status === "suspended" ? " (suspended)" : ""}
                    </option>
                  ))}
                </NativeSelect>
              </div>
              <div className="flex flex-col gap-1.5">
                <SectionLabel as="span" id={`${id}-scope`}>
                  Access
                </SectionLabel>
                <div role="group" aria-labelledby={`${id}-scope`}>
                  <Segmented value={scope} onChange={setScope} options={SCOPES} className="h-9 text-sm" />
                </div>
              </div>
              <div className="flex flex-col gap-1.5">
                <Label htmlFor={`${id}-hours`}>Duration</Label>
                <NativeSelect
                  id={`${id}-hours`}
                  className="h-9"
                  value={String(hours)}
                  onChange={(e) => setHours(Number(e.target.value))}
                >
                  {HOURS.map((h) => (
                    <option key={h} value={h}>
                      {h} {h === 1 ? "hour" : "hours"}
                    </option>
                  ))}
                </NativeSelect>
              </div>
            </div>
            <div className="flex flex-col gap-1.5">
              <Label htmlFor={`${id}-reason`}>Reason</Label>
              <textarea
                id={`${id}-reason`}
                value={reason}
                rows={3}
                maxLength={1000}
                aria-describedby={`${id}-reason-count${error ? ` ${errorId}` : ""}`}
                onChange={(e) => {
                  setReason(e.target.value);
                  setError(null);
                }}
                placeholder="For example: support ticket 4821, claim upload failing for the broker's admin"
                className="w-full min-h-20 rounded-md border border-input bg-card px-3 py-2 text-sm text-foreground shadow-sm placeholder:text-muted-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring/40"
              />
              <p
                id={`${id}-reason-count`}
                className={cn("text-xs", reasonShort ? "text-muted-foreground" : "text-good")}
              >
                {reasonShort
                  ? `${reasonLength} of at least ${REASON_MIN} characters. The firm's admins see this reason.`
                  : "The firm's admins see this reason."}
              </p>
            </div>
            {scope === "write" && (
              <p className="rounded-md bg-warn-soft px-3 py-2 text-sm text-warn">
                Write access lets you change and remove this broker&apos;s data.
                Choose it only when the task needs a change.
              </p>
            )}
            {error && (
              <p id={errorId} role="alert" className="text-sm text-error">
                {error}
              </p>
            )}
            <Button type="submit" loading={create.isPending}>
              Start access
            </Button>
          </form>
        )}
      </CardContent>
    </Card>
  );
}
