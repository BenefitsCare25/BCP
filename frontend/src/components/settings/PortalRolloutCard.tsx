/** Employee-portal rollout — the one place invites are sent from.
 *
 * Lives on the Authentication tab beside the sign-in policy it depends on: the
 * "Login username" setting decides what members type, and this decides who has
 * an account to type it into. It used to be an "Invite all to portal" button on
 * the roster page, two screens away from the setting that governs it.
 *
 * The send is idempotent by construction — the server targets members with no
 * DELIVERED invite (`invite_sent_at IS NULL`), so pressing it again mails the
 * remainder and never a second copy to anyone. That is why this is one button
 * and not a "send" plus a "resend": a resend, at roster scale, is a second
 * email to hundreds of people who already have theirs.
 */
import { useState } from "react";
import { Link } from "@tanstack/react-router";
import { Loader2, Send } from "lucide-react";
import { toast } from "sonner";
import {
  useBulkInviteMembers,
  usePortalRollout,
} from "@/api/memberAccounts";
import { formatError } from "@/lib/errors";
import { useSession } from "@/stores/session";
import { useMe } from "@/api/hooks";
import { AlertDialog } from "@/components/ui/alert-dialog";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { SectionLabel } from "@/components/ui/section-label";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { InfoHint } from "@/components/ui/tooltip";

/** A bare label + figure. Deliberately NOT `StatTile`, which renders a Card —
 *  this already sits inside one, and nested cards are banned. */
function Stat({
  label,
  value,
  hint,
  tone,
}: {
  label: string;
  value: number;
  hint?: string;
  tone?: "good" | "warn";
}) {
  const valueTone =
    tone === "good" ? "text-good" : tone === "warn" ? "text-warn" : "text-foreground";
  return (
    <div>
      <div className="flex items-center gap-1">
        <SectionLabel>{label}</SectionLabel>
        {hint && <InfoHint>{hint}</InfoHint>}
      </div>
      <div className={`mt-1 text-2xl font-semibold tabular-nums ${valueTone}`}>
        {value.toLocaleString()}
      </div>
    </div>
  );
}

