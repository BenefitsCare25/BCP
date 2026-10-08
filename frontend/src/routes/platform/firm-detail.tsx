import { Link, useParams } from "@tanstack/react-router";
import { ChevronLeft } from "lucide-react";
import {
  type PlatformFirm,
  usePlatformFirmSignInMethods,
  usePlatformFirms,
  useUpdatePlatformFirmSignInMethods,
} from "@/api/platform";
import { InviteUserForm } from "@/components/admin/InviteUserForm";
import { SignInMethodsCard } from "@/components/admin/SignInMethodsCard";
import { Badge } from "@/components/ui/badge";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { FirmActivityCard } from "@/components/platform/FirmActivityCard";
import { FirmDomainsCard } from "@/components/platform/FirmDomainsCard";
import { FirmSenderCard } from "@/components/platform/FirmSenderCard";
import { FirmStatusCard } from "@/components/platform/FirmStatusCard";
import { PlatformPageHeader } from "@/components/platform/PlatformPageHeader";
import { ListError, ListLoading } from "@/components/platform/QueryStates";
import { fmtDateTime } from "@/lib/format";

function BackToFirms() {
  return (
    <Link
      to="/platform/firms"
      className="mb-2 inline-flex items-center gap-1 text-xs text-muted-foreground transition-colors hover:text-foreground"
    >
      <ChevronLeft className="size-3.5" aria-hidden="true" /> All broker firms
    </Link>
  );
}

function FirmSignInMethods({ firm }: { firm: PlatformFirm }) {
  const methods = usePlatformFirmSignInMethods(firm.id);
  const update = useUpdatePlatformFirmSignInMethods(firm.id);
  return <SignInMethodsCard query={methods} update={update} subject={`${firm.name}'s staff`} />;
}

export function PlatformFirmDetailPage() {
  const { firmId } = useParams({ strict: false }) as { firmId: string };
  const firms = usePlatformFirms();

  if (firms.isPending) return <ListLoading label="Loading broker firm…" />;
  if (firms.isError) {
    return <ListError what="this broker firm" error={firms.error} onRetry={() => void firms.refetch()} />;
  }
  const firm = firms.data.find((f) => f.id === firmId);
  if (!firm) {
    return (
      <PlatformPageHeader
        eyebrow={<BackToFirms />}
        title="Broker firm not found"
        description="It may have been removed, or the link is incomplete."
      />
    );
  }

  return (
    <>
      <PlatformPageHeader
        eyebrow={<BackToFirms />}
        title={
          <span className="flex flex-wrap items-center gap-2">
            {firm.name}
            {firm.is_platform_owner && <Badge variant="info">Platform owner</Badge>}
            {firm.status === "suspended" && <Badge variant="error">Suspended</Badge>}
          </span>
        }
        description={
          <>
            Alias <span className="font-medium text-foreground">{firm.slug}</span>
            {" · "}
            {firm.client_count} {firm.client_count === 1 ? "company" : "companies"}
            {" · "}created {fmtDateTime(firm.created_at)}
          </>
        }
      />
      <FirmStatusCard firm={firm} />
      <FirmActivityCard firmId={firm.id} firmName={firm.name} />
      <FirmSignInMethods firm={firm} />
      <FirmSenderCard firm={firm} />
      <FirmDomainsCard firm={firm} />
      <Card>
        <CardHeader>
          <CardTitle className="text-sm">Invite a firm admin</CardTitle>
          <CardDescription>
            A firm admin runs {firm.name}: its users, web addresses and removal
            of saved data. They invite the rest of the firm&apos;s staff
            themselves.
          </CardDescription>
        </CardHeader>
        <CardContent>
          <InviteUserForm
            idPrefix={`firm-admin-${firm.id}`}
            roleOptions={["firm_admin"]}
            defaultRole="firm_admin"
            firmId={firm.id}
          />
        </CardContent>
      </Card>
    </>
  );
}
