/** Employee-detail block: portal access status + invite / resend / disable.
 * Rendered in the Employees page detail sheet; accounts are matched to the
 * employee by staff_id (the provisioning key). */
import { useState } from "react";
import { Link } from "@tanstack/react-router";
import {
  Copy,
  Eye,
  KeyRound,
  Loader2,
  Mail,
  RefreshCw,
  ShieldCheck,
  ShieldOff,
} from "lucide-react";
import { toast } from "sonner";
import {
  useCreateMemberAccount,
  useMemberAccounts,
  useMemberPasswordSetupLink,
  useRegenerateMemberLoginId,
  useResendMemberInvite,
  useSetMemberAccountStatus,
  useSetMemberPassword,
  type MemberAccount,
} from "@/api/memberAccounts";
import { formatError } from "@/lib/errors";
import { tenantSurfaceUrl } from "@/lib/tenant";
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

/** Password-based sign-in: username (system login id), password status, and the
 * two ways a broker gets the member a password — a self-serve set-password link,
 * or setting one directly (email-less members). */
function MemberCredentials({ account }: { account: MemberAccount }) {
  const makeLink = useMemberPasswordSetupLink();
  const setPassword = useSetMemberPassword();
  const regenerate = useRegenerateMemberLoginId();
  const [link, setLink] = useState<string | null>(null);
  const [showSet, setShowSet] = useState(false);
  const [password, setPassword_] = useState("");
  const [error, setError] = useState<string | null>(null);

  const createLink = async () => {
    try {
      const res = await makeLink.mutateAsync(account.id);
      if (res.set_password_token) {
        // ABSOLUTE url — the portal lives on `{slug}.portal.<base>`, not on the
        // broker host, so a bare path is unclickable in an email and the token
        // is shown only once.
        setLink(
          tenantSurfaceUrl(
            "portal",
            res.tenant_slug,
            `/portal/set-password?token=${encodeURIComponent(res.set_password_token)}`,
          ),
        );
      }
    } catch (err) {
      toast.error(formatError(err));
    }
  };

  const copyLink = async () => {
    if (!link) return;
    try {
      await navigator.clipboard.writeText(link);
      toast.success("Set-password link copied");
    } catch {
      toast.error("Couldn't copy — select and copy manually.");
    }
  };

  const doSetPassword = async () => {
    setError(null);
    try {
      await setPassword.mutateAsync({ accountId: account.id, password });
      toast.success("Password set");
      setShowSet(false);
      setPassword_("");
    } catch (err) {
      setError(formatError(err));
    }
  };

  return (
    <div className="space-y-2 border-t border-border pt-2">
      <div className="flex items-center justify-between gap-2">
        <div className="min-w-0 text-sm">
          <span className="text-muted-foreground">Username</span>{" "}
          <span className="font-mono">{account.system_login_id ?? "—"}</span>
          <span className="ml-2 text-xs text-muted-foreground">
            {account.has_password ? "· password set" : "· no password yet"}
          </span>
        </div>
        <Button
          size="sm"
          variant="ghost"
          disabled={regenerate.isPending}
          title="Generate a new login ID"
          onClick={async () => {
            try {
              await regenerate.mutateAsync(account.id);
              toast.success("New login ID generated");
            } catch (err) {
              toast.error(formatError(err));
            }
          }}
        >
          <RefreshCw className="size-3.5" />
        </Button>
      </div>
      <div className="flex flex-wrap gap-2">
        <Button
          size="sm"
          variant="outline"
          disabled={makeLink.isPending}
          onClick={createLink}
        >
          {makeLink.isPending ? (
            <Loader2 className="size-4 animate-spin" />
          ) : (
            <KeyRound className="size-4" />
          )}
          Set-password link
        </Button>
        <Button
          size="sm"
          variant="outline"
          onClick={() => {
            setShowSet((v) => !v);
            setError(null);
          }}
        >
          <KeyRound className="size-4" /> Set password
        </Button>
      </div>
      {link && (
        <div className="rounded-md border border-primary/30 bg-primary/5 p-2.5 text-sm">
          <p className="mb-1 text-xs text-muted-foreground">
            Send this one-time link to the employee (opens on their portal, expires
            in 72 hours).
          </p>
          <div className="flex items-center gap-2">
            <code className="min-w-0 flex-1 truncate rounded bg-muted px-2 py-1 text-xs">
              {link}
            </code>
            <Button size="sm" variant="outline" onClick={copyLink}>
              <Copy className="size-3.5" /> Copy
            </Button>
          </div>
        </div>
      )}
      {showSet && (
        <div className="space-y-1.5">
          <Input
            type="password"
            autoComplete="new-password"
            placeholder="New password (min 12 chars)"
            value={password}
            onChange={(e) => setPassword_(e.target.value)}
            className="h-8"
          />
          {error && <p className="text-xs text-error">{error}</p>}
          <Button
            size="sm"
            disabled={setPassword.isPending || password.length < 12}
            onClick={doSetPassword}
          >
            {setPassword.isPending ? (
              <Loader2 className="size-4 animate-spin" />
            ) : null}
            Save password
          </Button>
        </div>
      )}
    </div>
  );
}

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
      if (!created.email) {
        // Email-less: no invite email — hand over a set-password link below.
        toast.success("Portal account created — use “Set-password link” below to give them access");
      } else if (created.mail_sent === false) {
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
                <div className="text-sm font-medium truncate">
                  {account.email ?? account.system_login_id ?? account.staff_id}
                </div>
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
            <MemberCredentials account={account} />
            <AlertDialog
              open={confirmDisable}
              onOpenChange={setConfirmDisable}
              title="Disable portal access?"
              description={
                <>
                  <strong>
                    {account.email ?? account.system_login_id ?? account.staff_id}
                  </strong>{" "}
                  will no longer be able to sign in to the member portal to view
                  benefits or submit claims. You can re-enable access at any time.
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
