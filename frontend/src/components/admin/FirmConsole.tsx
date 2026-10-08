import { useId } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { useMe } from "@/api/hooks";
import {
  useFirmDomains,
  useFirmPlatformAccess,
  useFirmProfile,
  useFirmSignInMethods,
  useRequestFirmDomain,
  useUpdateFirmSignInMethods,
} from "@/api/firm";
import { Badge } from "@/components/ui/badge";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Label } from "@/components/ui/label";
import { Select, SelectContent, SelectTrigger, SelectValue } from "@/components/ui/select";
import { DomainRequestForm } from "@/components/platform/DomainRequestForm";
import { DomainSetupGuide } from "@/components/platform/DomainSetupGuide";
import { DomainsTable } from "@/components/platform/DomainsTable";
import { GrantsTable } from "@/components/platform/GrantsTable";
import { ListError, ListLoading } from "@/components/platform/QueryStates";
import { CompanySelectItems } from "@/components/shell/CompanySelectItems";
import { useAccessGrants } from "@/api/platform";
import { useSession } from "@/stores/session";
import { SignInMethodsCard } from "./SignInMethodsCard";
import { CompanyBrandCard } from "./brand/CompanyBrandCard";
import { FirmBrandCard } from "./brand/FirmBrandCard";

/** The firm-owner sections of Access & Companies: how the firm's staff sign
 *  in, its brand, its web addresses and the log of platform access to its
 *  data. A firm
 *  admin sees their own firm; a platform admin sees the firm of the selected
 *  company, so with no company selected they are asked to choose one first. */
export function FirmConsole({ isSystemAdmin }: { isSystemAdmin: boolean }) {
  const activeClientId = useSession((s) => s.activeClientId);
  if (isSystemAdmin && !activeClientId) return <ChooseCompanyCard />;
  return (
    <>
      <FirmHeading />
      <FirmSignInMethodsCard />
      <BrandCards isSystemAdmin={isSystemAdmin} />
      <WebAddressesCard />
      <PlatformAccessLogCard />
    </>
  );
}

/** Which firm the sections below describe — the platform admin's anchor when
 *  their companies span several firms. */
function FirmHeading() {
  const { data: firm } = useFirmProfile();
  if (!firm) return null;
  return (
    <div className="flex flex-wrap items-center gap-2 pt-2">
      <h2 className="text-base font-semibold text-foreground">{firm.name}</h2>
      <span className="text-xs text-muted-foreground">Alias {firm.slug}</span>
      {firm.is_platform_owner && <Badge variant="info">Platform owner</Badge>}
      {firm.status === "suspended" && <Badge variant="error">Suspended</Badge>}
    </div>
  );
}

function ChooseCompanyCard() {
  const { data: me } = useMe();
  const setActiveClient = useSession((s) => s.setActiveClient);
  const qc = useQueryClient();
  const id = useId();
  const hasCompanies = (me?.accessible_clients.length ?? 0) > 0;
  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-sm">Sign-in methods, brand, web addresses and platform access</CardTitle>
        <CardDescription>
          These belong to a broker firm. Choose one of its companies to see how
          that firm&apos;s staff sign in, its brand, its web addresses and who
          from the platform has accessed its data.
        </CardDescription>
      </CardHeader>
      <CardContent>
        {hasCompanies ? (
          <div className="flex max-w-sm flex-col gap-1.5">
            <Label htmlFor={id}>Company</Label>
            <Select
              onValueChange={(clientId) => {
                setActiveClient(clientId);
                // Same eviction as the company picker: cached reads belong to
                // no company, and must not be refetched under the new one.
                qc.removeQueries();
              }}
            >
              <SelectTrigger id={id}>
                <SelectValue placeholder="Choose a company" />
              </SelectTrigger>
              <SelectContent>
                <CompanySelectItems />
              </SelectContent>
            </Select>
          </div>
        ) : (
          <p className="text-sm text-muted-foreground">
            No company is available yet. Create one above, or start access to a
            broker under Platform → Firm access.
          </p>
        )}
      </CardContent>
    </Card>
  );
}

/** A firm admin edits the brand. A platform admin edits it in the platform
 *  owner's firm or under a write grant, and reads it under a read grant; the
 *  server enforces the same rule. */
function BrandCards({ isSystemAdmin }: { isSystemAdmin: boolean }) {
  const { data: me } = useMe();
  const { data: firm } = useFirmProfile();
  const grants = useAccessGrants(true, isSystemAdmin && me?.platform_console === true);
  const writeGrant = (grants.data ?? []).some(
    (g) => g.broker_firm_id === firm?.id && g.user_id === me?.user_id && g.scope === "write" && !g.revoked_at,
  );
  const readOnly =
    isSystemAdmin && !firm?.is_platform_owner && grants.isSuccess && !writeGrant;
  return (
    <>
      <FirmBrandCard readOnly={readOnly} />
      <CompanyBrandCard readOnly={readOnly} />
    </>
  );
}

function FirmSignInMethodsCard() {
  const methods = useFirmSignInMethods();
  const update = useUpdateFirmSignInMethods();
  return <SignInMethodsCard query={methods} update={update} subject="your firm's staff" />;
}

function WebAddressesCard() {
  const domains = useFirmDomains();
  const request = useRequestFirmDomain();
  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-sm">Web addresses</CardTitle>
        <CardDescription>
          Your own addresses for the staff site and the employee and HR portals.
          Invitations and sign-in links use the primary active address.
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-5">
        {domains.isPending ? (
          <ListLoading label="Loading web addresses…" />
        ) : domains.isError ? (
          <ListError what="web addresses" error={domains.error} onRetry={() => void domains.refetch()} />
        ) : domains.data.length === 0 ? (
          <p className="text-sm text-muted-foreground">
            No web addresses yet. Until one is active, nothing that emails a
            sign-in link can be sent.
          </p>
        ) : (
          <div className="rounded-md border border-border">
            <DomainsTable domains={domains.data} caption="Your firm's web addresses" />
          </div>
        )}
        <section aria-labelledby="firm-domain-request" className="space-y-3">
          <h4 id="firm-domain-request" className="text-sm font-medium text-foreground">
            Request an address
          </h4>
          <DomainSetupGuide audience="firm" />
          <DomainRequestForm
            idPrefix="firm-domain"
            submitLabel="Request address"
            pending={request.isPending}
            onSubmit={async (hostname, surface) => {
              const created = await request.mutateAsync({ hostname, surface });
              toast.success(
                `${created.hostname} requested. It stays pending until the platform activates it.`,
              );
            }}
          />
        </section>
      </CardContent>
    </Card>
  );
}

function PlatformAccessLogCard() {
  const grants = useFirmPlatformAccess();
  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-sm">Platform access</CardTitle>
        <CardDescription>
          Every time someone from the platform opened your firm&apos;s data: who,
          why, at what level and until when. Access is time-limited and also
          recorded in your audit log.
        </CardDescription>
      </CardHeader>
      <CardContent>
        {grants.isPending ? (
          <ListLoading label="Loading platform access…" />
        ) : grants.isError ? (
          <ListError what="platform access" error={grants.error} onRetry={() => void grants.refetch()} />
        ) : grants.data.length === 0 ? (
          <p className="text-sm text-muted-foreground">
            No one from the platform has accessed your firm&apos;s data.
          </p>
        ) : (
          <div className="rounded-md border border-border">
            <GrantsTable grants={grants.data} showFirm={false} caption="Platform access to your firm" />
          </div>
        )}
      </CardContent>
    </Card>
  );
}