export function PortalRolloutCard({ policyYearId: suppliedYear, readOnly = false }: { policyYearId?: string; readOnly?: boolean } = {}) {
  const selectedYear = useSession((s) => s.currentPolicyYearId);
  const policyYearId = suppliedYear ?? selectedYear;
  const { data: me } = useMe();
  const cannotSend = readOnly || !me || me.role === "broker_viewer";
  const { data: rollout, isLoading, isError, error, refetch } = usePortalRollout(policyYearId);
  const bulkInvite = useBulkInviteMembers();
  const [confirm, setConfirm] = useState(false);
  const [reenableDisabled, setReenableDisabled] = useState(false);

  if (!policyYearId) {
    return (
      <Card>
        <CardHeader>
          <CardTitle className="text-sm">Employee portal access</CardTitle>
          <p className="text-sm text-muted-foreground">Select a benefit year to invite employees.</p>
        </CardHeader>
      </Card>
    );
  }

  if (isError) {
    return <Card><CardContent className="space-y-3 py-5" role="alert">
      <p className="text-sm">Could not load employee portal access. {formatError(error)}</p>
      <Button variant="outline" size="sm" onClick={() => void refetch()}>Retry portal access</Button>
    </CardContent></Card>;
  }
  if (isLoading || !rollout) {
    return (
      <Card>
        <CardContent className="flex items-center gap-2 py-6 text-sm text-muted-foreground">
          <Loader2 className="size-4 animate-spin" /> Loading portal access…
        </CardContent>
      </Card>
    );
  }

  const pending = rollout.invite_pending;
  const disabledPending = rollout.disabled_invite_pending ?? 0;
  const sendable = pending + disabledPending;
  const selectedCount = pending + (reenableDisabled ? disabledPending : 0);
  const blocked = rollout.sending || !rollout.mail_deliverable || sendable === 0;

  const send = async () => {
    try {
      const res = await bulkInvite.mutateAsync({ policyYearId, reenableDisabled });
      setConfirm(false);
      if (res.already_sending) {
        toast.info("A send is already running — nothing was queued twice.");
        return;
      }
      toast.success(`Queued ${res.queued.toLocaleString()} invitation${res.queued === 1 ? "" : "s"}.`);
    } catch (err) {
      toast.error(formatError(err));
    }
  };

  return (
    <Card>
      <CardHeader>
        <div className="flex flex-wrap items-start justify-between gap-4">
          <CardTitle className="text-sm">Employee portal access</CardTitle>
          {!cannotSend && <div className="shrink-0">
            <Button
              disabled={Boolean(blocked) || bulkInvite.isPending}
              onClick={() => { setReenableDisabled(false); setConfirm(true); }}
            >
              {bulkInvite.isPending || rollout.sending ? (
                <Loader2 className="size-4 animate-spin" />
              ) : (
                <Send className="size-4" />
              )}
              {rollout.sending
                ? "Sending…"
                : sendable > 0
                  ? `Send all ${sendable.toLocaleString()} invitations`
                  : "Send all invitations"}
            </Button>
          </div>}
        </div>
      </CardHeader>
      <CardContent className="space-y-4">
        <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-5">
          <Stat
            label="Using the portal"
            value={rollout.signed_in}
            tone="good"
            hint="Signed in at least once and chosen their own password."
          />
          <Stat
            label="Invited"
            value={rollout.invited}
            hint="Sent a one-time password, not signed in yet. The button above never emails these people again."
          />
          <Stat
            label="Not invited yet"
            value={rollout.invite_pending}
            tone={rollout.invite_pending > 0 ? "warn" : undefined}
            hint="Enabled accounts with a valid email address awaiting their first invitation."
          />
          <Stat
            label="Needs individual activation"
            value={rollout.no_email + rollout.duplicate}
            tone={rollout.no_email + rollout.duplicate > 0 ? "warn" : undefined}
            hint="No email address on file, or an address shared with another employee. These employees are skipped by bulk email; other eligible employees can still be invited."
          />
          <Stat label="Disabled access" value={rollout.disabled}
            hint="Disabled accounts are excluded unless you approve re-enabling them in the send confirmation." />
        </div>

        {rollout.needs_attention.length > 0 && (
          <div className="space-y-2">
            <div className="max-h-64 overflow-y-auto rounded-md border border-border">
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead>Staff ID</TableHead>
                    <TableHead>Name</TableHead>
                    <TableHead>Why</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {rollout.needs_attention.map((m) => (
                    <TableRow key={m.employee_id}>
                      <TableCell className="font-mono text-xs">
                        {m.staff_id}
                      </TableCell>
                      <TableCell><Link className="focus-ring underline underline-offset-2" to="/policy-admin/member-coverage"
                        search={{ employee: m.employee_id, view: undefined }}>{m.employee_name ?? m.staff_id}</Link></TableCell>
                      <TableCell className="text-xs text-muted-foreground">
                        {m.reason === "duplicate" ? (
                          <>
                            <span className="text-warn">
                              Shared email — individual activation required
                            </span>
                            {m.email && (
                              <span className="block font-mono text-2xs text-subtle">
                                {m.email}
                              </span>
                            )}
                          </>
                        ) : (
                          "No valid email address"
                        )}
                      </TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            </div>
            {rollout.needs_attention_truncated && (
              <p className="text-xs text-subtle">
                Showing the first {rollout.needs_attention.length} of{" "}
                {(rollout.no_email + rollout.duplicate).toLocaleString()}.
              </p>
            )}
          </div>
        )}
      </CardContent>

      <AlertDialog
        open={confirm}
        onOpenChange={setConfirm}
        title="Send all invitations?"
        description={
          <div className="space-y-3">
            <p>{selectedCount.toLocaleString()} employee{selectedCount === 1 ? "" : "s"} selected.</p>
            {disabledPending > 0 && <label className="flex items-start gap-2 text-sm">
              <input type="checkbox" className="mt-1" checked={reenableDisabled}
                disabled={bulkInvite.isPending} onChange={(event) => setReenableDisabled(event.target.checked)} />
              <span>Re-enable {disabledPending.toLocaleString()} disabled accounts and invite them.</span>
            </label>}
          </div>
        }
        confirmLabel={reenableDisabled ? "Re-enable and send" : "Send invitations"}
        confirmDisabled={selectedCount === 0 || !rollout.mail_deliverable || rollout.sending}
        confirmVariant="default"
        tone="info"
        loading={bulkInvite.isPending}
        onConfirm={() => void send()}
      />
    </Card>
  );
}
