import { FirmOwnerOnly } from "@/components/auth/FirmOwnerOnly";
import { useState } from "react";
import { Loader2, Plus, ShieldAlert, Trash2 } from "lucide-react";
import {
  type AdminClient,
  useAdminClients,
  useBrokerFirms,
  useCreateClient,
  useDashboardSummary,
  useDeleteClient,
  useMe,
  usePatchClient,
} from "@/api/hooks";
import { FirmConsole } from "@/components/admin/FirmConsole";
import { FirmPicker } from "@/components/admin/FirmPicker";
import { UsersCard } from "@/components/admin/UsersCard";
import { AlertDialog } from "@/components/ui/alert-dialog";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Switch } from "@/components/ui/switch";
import { InfoHint } from "@/components/ui/tooltip";
import { formatError } from "@/lib/errors";
import { isBrokerAdminRole, isFirmOwnerRole, isSystemAdminRole } from "@/lib/roles";
import { toast } from "sonner";
import { portalPath } from "@/lib/tenant";

export function AdminPage() {
  const { data: me, isLoading, isError, refetch } = useMe();
  const isSystemAdmin = isSystemAdminRole(me?.role);
  const canAdmin = isBrokerAdminRole(me?.role);
  const isFirmOwner = isFirmOwnerRole(me?.role);

  if (isLoading) {
    return <div className="text-sm text-muted-foreground p-8">Loading…</div>;
  }

  if (isError || !me) {
    return (
      <Card className="max-w-lg">
        <CardHeader>
          <CardTitle className="flex items-center gap-2 text-sm">
            <ShieldAlert className="size-4 text-warn" /> Couldn’t load your account
          </CardTitle>
          <CardDescription>
            We couldn’t verify your access just now.
          </CardDescription>
        </CardHeader>
        <CardContent>
          <Button variant="outline" size="sm" onClick={() => refetch()}>
            Retry
          </Button>
        </CardContent>
      </Card>
    );
  }

  if (!canAdmin) {
    return (
      <Card className="max-w-lg">
        <CardHeader>
          <CardTitle className="flex items-center gap-2 text-sm">
            <ShieldAlert className="size-4 text-warn" /> Access restricted
          </CardTitle>
          <CardDescription>
            Administration is available to broker administrators only. Contact
            your firm administrator if you need access.
          </CardDescription>
        </CardHeader>
      </Card>
    );
  }

  return (
    <div className="space-y-5">
      {/* Broker firms themselves are administered in the platform console
          (/platform/firms). This page is one firm's own surface: companies for
          broker administrators; users, web addresses and the platform access
          log for firm owners only, so no other role fetches them. */}
      <ClientsCard isSystemAdmin={isSystemAdmin} />
      {isFirmOwner && <UsersCard callerRole={me.role} />}
      {isFirmOwner && <FirmConsole isSystemAdmin={isSystemAdmin} />}
    </div>
  );
}

const ACCESS_SURFACES = [
  { key: "portal_enabled", label: "Employee portal", noun: "employee portal" },
  { key: "hr_enabled", label: "HR portal", noun: "HR portal" },
] as const;

type AccessSurface = (typeof ACCESS_SURFACES)[number];

/** The company's employee- and HR-portal kill switches, under the same
 *  permission as editing the company. Turning one off signs everyone out of
 *  that portal for the company, so only that direction is confirmed. */
function ClientAccessSwitches({ client }: { client: AdminClient }) {
  const patch = usePatchClient();
  const [turningOff, setTurningOff] = useState<AccessSurface | null>(null);

  const apply = async (surface: AccessSurface, enabled: boolean) => {
    try {
      await patch.mutateAsync(
        surface.key === "portal_enabled"
          ? { id: client.id, portal_enabled: enabled }
          : { id: client.id, hr_enabled: enabled },
      );
      toast.success(`${surface.label} turned ${enabled ? "on" : "off"} for ${client.name}`);
    } catch (e) {
      toast.error(formatError(e));
    } finally {
      setTurningOff(null);
    }
  };

  return (
    <div className="mt-1.5 flex flex-wrap items-center gap-x-4 gap-y-1.5">
      {ACCESS_SURFACES.map((surface) => {
        const id = `${surface.key}-${client.id}`;
        return (
          <div key={surface.key} className="flex items-center gap-2">
            <Switch
              id={id}
              checked={client[surface.key]}
              disabled={patch.isPending}
              aria-label={`${surface.label} for ${client.name}`}
              onCheckedChange={(on) => {
                if (on) void apply(surface, true);
                else setTurningOff(surface);
              }}
            />
            <Label htmlFor={id} className="text-xs font-normal text-muted-foreground">
              {surface.label}
            </Label>
          </div>
        );
      })}
      <AlertDialog
        open={turningOff !== null}
        onOpenChange={(open) => {
          if (!open) setTurningOff(null);
        }}
        title={`Turn off the ${turningOff?.noun ?? "portal"} for ${client.name}?`}
        description={`Everyone signed in to this portal for ${client.name} will be signed out, and no one can sign in to it until it is turned back on.`}
        confirmLabel="Turn off"
        loading={patch.isPending}
        onConfirm={() => {
          if (turningOff) void apply(turningOff, false);
        }}
      />
    </div>
  );
}

