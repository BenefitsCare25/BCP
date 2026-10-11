import { useState } from "react";
import { ShieldAlert } from "lucide-react";
import { toast } from "sonner";
import {
  type AdminInvitation,
  type AdminUser,
  type CreatedInvitation,
  useAdminUsers,
  useBrokerFirms,
  useInvitations,
  useMe,
  usePatchUser,
  useResetUserSignIn,
  useRevokeInvitation,
} from "@/api/hooks";
import { AlertDialog } from "@/components/ui/alert-dialog";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { InfoHint } from "@/components/ui/tooltip";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { ListError } from "@/components/platform/QueryStates";
import { formatError } from "@/lib/errors";
import {
  CLIENT_ROLES,
  assignableStaffRoles,
  isSystemAdminRole,
  roleLabel,
  useWriteAccess,
} from "@/lib/roles";
import { FirmPicker } from "./FirmPicker";
import { InvitationLinkNotice } from "./InvitationLinkNotice";
import { InviteUserForm } from "./InviteUserForm";

type Firm = { id: string; name: string };

function statusVariant(status: string): "good" | "warn" | "error" | "default" {
  if (status === "active") return "good";
  if (status === "invited") return "warn";
  if (status === "disabled") return "error";
  return "default";
}

/** Brokerage staff accounts — mounted only for firm owners (`firm_admin` for
 *  its own firm, `system_admin` across the platform), so no other role ever
 *  fetches user or invitation data. A firm admin may grant every staff role
 *  except system_admin; the API enforces the same. */
export function UsersCard({ callerRole }: { callerRole: string }) {
  const isSystemAdmin = isSystemAdminRole(callerRole);
  const usersQuery = useAdminUsers();
  const users = usersQuery.data ?? [];
  const { data: invites = [] } = useInvitations();
  // The platform firm list is a system_admin surface; a firm admin acts on
  // its own firm and never names one.
  const { data: firms = [], isPending: firmsPending } = useBrokerFirms(isSystemAdmin);
  const roleOptions = assignableStaffRoles(callerRole);
  const [demotion, setDemotion] = useState<{ user: AdminUser; role: string } | null>(null);
  const { data: me } = useMe();
  // This card is mounted for firm owners only, so a missing write right here
  // means a read-only access grant on this firm: its accounts and invitations
  // are listed, not changed (the API refuses those changes). A platform
  // admin's own firm-less row is not the firm's, so it stays editable.
  const { canRemove: canManage } = useWriteAccess();
  const rowReadOnly = (u: AdminUser) => !canManage && u.broker_firm_id !== null;
  const [resetting, setResetting] = useState<AdminUser | null>(null);
  const [resetLink, setResetLink] = useState<CreatedInvitation | null>(null);

  const pendingByEmail = new Map(invites.map((i) => [i.email, i]));
  // Firm-wide user management is brokerage staff only; a company's HR logins
  // live under Company settings → Authentication, so keep them out of here.
  const brokerUsers = users.filter((u) => !CLIENT_ROLES.has(u.role));

  return (
    <>
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-1.5 text-sm">
          Users
          <InfoHint>
            Each invitation gives a single-use link, shown once. The person accepts it with
            Microsoft 365 or by setting a password, whichever your sign-in methods allow.
            Status: invited = awaiting first sign-in, active = has signed in, disabled = access
            revoked.
          </InfoHint>
        </CardTitle>
      </CardHeader>
      <CardContent className="space-y-4">
        <p className="text-sm text-muted-foreground">For Microsoft sign-in, authenticator verification
          is off by default. Only a firm or system administrator can require it per account. Changing it
          signs that user out. Password sign-in always requires it.</p>
        {canManage && (
          <InviteUserForm
            idPrefix="broker-invite"
            roleOptions={roleOptions}
            defaultRole="broker_viewer"
            firms={isSystemAdmin ? firms : []}
          />
        )}
        {resetLink && (
          <InvitationLinkNotice invitation={resetLink} onDismiss={() => setResetLink(null)} />
        )}
        {usersQuery.isError ? (
          <ListError what="users" error={usersQuery.error} onRetry={() => void usersQuery.refetch()} />
        ) : (
        <ul className="divide-y divide-border rounded-md border border-border">
          {brokerUsers.map((u) => (
            <UserRow
              key={u.id}
              user={u}
              invitation={pendingByEmail.get(u.email)}
              roleOptions={roleOptions}
              readOnly={rowReadOnly(u)}
              onDemote={(user, role) => setDemotion({ user, role })}
              // Nobody resets their own sign-in (the API refuses it). A
              // disabled account is reset straight to invited, so a
              // compromised one is never re-enabled with its old factors.
              onResetSignIn={
                !rowReadOnly(u) && u.id !== me?.user_id ? () => setResetting(u) : undefined
              }
            />
          ))}
          {brokerUsers.length === 0 && (
            <li className="flex items-center gap-2 px-3 py-3 text-sm text-muted-foreground">
              <ShieldAlert className="size-4" /> No users yet.
            </li>
          )}
        </ul>
        )}
      </CardContent>
    </Card>
    {demotion && (
      <DemotionDialog
        key={`${demotion.user.id}-${demotion.role}`}
        user={demotion.user}
        role={demotion.role}
        firms={firms}
        firmsPending={firmsPending}
        onClose={() => setDemotion(null)}
      />
    )}
    {resetting && (
      <ResetSignInDialog
        key={resetting.id}
        user={resetting}
        onClose={() => setResetting(null)}
        onReset={(invitation) => {
          setResetting(null);
          setResetLink(invitation);
        }}
      />
    )}
    </>
  );
}

