import { useState } from "react";
import { Loader2, Plus, ShieldAlert, Trash2 } from "lucide-react";
import {
  type AdminUser,
  useAdminClients,
  useAdminUsers,
  useBrokerFirms,
  useCreateBrokerFirm,
  useCreateClient,
  useCreateInvitation,
  useDeleteClient,
  useInvitations,
  useMe,
  usePatchClient,
  usePatchUser,
  useRevokeInvitation,
} from "@/api/hooks";
import { AlertDialog } from "@/components/ui/alert-dialog";
import { Badge } from "@/components/ui/badge";
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
import { FieldLabel, InfoHint } from "@/components/ui/tooltip";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { formatError } from "@/lib/errors";
import { toast } from "sonner";

const ASSIGNABLE_ROLES = [
  "broker_admin",
  "broker_viewer",
  "client_admin",
  "client_hr",
];
const CLIENT_ROLES = new Set(["client_admin", "client_hr"]);

function statusVariant(status: string): "good" | "warn" | "error" | "default" {
  if (status === "active") return "good";
  if (status === "invited") return "warn";
  if (status === "disabled") return "error";
  return "default";
}

export function AdminPage() {
  const { data: me, isLoading, isError, refetch } = useMe();
  const isSystemAdmin = me?.role === "system_admin";
  const canAdmin = me?.role === "broker_admin" || isSystemAdmin;

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
      {isSystemAdmin && <BrokerFirmsCard />}
      <ClientsCard />
      <UsersCard meRole={me?.role ?? "broker_viewer"} />
    </div>
  );
}

function BrokerFirmsCard() {
  const { data: firms = [] } = useBrokerFirms();
  const create = useCreateBrokerFirm();
  const [name, setName] = useState("");

  const onCreate = async () => {
    if (!name.trim()) return;
    try {
      await create.mutateAsync(name.trim());
      toast.success("Broker firm created");
      setName("");
    } catch (e) {
      toast.error(formatError(e));
    }
  };

  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-1.5 text-sm">
          Broker firms
          <InfoHint>
            Platform-level. Each firm is a hard-isolated tenant boundary.
          </InfoHint>
        </CardTitle>
      </CardHeader>
      <CardContent className="space-y-3">
        <div className="flex items-end gap-2">
          <div className="flex flex-col gap-1.5 flex-1">
            <Label>New firm name</Label>
            <Input value={name} onChange={(e) => setName(e.target.value)} />
          </div>
          <Button onClick={onCreate} disabled={create.isPending || !name.trim()}>
            {create.isPending ? <Loader2 className="size-4 animate-spin" /> : <Plus className="size-4" />}
            Create
          </Button>
        </div>
        <ul className="divide-y divide-border rounded-md border border-border">
          {firms.map((f) => (
            <li key={f.id} className="flex items-center justify-between px-3 py-2 text-sm">
              <span className="font-medium">{f.name}</span>
              <span className="text-xs text-muted-foreground">
                {f.client_count} client{f.client_count === 1 ? "" : "s"}
              </span>
            </li>
          ))}
        </ul>
      </CardContent>
    </Card>
  );
}

