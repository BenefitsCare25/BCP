import { useState, type FormEvent } from "react";
import { Loader2, Plus } from "lucide-react";
import { toast } from "sonner";
import { type CreatedInvitation, useCreateInvitation } from "@/api/hooks";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { FieldLabel } from "@/components/ui/tooltip";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { sendErrorMessage } from "@/lib/errors";
import { roleLabel } from "@/lib/roles";
import { FirmPicker } from "./FirmPicker";
import { InvitationLinkNotice } from "./InvitationLinkNotice";

const OBJECT_ID = /^[0-9a-f]{8}-(?:[0-9a-f]{4}-){3}[0-9a-f]{12}$/i;

const ROLE_HINTS: Record<string, string> = {
  system_admin: "the platform's master admin, outside every firm.",
  firm_admin: "runs the firm — users, web addresses and removal of saved data.",
  broker_admin: "manages companies and settings.",
  broker_viewer: "read-only.",
};

/** The broker staff invitation: email, optional name, an optional Microsoft
 *  object ID and a role. Used by the Users card and by the platform console,
 *  which fixes the firm and the role (a new firm's first firm admin).
 *
 *  The invitee accepts through the single-use link returned here, with
 *  Microsoft 365 or by setting a password, whichever the firm allows. Binding
 *  an object ID in advance is optional. The link is shown once, below the
 *  form. Nothing here says "sent": whether an email goes out depends on the
 *  mail setup. */
export function InviteUserForm({
  idPrefix,
  roleOptions,
  defaultRole,
  firms = [],
  firmId,
  onInvited,
}: {
  idPrefix: string;
  roleOptions: string[];
  defaultRole: string;
  /** Firms to choose between (a platform admin's FirmPicker). */
  firms?: { id: string; name: string }[];
  /** A fixed target firm; hides the picker. */
  firmId?: string;
  onInvited?: (invitation: CreatedInvitation) => void;
}) {
  const invite = useCreateInvitation();
  const [email, setEmail] = useState("");
  const [name, setName] = useState("");
  const [objectId, setObjectId] = useState("");
  const [role, setRole] = useState(defaultRole);
  const [pickedFirmId, setPickedFirmId] = useState("");
  const [created, setCreated] = useState<CreatedInvitation | null>(null);
  const targetFirmId = firmId ?? pickedFirmId;
  const trimmedObjectId = objectId.trim();
  const objectIdInvalid = trimmedObjectId !== "" && !OBJECT_ID.test(trimmedObjectId);
  const canSubmit = Boolean(email.trim()) && !objectIdInvalid;

  const onSubmit = async (event: FormEvent) => {
    event.preventDefault();
    if (!canSubmit || invite.isPending) return;
    setCreated(null);
    try {
      const invitation = await invite.mutateAsync({
        email: email.trim(),
        ...(name.trim() ? { display_name: name.trim() } : {}),
        role,
        ...(trimmedObjectId ? { external_id: trimmedObjectId } : {}),
        client_ids: [],
        ...(targetFirmId ? { broker_firm_id: targetFirmId } : {}),
      });
      toast.success(`${invitation.email} invited as ${roleLabel(invitation.role)}`);
      setEmail("");
      setName("");
      setObjectId("");
      setCreated(invitation);
      onInvited?.(invitation);
    } catch (e) {
      toast.error(sendErrorMessage(e));
    }
  };

  return (
    <div className="space-y-3">
      <form
        onSubmit={(event) => void onSubmit(event)}
        className="space-y-3 rounded-md border border-border bg-muted/30 p-3"
      >
        <div className="grid grid-cols-1 items-end gap-2 md:grid-cols-[1fr_1fr_180px_auto]">
          <div className="flex flex-col gap-1.5">
            <Label htmlFor={`${idPrefix}-email`}>Email</Label>
            <Input
              id={`${idPrefix}-email`}
              type="email"
              autoComplete="off"
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              placeholder="person@company.com"
            />
          </div>
          {/* Optional, and the invite is not blocked on it — an invite is often
              sent from an email address alone. */}
          <div className="flex flex-col gap-1.5">
            <Label htmlFor={`${idPrefix}-name`}>Name</Label>
            <Input
              id={`${idPrefix}-name`}
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder="Optional"
            />
          </div>
          <div className="flex flex-col gap-1.5">
            <Label htmlFor={`${idPrefix}-oid`}>Microsoft object ID</Label>
            <Input
              id={`${idPrefix}-oid`}
              value={objectId}
              spellCheck={false}
              autoComplete="off"
              aria-invalid={objectIdInvalid || undefined}
              aria-describedby={objectIdInvalid ? `${idPrefix}-oid-error` : undefined}
              onChange={(e) => setObjectId(e.target.value)}
              placeholder="Optional"
            />
          </div>
          <div className="flex flex-col gap-1.5">
            <FieldLabel
              htmlFor={roleOptions.length > 1 ? `${idPrefix}-role` : undefined}
              hint={
                <>
                  {roleOptions.map((r) => (
                    <span key={r} className="block">
                      {roleLabel(r)}: {ROLE_HINTS[r] ?? ""}
                    </span>
                  ))}
                  <span className="mt-1 block">
                    A company&apos;s HR logins are managed under Company settings →
                    Authentication, not here.
                  </span>
                </>
              }
            >
              Role
            </FieldLabel>
            {roleOptions.length > 1 ? (
              <Select value={role} onValueChange={setRole}>
                <SelectTrigger id={`${idPrefix}-role`}><SelectValue /></SelectTrigger>
                <SelectContent>
                  {roleOptions.map((r) => (
                    <SelectItem key={r} value={r}>{roleLabel(r)}</SelectItem>
                  ))}
                </SelectContent>
              </Select>
            ) : (
              <p className="flex h-9 items-center text-sm font-medium text-foreground">
                {roleLabel(role)}
              </p>
            )}
          </div>
          {firmId === undefined && (
            <FirmPicker firms={firms} value={pickedFirmId} onChange={setPickedFirmId} />
          )}
          <Button type="submit" disabled={invite.isPending || !canSubmit}>
            {invite.isPending ? <Loader2 className="size-4 animate-spin" /> : <Plus className="size-4" />}
            Invite
          </Button>
        </div>
        {objectIdInvalid && (
          <p id={`${idPrefix}-oid-error`} role="alert" className="text-xs text-error">
            A Microsoft object ID looks like 00000000-0000-0000-0000-000000000000. Leave it
            empty to let the person link their Microsoft account when they accept.
          </p>
        )}
      </form>
      {created && <InvitationLinkNotice invitation={created} onDismiss={() => setCreated(null)} />}
    </div>
  );
}