/** One staff account: name, Microsoft identity binding, status, authenticator
 *  requirement, role and access. Each row keeps its own edit state. */
function UserRow({
  user: u,
  invitation,
  roleOptions,
  readOnly,
  onDemote,
  onResetSignIn,
}: {
  user: AdminUser;
  invitation: AdminInvitation | undefined;
  roleOptions: string[];
  /** Listed only: no rename, binding, authenticator, role or status change. */
  readOnly: boolean;
  onDemote: (user: AdminUser, role: string) => void;
  /** Absent when this caller may not reset this account's sign-in. */
  onResetSignIn?: () => void;
}) {
  const patch = usePatchUser();
  const revoke = useRevokeInvitation();
  const [editing, setEditing] = useState(false);
  const [editName, setEditName] = useState("");
  const [binding, setBinding] = useState(false);
  const [bindingId, setBindingId] = useState("");
  // A role this caller cannot grant (a system admin, seen by a firm admin) is
  // shown, never offered as a change.
  const canChangeRole = !readOnly && roleOptions.includes(u.role);

  const run = async (change: () => Promise<unknown>, success: string) => {
    try {
      await change();
      toast.success(success);
      return true;
    } catch (e) {
      toast.error(formatError(e));
      return false;
    }
  };

  const onSaveName = async () => {
    const next = editName.trim();
    if (next === (u.display_name ?? "")) {
      setEditing(false);
      return;
    }
    // Empty string CLEARS the name (the API reads "" as "unset"); null would
    // mean "leave it alone", which is not what an emptied box is asking for.
    if (await run(() => patch.mutateAsync({ id: u.id, patch: { display_name: next } }),
      next ? "Name updated" : "Name removed")) setEditing(false);
  };

  const onRoleChange = (newRole: string) => {
    if (newRole === u.role) return;
    // A platform admin belongs to no firm, so moving one to a firm role has to
    // name the firm it joins — confirmed in a dialog that asks for it.
    if (isSystemAdminRole(u.role)) {
      onDemote(u, newRole);
      return;
    }
    void run(() => patch.mutateAsync({ id: u.id, patch: { role: newRole } }),
      `Role changed to ${roleLabel(newRole)}`);
  };

  const onBindIdentity = async () => {
    if (await run(() => patch.mutateAsync({ id: u.id, patch: { external_id: bindingId.trim() } }),
      "Microsoft identity bound")) {
      setBinding(false);
      setBindingId("");
    }
  };

  const nextStatus = u.status === "disabled" ? "active" : "disabled";

  return (
    <li className="flex flex-col items-stretch justify-between gap-3 px-3 py-2.5 sm:flex-row sm:items-center">
      {editing ? (
        <div className="flex min-w-0 flex-1 items-center gap-2">
          <Input
            autoFocus
            value={editName}
            onChange={(e) => setEditName(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter") void onSaveName();
              if (e.key === "Escape") setEditing(false);
            }}
            className="h-8 max-w-64"
            placeholder="Full name"
            aria-label={`Name for ${u.email}`}
          />
          <Button size="sm" onClick={() => void onSaveName()} disabled={patch.isPending}>
            Save
          </Button>
          <Button size="sm" variant="ghost" onClick={() => setEditing(false)}>
            Cancel
          </Button>
        </div>
      ) : (
        <div className="flex min-w-0 flex-1 items-center gap-2">
          <div className="min-w-0">
            {/* Only the email when there is no name — printing it as the title
                AND the subtitle just says it twice. */}
            {u.display_name ? (
              <>
                <div className="truncate text-sm font-medium">{u.display_name}</div>
                <div className="truncate text-xs text-muted-foreground">{u.email}</div>
              </>
            ) : (
              <div className="truncate text-sm">{u.email}</div>
            )}
          </div>
          {!readOnly && (
            <Button
              size="sm"
              variant="ghost"
              className="shrink-0 text-muted-foreground"
              onClick={() => {
                setEditName(u.display_name ?? "");
                setEditing(true);
              }}
            >
              {u.display_name ? "Rename" : "Add name"}
            </Button>
          )}
        </div>
      )}
      <div className="flex flex-wrap items-center gap-2 sm:shrink-0">
        {!readOnly && !u.external_id && (binding ? (
          <div className="flex flex-wrap items-center gap-2">
            <Input aria-label={`Microsoft object ID for ${u.email}`} value={bindingId}
              onChange={(event) => setBindingId(event.target.value)} className="max-w-80" />
            <Button size="sm" disabled={patch.isPending || !bindingId.trim()} onClick={() => void onBindIdentity()}>
              Save identity
            </Button>
            <Button size="sm" variant="ghost" onClick={() => setBinding(false)}>Cancel</Button>
          </div>
        ) : (
          <Button size="sm" variant="outline" onClick={() => { setBinding(true); setBindingId(""); }}>
            Bind Microsoft identity
          </Button>
        ))}
        <Badge variant={statusVariant(u.status)}>{u.status}</Badge>
        {invitation?.expires_at && Date.parse(invitation.expires_at) <= Date.now() && (
          <Badge variant="warn" title="Invite them again to issue a new link.">link expired</Badge>
        )}
        <div className="flex flex-col gap-1">
          <Label htmlFor={`broker-mfa-${u.id}`} className="text-xs">Authenticator</Label>
          <Select value={u.broker_mfa_required ? "required" : "off"}
            disabled={readOnly || patch.isPending}
            onValueChange={(value) => {
              const required = value === "required";
              void run(() => patch.mutateAsync({ id: u.id, patch: { broker_mfa_required: required } }),
                `Authenticator ${required ? "required" : "turned off"}. This user must sign in again.`);
            }}>
            <SelectTrigger id={`broker-mfa-${u.id}`} className="h-8 w-[130px]"
              aria-label={`Authenticator for ${u.email}`}><SelectValue /></SelectTrigger>
            <SelectContent>
              <SelectItem value="off">Off</SelectItem>
              <SelectItem value="required">Required</SelectItem>
            </SelectContent>
          </Select>
        </div>
        {canChangeRole ? (
          <Select value={u.role} onValueChange={onRoleChange}>
            <SelectTrigger className="h-8 w-[150px]" aria-label={`Role for ${u.email}`}>
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              {roleOptions.map((r) => (
                <SelectItem key={r} value={r}>{roleLabel(r)}</SelectItem>
              ))}
            </SelectContent>
          </Select>
        ) : (
          <Badge variant="outline">{roleLabel(u.role)}</Badge>
        )}
        {onResetSignIn && (
          <Button size="sm" variant="outline" onClick={onResetSignIn}>
            Reset sign-in
          </Button>
        )}
        {readOnly ? null : invitation ? (
          <Button size="sm" variant="ghost" className="text-error hover:text-error" disabled={revoke.isPending}
            onClick={() => void run(() => revoke.mutateAsync(invitation.id), "Invitation revoked")}>
            Revoke
          </Button>
        ) : (
          <Button size="sm" variant="outline" disabled={patch.isPending}
            onClick={() => void run(() => patch.mutateAsync({ id: u.id, patch: { status: nextStatus } }),
              nextStatus === "disabled" ? "User disabled" : "User enabled")}>
            {u.status === "disabled" ? "Enable" : "Disable"}
          </Button>
        )}
      </div>
    </li>
  );
}