function ClientsCard() {
  const { data: clients = [] } = useAdminClients();
  const create = useCreateClient();
  const patch = usePatchClient();
  const del = useDeleteClient();
  const [name, setName] = useState("");
  const [editing, setEditing] = useState<string | null>(null);
  const [editName, setEditName] = useState("");
  const [deleteTarget, setDeleteTarget] = useState<{ id: string; name: string } | null>(
    null,
  );

  const onCreate = async () => {
    if (!name.trim()) return;
    try {
      await create.mutateAsync(name.trim());
      toast.success("Client created");
      setName("");
    } catch (e) {
      toast.error(formatError(e));
    }
  };

  const onRename = async (id: string) => {
    try {
      await patch.mutateAsync({ id, name: editName.trim() });
      toast.success("Client renamed");
      setEditing(null);
    } catch (e) {
      toast.error(formatError(e));
    }
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
        <div className="flex items-end gap-2">
          <div className="flex flex-col gap-1.5 flex-1">
            <Label>New client name</Label>
            <Input value={name} onChange={(e) => setName(e.target.value)} />
          </div>
          <Button onClick={onCreate} disabled={create.isPending || !name.trim()}>
            {create.isPending ? <Loader2 className="size-4 animate-spin" /> : <Plus className="size-4" />}
            Create
          </Button>
        </div>
        <ul className="divide-y divide-border rounded-md border border-border">
          {clients.map((c) => (
            <li key={c.id} className="flex items-center justify-between gap-2 px-3 py-2 text-sm">
              {editing === c.id ? (
                <>
                  <Input
                    value={editName}
                    onChange={(e) => setEditName(e.target.value)}
                    className="h-8"
                  />
                  <div className="flex gap-1 shrink-0">
                    <Button size="sm" onClick={() => onRename(c.id)} disabled={patch.isPending}>
                      Save
                    </Button>
                    <Button size="sm" variant="ghost" onClick={() => setEditing(null)}>
                      Cancel
                    </Button>
                  </div>
                </>
              ) : (
                <>
                  <span className="font-medium">{c.name}</span>
                  <div className="flex gap-1 shrink-0">
                    <Button
                      size="sm"
                      variant="outline"
                      onClick={() => {
                        setEditing(c.id);
                        setEditName(c.name);
                      }}
                    >
                      Rename
                    </Button>
                    <Button
                      size="sm"
                      variant="ghost"
                      className="text-error hover:text-error"
                      aria-label={`Delete ${c.name}`}
                      onClick={() => setDeleteTarget({ id: c.id, name: c.name })}
                    >
                      <Trash2 className="size-3.5" />
                    </Button>
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
      open={deleteTarget !== null}
      onOpenChange={(open) => !open && setDeleteTarget(null)}
      title={`Delete ${deleteTarget?.name ?? "company"}?`}
      description="This permanently removes the company and its user-access grants. If it still has benefit years, delete those first. This cannot be undone."
      confirmLabel="Delete company"
      confirmVariant="destructive"
      loading={del.isPending}
      onConfirm={onDelete}
    />
    </>
  );
}

function UsersCard({ meRole }: { meRole: string }) {
  const { data: users = [] } = useAdminUsers();
  const { data: clients = [] } = useAdminClients();
  const { data: invites = [] } = useInvitations();
  const invite = useCreateInvitation();
  const patch = usePatchUser();
  const revoke = useRevokeInvitation();

  const [email, setEmail] = useState("");
  const [role, setRole] = useState("broker_viewer");
  const [clientIds, setClientIds] = useState<string[]>([]);

  const roleOptions = meRole === "system_admin"
    ? [...ASSIGNABLE_ROLES, "system_admin"]
    : ASSIGNABLE_ROLES;

  const onInvite = async () => {
    if (!email.trim()) return;
    try {
      await invite.mutateAsync({
        email: email.trim(),
        role,
        client_ids: CLIENT_ROLES.has(role) ? clientIds : [],
      });
      toast.success("Invitation sent");
      setEmail("");
      setClientIds([]);
    } catch (e) {
      toast.error(formatError(e));
    }
  };

  const onRoleChange = async (u: AdminUser, newRole: string) => {
    try {
      await patch.mutateAsync({ id: u.id, patch: { role: newRole } });
      toast.success("Role updated");
    } catch (e) {
      toast.error(formatError(e));
    }
  };

  const onToggleStatus = async (u: AdminUser) => {
    const next = u.status === "disabled" ? "active" : "disabled";
    try {
      await patch.mutateAsync({ id: u.id, patch: { status: next } });
      toast.success(next === "disabled" ? "User disabled" : "User enabled");
    } catch (e) {
      toast.error(formatError(e));
    }
  };

  const pendingByEmail = new Map(invites.map((i) => [i.email, i]));

  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-1.5 text-sm">
          Users
          <InfoHint>
            Invite by email — access is granted on first sign-in. Status: invited
            = awaiting first sign-in, active = has signed in, disabled = access
            revoked.
          </InfoHint>
        </CardTitle>
      </CardHeader>
      <CardContent className="space-y-4">
        <div className="rounded-md border border-border p-3 space-y-3 bg-muted/30">
          <div className="grid grid-cols-1 md:grid-cols-[1fr_180px_auto] gap-2 items-end">
            <div className="flex flex-col gap-1.5">
              <Label>Email</Label>
              <Input
                type="email"
                value={email}
                onChange={(e) => setEmail(e.target.value)}
                placeholder="person@company.com"
              />
            </div>
            <div className="flex flex-col gap-1.5">
              <FieldLabel
                hint={
                  <>
                    system_admin: full platform. broker_admin: manage this firm.
                    broker_viewer: read-only. client_admin / client_hr: limited
                    to the clients you grant below.
                  </>
                }
              >
                Role
              </FieldLabel>
              <Select value={role} onValueChange={setRole}>
                <SelectTrigger><SelectValue /></SelectTrigger>
                <SelectContent>
                  {roleOptions.map((r) => (
                    <SelectItem key={r} value={r}>{r}</SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
            <Button onClick={onInvite} disabled={invite.isPending || !email.trim()}>
              {invite.isPending ? <Loader2 className="size-4 animate-spin" /> : <Plus className="size-4" />}
              Invite
            </Button>
          </div>
          {CLIENT_ROLES.has(role) && (
            <div className="flex flex-col gap-1.5">
              <FieldLabel
                hint={
                  <>
                    Client roles only see the companies you select here. Broker
                    roles skip this — they see every client in the firm.
                  </>
                }
              >
                Grant access to clients
              </FieldLabel>
              <div className="flex flex-wrap gap-2">
                {clients.map((c) => {
                  const on = clientIds.includes(c.id);
                  return (
                    <button
                      key={c.id}
                      type="button"
                      onClick={() =>
                        setClientIds((prev) =>
                          on ? prev.filter((x) => x !== c.id) : [...prev, c.id],
                        )
                      }
                      className={
                        on
                          ? "rounded-full bg-primary text-primary-foreground text-xs px-3 py-1"
                          : "rounded-full border border-border text-xs px-3 py-1 hover:bg-muted"
                      }
                    >
                      {c.name}
                    </button>
                  );
                })}
                {clients.length === 0 && (
                  <span className="text-xs text-muted-foreground">Create a client first.</span>
                )}
              </div>
            </div>
          )}
        </div>

        <ul className="divide-y divide-border rounded-md border border-border">
          {users.map((u) => {
            const inv = pendingByEmail.get(u.email);
            return (
              <li key={u.id} className="flex items-center justify-between gap-3 px-3 py-2.5">
                <div className="min-w-0">
                  <div className="text-sm font-medium truncate">
                    {u.display_name || u.email}
                  </div>
                  <div className="text-xs text-muted-foreground truncate">{u.email}</div>
                </div>
                <div className="flex items-center gap-2 shrink-0">
                  <Badge variant={statusVariant(u.status)}>{u.status}</Badge>
                  <Select value={u.role} onValueChange={(v) => onRoleChange(u, v)}>
                    <SelectTrigger className="h-8 w-[150px]"><SelectValue /></SelectTrigger>
                    <SelectContent>
                      {roleOptions.map((r) => (
                        <SelectItem key={r} value={r}>{r}</SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                  {inv ? (
                    <Button
                      size="sm"
                      variant="ghost"
                      className="text-error hover:text-error"
                      onClick={async () => {
                        try {
                          await revoke.mutateAsync(inv.id);
                          toast.success("Invitation revoked");
                        } catch (e) {
                          toast.error(formatError(e));
                        }
                      }}
                    >
                      Revoke
                    </Button>
                  ) : (
                    <Button size="sm" variant="outline" onClick={() => onToggleStatus(u)}>
                      {u.status === "disabled" ? "Enable" : "Disable"}
                    </Button>
                  )}
                </div>
              </li>
            );
          })}
          {users.length === 0 && (
            <li className="px-3 py-3 text-sm text-muted-foreground flex items-center gap-2">
              <ShieldAlert className="size-4" /> No users yet.
            </li>
          )}
        </ul>
      </CardContent>
    </Card>
  );
}
