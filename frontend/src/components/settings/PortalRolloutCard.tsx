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
import { Loader2, Mail, MailWarning, Send } from "lucide-react";
import { toast } from "sonner";
import {
  useBulkInviteMembers,
  usePortalRollout,
  type PortalRollout,
} from "@/api/memberAccounts";
import { formatError } from "@/lib/errors";
import { useSession } from "@/stores/session";
import { AlertDialog } from "@/components/ui/alert-dialog";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
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

/** Why the send button is off, in the user's terms. Returning null means it is
 *  enabled — the caller renders no explanation, because there is nothing to
 *  explain. A disabled control with no stated reason is the failure mode here:
 *  "nothing to send" and "we can't send" look identical otherwise. */
function disabledReason(rollout: PortalRollout): string | null {
  if (rollout.sending)
    return "Sending — invites are going out now. This page updates as they land.";
  if (rollout.employees_total === 0)
    return "No employees on this benefit year yet — upload the roster first.";
  if (!rollout.mail_deliverable)
    return "Email delivery is misconfigured, so nothing would actually be sent. Members can still be given access one at a time from Member Coverage.";
  if (rollout.invite_pending === 0) {
    return rollout.no_email + rollout.duplicate > 0
      ? "Everyone reachable by email has been invited. The rest are listed below."
      : "Everyone on the roster has been invited.";
  }
  return null;
}

export function PortalRolloutCard() {
  const policyYearId = useSession((s) => s.currentPolicyYearId);
  const { data: rollout, isLoading } = usePortalRollout(policyYearId);
  const bulkInvite = useBulkInviteMembers();
  const [confirm, setConfirm] = useState(false);

  if (!policyYearId) {
    return (
      <Card>
        <CardHeader>
          <CardTitle className="text-sm">Employee portal access</CardTitle>
          <CardDescription>
            Add a benefit year covering today to invite employees to the portal.
          </CardDescription>
        </CardHeader>
      </Card>
    );
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

  const blocked = disabledReason(rollout);
  const pending = rollout.invite_pending;

  const send = async () => {
    try {
      const res = await bulkInvite.mutateAsync(policyYearId);
      setConfirm(false);
      if (res.already_sending) {
        toast.info("A send is already running — nothing was queued twice.");
        return;
      }
      toast.success(
        res.queued === 1
          ? "Sending 1 invite — it'll arrive shortly."
          : `Sending ${res.queued.toLocaleString()} invites — they'll arrive over the next few minutes.`,
      );
      const unreachable = res.no_email + res.duplicate;
      if (unreachable > 0) {
        toast.warning(
          `${unreachable.toLocaleString()} employee${unreachable === 1 ? "" : "s"} couldn't be emailed — see “Couldn't be reached” below.`,
        );
      }
    } catch (err) {
      toast.error(formatError(err));
    }
  };

  return (
    <Card>
      <CardHeader>
        <div className="flex items-start justify-between gap-4">
          <div className="min-w-0">
            <CardTitle className="text-sm">Employee portal access</CardTitle>
            <CardDescription>
              Each employee is emailed a one-time password and chooses their own
              at first sign-in. Nobody else ever sees it — not HR, not you.
            </CardDescription>
          </div>
          <div className="shrink-0 basis-80 text-right">
            <Button
              disabled={Boolean(blocked) || bulkInvite.isPending}
              onClick={() => setConfirm(true)}
            >
              {bulkInvite.isPending || rollout.sending ? (
                <Loader2 className="size-4 animate-spin" />
              ) : (
                <Send className="size-4" />
              )}
              {rollout.sending
                ? "Sending…"
                : pending > 0
                  ? `Send ${pending.toLocaleString()} invite${pending === 1 ? "" : "s"}`
                  : "Send invites"}
            </Button>
          </div>
        </div>
      </CardHeader>
      <CardContent className="space-y-4">
        <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
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
            hint="Has an email address but no invite delivered — exactly who the button above will email."
          />
          <Stat
            label="Can't be reached"
            value={rollout.no_email + rollout.duplicate}
            tone={rollout.no_email + rollout.duplicate > 0 ? "warn" : undefined}
            hint="No email address on file, or an address that already belongs to another employee. Listed below."
          />
        </div>

        {blocked && (
          <p className="flex items-start gap-2 text-sm text-muted-foreground">
            {rollout.mail_deliverable ? (
              <Mail className="mt-0.5 size-4 shrink-0" />
            ) : (
              <MailWarning className="mt-0.5 size-4 shrink-0 text-warn" />
            )}
            <span>{blocked}</span>
          </p>
        )}

        {/* Log mode still "delivers" — to the application log — which is how a
         * rollout is rehearsed before it is run for real. Blocking it would
         * make the flow untestable anywhere, since prod refuses to boot in log
         * mode in the first place. So: warn, don't disable. */}
        {rollout.mail_deliverable && rollout.mail_mode === "log" && (
          <p className="flex items-start gap-2 text-sm text-warn">
            <MailWarning className="mt-0.5 size-4 shrink-0" />
            <span>
              This environment writes invites to the application log instead of
              emailing them. Safe to use for a rehearsal; no employee will
              receive anything.
            </span>
          </p>
        )}

        {rollout.needs_attention.length > 0 && (
          <div className="space-y-2">
            <div>
              <SectionLabel>Couldn't be reached</SectionLabel>
              <p className="text-xs text-muted-foreground">
                Fix the roster row and they're picked up by the next send — or
                give them a set-password link individually from Member
                Coverage.
              </p>
            </div>
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
                      <TableCell>{m.employee_name ?? "—"}</TableCell>
                      <TableCell className="text-xs text-muted-foreground">
                        {m.reason === "duplicate" ? (
                          <>
                            <span className="text-warn">
                              Email already used by another employee
                            </span>
                            {m.email && (
                              <span className="block font-mono text-2xs text-subtle">
                                {m.email}
                              </span>
                            )}
                          </>
                        ) : (
                          "No email address"
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
        title={`Send ${pending.toLocaleString()} portal invite${pending === 1 ? "" : "s"}?`}
        description={
          <>
            Each of these {pending.toLocaleString()} employee
            {pending === 1 ? "" : "s"} gets an email with a one-time password and
            a link to your portal.
            {rollout.invited + rollout.signed_in > 0 && (
              <>
                {" "}
                The{" "}
                <strong>
                  {(rollout.invited + rollout.signed_in).toLocaleString()}
                </strong>{" "}
                who already have an invite will not be emailed again.
              </>
            )}
          </>
        }
        confirmLabel="Send invites"
        confirmVariant="default"
        tone="info"
        loading={bulkInvite.isPending}
        onConfirm={() => void send()}
      />
    </Card>
  );
}