function ClientsCard({ isSystemAdmin }: { isSystemAdmin: boolean }) {
  const { data: clients = [] } = useAdminClients();
  const { data: firms = [] } = useBrokerFirms(isSystemAdmin);
  const [firmId, setFirmId] = useState("");
  const { data: summary } = useDashboardSummary();
  const create = useCreateClient();
  // Join the firm roll-up (member counts + current benefit year) onto each
  // client so this single firm-wide surface shows what the old Companies tab did.
  const statsById = new Map(
    (summary?.companies ?? []).map((c) => [c.id, c]),
  );
  const patch = usePatchClient();
  const del = useDeleteClient();
  const [name, setName] = useState("");
  const [legalName, setLegalName] = useState("");
  const [editing, setEditing] = useState<string | null>(null);
  const [editName, setEditName] = useState("");
  const [editLegal, setEditLegal] = useState("");
  const [editSlug, setEditSlug] = useState("");
  // The alias before the edit began, so the confirm below can tell a change
  // from a no-op without re-deriving it from the list mid-save.
  const [slugWas, setSlugWas] = useState("");
  const [aliasConfirm, setAliasConfirm] = useState<string | null>(null);
  const [deleteTarget, setDeleteTarget] = useState<{ id: string; name: string } | null>(
    null,
  );

  const onCreate = async () => {
    if (!name.trim()) return;
    try {
      // Omit broker_firm_id when there is nothing to disambiguate — the backend
      // resolves the sole firm, and a broker_admin may not name one at all.
      await create.mutateAsync({
        name: name.trim(),
        ...(legalName.trim() ? { legal_name: legalName.trim() } : {}),
        ...(firmId ? { broker_firm_id: firmId } : {}),
      });
      toast.success("Client created");
      setName("");
      setLegalName("");
    } catch (e) {
      toast.error(formatError(e));
    }
  };

  const beginEdit = (c: AdminClient) => {
    setEditing(c.id);
    setEditName(c.name);
    setEditLegal(c.legal_name ?? "");
    setEditSlug(c.slug ?? "");
    setSlugWas(c.slug ?? "");
  };

  const save = async (id: string) => {
    const nextSlug = editSlug.trim().toLowerCase();
    try {
      await patch.mutateAsync({
        id,
        name: editName.trim(),
        // Always sent, so clearing the field clears the stored value. Safe
        // because the PATCH is partial on the server and this form is the only
        // thing that edits these fields.
        legal_name: editLegal.trim() || null,
        // Only sent when it CHANGED. Sending it unchanged would be harmless
        // today but makes "never move the alias on a plain rename" a property
        // of this form rather than of the server, which is the wrong place for
        // it to live.
        ...(nextSlug && nextSlug !== slugWas ? { slug: nextSlug } : {}),
      });
      toast.success("Company updated");
      setEditing(null);
      setAliasConfirm(null);
    } catch (e) {
      toast.error(formatError(e));
    }
  };

  /** Changing the alias moves the portal address. Every invite already mailed
   *  points at the old one and an unopened invite is a live one-time password,
   *  so this is confirmed rather than saved silently. */
  const onSave = (id: string) => {
    const nextSlug = editSlug.trim().toLowerCase();
    if (slugWas && nextSlug && nextSlug !== slugWas) {
      setAliasConfirm(id);
      return;
    }
    void save(id);
  };

  const onDelete = async () => {
    if (!deleteTarget) return;
    try {
      await del.mutateAsync(deleteTarget.id);
      toast.success("Company deleted");
    } catch (e) {
      toast.error(formatError(e));
    } finally {
      setDeleteTarget(null);
    }
  };

  return (
    <>
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-1.5 text-sm">
          Client companies
          <InfoHint>
            Each client is a tenant within your firm. Switch between them from
            the top bar.
          </InfoHint>
        </CardTitle>
      </CardHeader>
      <CardContent className="space-y-3">
        <div className="flex flex-wrap items-end gap-2">
          <div className="flex min-w-40 flex-1 flex-col gap-1.5">
            <Label>Short name</Label>
            <Input
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder="CDL"
            />
          </div>
          {/* Optional at create: a company is often set up from a slip long
              before anyone has its registered name to hand, and blocking the
              create on it would just get a guess typed in. */}
          <div className="flex min-w-52 flex-1 flex-col gap-1.5">
            <Label>Full legal name</Label>
            <Input
              value={legalName}
              onChange={(e) => setLegalName(e.target.value)}
              placeholder="City Developments Limited"
            />
          </div>
          <FirmPicker firms={firms} value={firmId} onChange={setFirmId} />
          <Button onClick={onCreate} disabled={create.isPending || !name.trim()}>
            {create.isPending ? <Loader2 className="size-4 animate-spin" /> : <Plus className="size-4" />}
            Create
          </Button>
        </div>
        <p className="text-xs text-muted-foreground">
          The short name is what you see across this tool. The full legal name
          is what employees see in their portal, and the portal address is
          derived from the short name — you can change it below.
        </p>
        <ul className="divide-y divide-border rounded-md border border-border">
          {clients.map((c) => (
            <li key={c.id} className="flex items-center justify-between gap-2 px-3 py-2 text-sm">
              {editing === c.id ? (
                <>
                  <div className="grid min-w-0 flex-1 gap-2 sm:grid-cols-3">
                    <div className="flex flex-col gap-1">
                      <Label>Short name</Label>
                      <Input
                        value={editName}
                        onChange={(e) => setEditName(e.target.value)}
                        className="h-8"
                      />
                    </div>
                    <div className="flex flex-col gap-1">
                      <Label>Full legal name</Label>
                      <Input
                        value={editLegal}
                        onChange={(e) => setEditLegal(e.target.value)}
                        className="h-8"
                        placeholder="Not set"
                      />
                    </div>
                    <div className="flex flex-col gap-1">
                      <Label>Portal address</Label>
                      <Input
                        value={editSlug}
                        onChange={(e) => setEditSlug(e.target.value)}
                        className="h-8"
                        spellCheck={false}
                        aria-describedby={`slug-help-${c.id}`}
                      />
                      <p
                        id={`slug-help-${c.id}`}
                        className="text-xs text-muted-foreground"
                      >
                        {portalPath(editSlug.trim().toLowerCase() || c.slug)}
                      </p>
                    </div>
                  </div>
                  <div className="flex shrink-0 gap-1 self-start pt-5">
                    <Button size="sm" onClick={() => onSave(c.id)} disabled={patch.isPending}>
                      Save
                    </Button>
                    <Button size="sm" variant="ghost" onClick={() => setEditing(null)}>
                      Cancel
                    </Button>
                  </div>
                </>
              ) : (
                <>
                  <div className="min-w-0">
                    <span className="font-medium">{c.name}</span>
                    {/* The registered name beside the handle, so this list
                        answers "which company IS this" — "CDL" alone does not,
                        and it is the only name the portal shows members. */}
                    {c.legal_name && (
                      <span className="ml-2 text-muted-foreground">
                        {c.legal_name}
                      </span>
                    )}
                    {(() => {
                      const s = statsById.get(c.id);
                      if (!s) return null;
                      return (
                        <div className="text-xs text-muted-foreground">
                          {s.current_year
                            ? `${s.current_year.year} · Current`
                            : "No current year"}
                          {" · "}
                          {s.member_count} member
                          {s.member_count === 1 ? "" : "s"}
                        </div>
                      );
                    })()}
                    {/* The real address, not the bare alias. A slug on its own
                        reads as an internal code; the path is the thing a
                        broker can check against an invite email. */}
                    {c.slug && (
                      <div className="text-xs text-muted-foreground">
                        {portalPath(c.slug)}
                      </div>
                    )}
                    <ClientAccessSwitches client={c} />
                  </div>
                  <div className="flex gap-1 shrink-0">
                    <Button
                      size="sm"
                      variant="outline"
                      onClick={() => beginEdit(c)}
                    >
                      Edit
                    </Button>
                    <FirmOwnerOnly><Button
                      size="sm"
                      variant="ghost"
                      className="text-error hover:text-error"
                      aria-label={`Delete ${c.name}`}
                      onClick={() => setDeleteTarget({ id: c.id, name: c.name })}
                    >
                      <Trash2 className="size-3.5" />
                    </Button></FirmOwnerOnly>
                  </div>
                </>
              )}
            </li>
          ))}
          {clients.length === 0 && (
            <li className="px-3 py-3 text-sm text-muted-foreground">No clients yet.</li>
          )}
        </ul>
      </CardContent>
    </Card>
    <AlertDialog
      open={aliasConfirm !== null}
      onOpenChange={(open) => !open && setAliasConfirm(null)}
      title="Change the portal address?"
      description={`Employees will use ${portalPath(
        editSlug.trim().toLowerCase(),
      )} from now on. Invitation and sign-in emails already sent point at ${portalPath(
        slugWas,
      )} and will stop working, and so will any bookmark. Anyone who hasn't signed in yet will need a fresh invite.`}
      confirmLabel="Change address"
      confirmVariant="destructive"
      loading={patch.isPending}
      onConfirm={() => {
        if (aliasConfirm) void save(aliasConfirm);
      }}
    />
    <FirmOwnerOnly><AlertDialog
      open={deleteTarget !== null}
      onOpenChange={(open) => !open && setDeleteTarget(null)}
      title={`Delete ${deleteTarget?.name ?? "company"}?`}
      description="This permanently removes the company and its user-access grants. If it still has benefit years, delete those first. This cannot be undone."
      confirmLabel="Delete company"
      confirmVariant="destructive"
      loading={del.isPending}
      onConfirm={onDelete}
    /></FirmOwnerOnly>
    </>
  );
}
