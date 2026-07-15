/** Employee-detail block: portal access status + invite / resend / disable.
 * Rendered in the Employees page detail sheet; accounts are matched to the
 * employee by staff_id (the provisioning key). */
import { useState } from "react";
import { Link } from "@tanstack/react-router";
import { Eye, Loader2, Mail, ShieldCheck, ShieldOff } from "lucide-react";
import { toast } from "sonner";
import {
  useCreateMemberAccount,
  useMemberAccounts,
  useResendMemberInvite,
  useSetMemberAccountStatus,
} from "@/api/memberAccounts";
import { formatError } from "@/lib/errors";
import { AlertDialog } from "@/components/ui/alert-dialog";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { InfoHint } from "@/components/ui/tooltip";

const STATUS_BADGE = {
  invited: { variant: "warn" as const, label: "Invited" },
  active: { variant: "good" as const, label: "Active" },
  disabled: { variant: "error" as const, label: "Disabled" },
};

export function MemberAccountActions({
  employeeId,
  staffId,
}: {
  employeeId: string;
  staffId: string;
}) {
  const { data } = useMemberAccounts();
  const createAccount = useCreateMemberAccount();
  const resendInvite = useResendMemberInvite();
  const setStatus = useSetMemberAccountStatus();
  const [emailOverride, setEmailOverride] = useState("");
  const [needsEmail, setNeedsEmail] = useState(false);
  const [confirmDisable, setConfirmDisable] = useState(false);

  const account = data?.items.find((a) => a.staff_id === staffId);

  const setAccountStatus = async (
    accountId: string,
    next: "active" | "disabled",
  ) => {
    try {
      await setStatus.mutateAsync({ accountId, status: next });
      toast.success(
        next === "disabled"
          ? "Portal access disabled"
          : "Portal access re-enabled",
      );
    } catch (err) {
      toast.error(formatError(err));
    }
  };

  const invite = async () => {
    try {
      const created = await createAccount.mutateAsync({
        employeeId,
        email: emailOverride.trim() || undefined,
      });
      if (created.mail_sent === false) {
        toast.warning(
          `Account created for ${created.email}, but the invite email failed to send — check mail settings`,
        );
      } else {
        toast.success(`Portal invite sent to ${created.email}`);
      }
      setNeedsEmail(false);
      setEmailOverride("");
    } catch (err) {
      const message = formatError(err);
      // No roster email → surface the inline email field instead of a dead end.
      if (message.toLowerCase().includes("no email")) {
        setNeedsEmail(true);
      }
      toast.error(message);
    }
  };

  return (
    <div>
      <div className="flex items-center gap-1 mb-2">
        <div className="text-xs uppercase tracking-wider text-muted-foreground">
          Portal access
        </div>
        <InfoHint>
          Invited = emailed a sign-in code, not yet signed in. Active = has
          signed in. Disabled = access revoked. Inviting lets the employee view
          their benefits and submit claims online.
        </InfoHint>
      </div>
      <div className="rounded-md border border-border p-2.5 bg-card space-y-2">
        {account ? (
          <>
            <div className="flex items-center justify-between gap-2">
              <div className="min-w-0">
                <div className="text-sm font-medium truncate">{account.email}</div>
                <div className="text-xs text-muted-foreground">
                  {account.last_sign_in_at
                    ? `Last signed in ${new Date(account.last_sign_in_at).toLocaleDateString()}`
                    : "Never signed in"}
                </div>
              </div>
              <Badge variant={STATUS_BADGE[account.status].variant}>
                {STATUS_BADGE[account.status].label}
              </Badge>
            </div>
            <div className="flex gap-2">
              {account.status !== "disabled" && (
                <Button
                  size="sm"
                  variant="outline"
                  disabled={resendInvite.isPending}
                  onClick={async () => {
                    try {
                      const res = await resendInvite.mutateAsync(account.id);
                      if (res.mail_sent === false) {
                        toast.warning(
                          "Invite refreshed, but the email failed to send — check mail settings",
                        );
                      } else {
                        toast.success("Sign-in code re-sent");
                      }
                    } catch {
                      /* global toast covers it */
                    }
                  }}
                >
                  <Mail className="size-4" /> Resend code
                </Button>
              )}
              <Button
                size="sm"
                variant="outline"
                disabled={setStatus.isPending}
                className={account.status === "disabled" ? "" : "text-error hover:text-error"}
                onClick={() => {
                  // Disabling revokes a member's login — confirm first.
                  // Re-enabling is safe, so it stays one click.
                  if (account.status === "disabled") {
                    void setAccountStatus(account.id, "active");
                  } else {
                    setConfirmDisable(true);
                  }
                }}
              >
                {account.status === "disabled" ? (
                  <>
                    <ShieldCheck className="size-4" /> Re-enable
                  </>
                ) : (
                  <>
                    <ShieldOff className="size-4" /> Disable
                  </>
                )}
              </Button>
            </div>
            <AlertDialog
              open={confirmDisable}
              onOpenChange={setConfirmDisable}
              title="Disable portal access?"
              description={
                <>
                  <strong>{account.email}</strong> will no longer be able to sign
                  in to the member portal to view benefits or submit claims. You
                  can re-enable access at any time.
                </>
              }
              confirmLabel="Disable access"
              confirmVariant="destructive"
              loading={setStatus.isPending}
              onConfirm={() => {
                void setAccountStatus(account.id, "disabled").then(() =>
                  setConfirmDisable(false),
                );
              }}
            />
          </>
        ) : (
          <>
            {needsEmail && (
              <Input
                placeholder="employee@company.com"
                value={emailOverride}
                onChange={(e) => setEmailOverride(e.target.value)}
                className="h-8"
              />
            )}
            <Button size="sm" disabled={createAccount.isPending} onClick={invite}>
              {createAccount.isPending ? (
                <Loader2 className="size-4 animate-spin" />
              ) : (
                <Mail className="size-4" />
              )}
              Invite to portal
            </Button>
          </>
        )}
        <Button asChild size="sm" variant="ghost" className="w-full justify-start">
          <Link
            to="/operations/coverage"
            search={{ employee: employeeId, view: "employee" }}
          >
            <Eye className="size-4" /> Preview what this employee sees
          </Link>
        </Button>
      </div>
    </div>
  );
}