/** Recovering an account whose password or authenticator is lost. The server
 *  removes both, signs the person out and returns a new invitation link,
 *  which the card then shows once. */
function ResetSignInDialog({
  user,
  onClose,
  onReset,
}: {
  user: AdminUser;
  onClose: () => void;
  onReset: (invitation: CreatedInvitation) => void;
}) {
  const reset = useResetUserSignIn();
  const [error, setError] = useState<string | null>(null);
  const name = user.display_name || user.email;

  const confirm = async () => {
    setError(null);
    try {
      const invitation = await reset.mutateAsync(user.id);
      toast.success(`Sign-in reset for ${name}`);
      onReset(invitation);
    } catch (e) {
      setError(formatError(e));
    }
  };

  return (
    <AlertDialog
      open
      onOpenChange={(open) => {
        if (!open && !reset.isPending) onClose();
      }}
      title={`Reset sign-in for ${name}?`}
      description={
        <div className="space-y-3">
          <p>
            Their password and authenticator, with its recovery codes, are removed and
            they are signed out everywhere. Their role and access stay as they are.
          </p>
          <p>
            You get a new single-use invitation link to send them. They must use it to
            sign in again: with a new password and a new authenticator, or with Microsoft
            if their account uses it. Any earlier invitation link stops working.
          </p>
          {error && (
            <p role="alert" className="text-error">
              {error}
            </p>
          )}
        </div>
      }
      confirmLabel="Reset sign-in"
      loading={reset.isPending}
      onConfirm={() => void confirm()}
    />
  );
}

