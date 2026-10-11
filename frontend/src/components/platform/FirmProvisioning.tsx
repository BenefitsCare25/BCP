import type { ReactNode } from "react";
import { Clock } from "lucide-react";
import type { PlatformFirm } from "@/api/platform";

/** Shown while a new firm waits for the migration job to create its database
 *  schema (runtime provisioning off). The firm list is polled meanwhile
 *  (`usePlatformFirms`), so this clears by itself once the job has run. */
export function FirmProvisioningNotice({ firm, id }: { firm: PlatformFirm; id?: string }) {
  if (firm.provisioned) return null;
  return (
    <div
      id={id}
      role="status"
      className="flex items-start gap-3 rounded-md border border-border bg-warn-soft p-3 text-sm text-warn"
    >
      <Clock className="mt-0.5 size-4 shrink-0" aria-hidden="true" />
      <div className="space-y-1">
        <p className="font-medium">
          Waiting for the migration job to create this firm&apos;s database schema
        </p>
        <p className="text-foreground/80">
          Inviting {firm.name}&apos;s staff, starting access to it and changing
          its sign-in methods become available once the job has run. This
          updates by itself.
        </p>
      </div>
    </div>
  );
}

/** Disables every control inside until the firm's schema exists: the server
 *  refuses those actions with 409 `firm_not_provisioned` until then. */
export function UntilProvisioned({
  firm,
  describedBy,
  children,
}: {
  firm: PlatformFirm;
  /** The id of the notice that says why the controls are off. */
  describedBy?: string;
  children: ReactNode;
}) {
  const pending = !firm.provisioned;
  return (
    <fieldset
      disabled={pending}
      aria-describedby={pending ? describedBy : undefined}
      className="min-w-0"
    >
      {children}
    </fieldset>
  );
}
