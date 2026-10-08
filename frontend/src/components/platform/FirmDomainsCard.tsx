import { useState } from "react";
import { Star, Trash2 } from "lucide-react";
import { toast } from "sonner";
import {
  type PlatformFirm,
  type TenantDomain,
  useAddPlatformDomain,
  useDeletePlatformDomain,
  usePatchPlatformDomain,
  usePlatformFirmDomains,
} from "@/api/platform";
import { AlertDialog } from "@/components/ui/alert-dialog";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { formatError } from "@/lib/errors";
import { DomainRequestForm } from "./DomainRequestForm";
import { DomainSetupGuide } from "./DomainSetupGuide";
import { DomainsTable } from "./DomainsTable";
import { ListError, ListLoading } from "./QueryStates";

type Pending =
  | { kind: "activate" | "disable" | "delete"; domain: TenantDomain }
  | null;

const CONFIRM_COPY = {
  activate: {
    title: (h: string) => `Activate ${h}?`,
    description:
      "Only activate once the broker's TXT validation record and CNAME are in place. The address starts serving the firm's sites straight away.",
    confirm: "Activate address",
  },
  disable: {
    title: (h: string) => `Disable ${h}?`,
    description:
      "People using this address can no longer reach the firm's sites through it, and links already emailed with it stop working. It can be activated again later.",
    confirm: "Disable address",
  },
  delete: {
    title: (h: string) => `Delete ${h}?`,
    description:
      "The address is removed from this firm. Adding it again later needs the DNS validation again.",
    confirm: "Delete address",
  },
} as const;

/** The platform's management of one firm's web addresses: add, activate,
 *  disable, make primary and delete. State changes that affect live traffic
 *  are confirmed. */
export function FirmDomainsCard({ firm }: { firm: PlatformFirm }) {
  const domains = usePlatformFirmDomains(firm.id);
  const add = useAddPlatformDomain(firm.id);
  const patch = usePatchPlatformDomain(firm.id);
  const remove = useDeletePlatformDomain(firm.id);
  const [pending, setPending] = useState<Pending>(null);
  const busy = patch.isPending || remove.isPending;

  const makePrimary = async (domain: TenantDomain) => {
    try {
      await patch.mutateAsync({ id: domain.id, is_primary: true });
      toast.success(`${domain.hostname} is now the primary address`);
    } catch (e) {
      toast.error(formatError(e));
    }
  };

  const confirmPending = async () => {
    if (!pending) return;
    const { kind, domain } = pending;
    try {
      if (kind === "delete") {
        await remove.mutateAsync(domain.id);
        toast.success(`${domain.hostname} deleted`);
      } else {
        await patch.mutateAsync({
          id: domain.id,
          status: kind === "activate" ? "active" : "disabled",
        });
        toast.success(`${domain.hostname} ${kind === "activate" ? "activated" : "disabled"}`);
      }
      setPending(null);
    } catch (e) {
      toast.error(formatError(e));
    }
  };

  const copy = pending ? CONFIRM_COPY[pending.kind] : null;

  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-sm">Web addresses</CardTitle>
        <CardDescription>
          The broker&apos;s own addresses. Invitations and sign-in links use the
          primary active address; without an active one, nothing that emails a
          link can be sent.
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-5">
        {domains.isPending ? (
          <ListLoading label="Loading web addresses…" />
        ) : domains.isError ? (
          <ListError what="web addresses" error={domains.error} onRetry={() => void domains.refetch()} />
        ) : domains.data.length === 0 ? (
          <p className="text-sm text-muted-foreground">No web addresses yet.</p>
        ) : (
          <div className="rounded-md border border-border">
            <DomainsTable
              domains={domains.data}
              caption={`${firm.name} web addresses`}
              actions={(domain) => (
                <div className="flex flex-wrap justify-end gap-1">
                  {domain.status === "active" ? (
                    <Button size="sm" variant="outline" disabled={busy}
                      onClick={() => setPending({ kind: "disable", domain })}>
                      Disable
                    </Button>
                  ) : (
                    <Button size="sm" variant="outline" disabled={busy}
                      onClick={() => setPending({ kind: "activate", domain })}>
                      Activate
                    </Button>
                  )}
                  {!domain.is_primary && (
                    <Button size="sm" variant="ghost" disabled={busy}
                      onClick={() => void makePrimary(domain)}>
                      <Star className="size-3.5" aria-hidden="true" /> Make primary
                    </Button>
                  )}
                  <Button size="sm" variant="ghost" className="text-error hover:text-error"
                    disabled={busy} aria-label={`Delete ${domain.hostname}`}
                    onClick={() => setPending({ kind: "delete", domain })}>
                    <Trash2 className="size-3.5" aria-hidden="true" />
                  </Button>
                </div>
              )}
            />
          </div>
        )}
        <section aria-labelledby={`platform-domain-add-${firm.id}`} className="space-y-3">
          <h4 id={`platform-domain-add-${firm.id}`} className="text-sm font-medium text-foreground">
            Add an address
          </h4>
          <DomainSetupGuide audience="platform" />
          <DomainRequestForm
            idPrefix={`platform-domain-${firm.id}`}
            submitLabel="Add address"
            pending={add.isPending}
            onSubmit={async (hostname, surface) => {
              const created = await add.mutateAsync({ hostname, surface });
              toast.success(`${created.hostname} added as pending`);
            }}
          />
        </section>
      </CardContent>
      <AlertDialog
        open={pending !== null}
        onOpenChange={(open) => !open && !busy && setPending(null)}
        tone={pending?.kind === "activate" ? "info" : "danger"}
        confirmVariant={pending?.kind === "activate" ? "default" : "destructive"}
        title={copy && pending ? copy.title(pending.domain.hostname) : ""}
        description={copy?.description ?? ""}
        confirmLabel={copy?.confirm ?? "Confirm"}
        loading={busy}
        onConfirm={() => void confirmPending()}
      />
    </Card>
  );
}