/** Moving a platform admin to a firm role: the account joins one firm, so the
 *  firm is named here (filled in when there is only one). */
function DemotionDialog({
  user,
  role,
  firms,
  firmsPending,
  onClose,
}: {
  user: AdminUser;
  role: string;
  firms: Firm[];
  firmsPending: boolean;
  onClose: () => void;
}) {
  const patch = usePatchUser();
  const [firmId, setFirmId] = useState("");
  const [error, setError] = useState<string | null>(null);
  // With one firm there is one answer, so it is filled in rather than asked.
  const targetFirm = firmId || (firms.length === 1 ? firms[0].id : "");
  const name = user.display_name || user.email || "this user";
  const label = roleLabel(role);

  const confirm = async () => {
    if (!targetFirm) return;
    setError(null);
    try {
      await patch.mutateAsync({ id: user.id, patch: { role, broker_firm_id: targetFirm } });
      toast.success(`Role changed to ${label}`);
      onClose();
    } catch (e) {
      // Kept in the dialog with the firm choice it is about: a 422 when the
      // server would not take the firm, a 409 for the last platform admin.
      setError(formatError(e));
    }
  };

  return (
    <AlertDialog
      open
      onOpenChange={(open) => {
        if (!open && !patch.isPending) onClose();
      }}
      title={`Change ${name} to ${label}?`}
      description={
        <div className="space-y-3">
          <p>
            A platform admin belongs to no broker firm. As {label}, this account
            joins one firm and loses platform-wide access. They are signed out
            and must sign in again.
          </p>
          {firms.length > 1 ? (
            <FirmPicker
              firms={firms}
              value={firmId}
              onChange={(v) => {
                setFirmId(v);
                setError(null);
              }}
            />
          ) : firms.length === 1 ? (
            <p>
              They will join <strong className="text-foreground">{firms[0].name}</strong>.
            </p>
          ) : (
            <p>
              {firmsPending
                ? "Loading broker firms…"
                : "No broker firm exists yet, so this account cannot take a firm role."}
            </p>
          )}
          {error && (
            <p role="alert" className="text-error">
              {error}
            </p>
          )}
        </div>
      }
      confirmLabel="Change role"
      confirmVariant="default"
      tone="info"
      loading={patch.isPending}
      confirmDisabled={!targetFirm}
      onConfirm={() => void confirm()}
    />
  );
}
