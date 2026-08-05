/** Portal access for ONE employee — Coverage & Members.
 *
 * It is a BUTTON IN THE IDENTITY STRIP that opens a sheet, not a card above the
 * benefit statement. Granting portal access is an administrative act on the
 * person; it is not a fact about their cover, and as a card it pushed the eight
 * coverage rows a broker came for below the fold. The button carries the
 * account's state, so nothing about "can this person get in?" is hidden behind
 * the click — only the controls that change it.
 *
 * Bulk rollout lives on Company settings → Authentication. This panel is the
 * individual case: the person the bulk send couldn't reach (no email address),
 * the one whose invite expired, the one who needs access today.
 *
 * It is organised around the ONE question a broker opens it to answer — "can
 * this person get in, and if not what do I do about it?" — so the panel leads
 * with a plain-language state and offers the single action that advances it.
 * The previous version showed the same four buttons in every state, including
 * "Resend code" for a code the portal cannot accept, and reported status only
 * as a bare badge whose tooltip described a sign-in method that no longer
 * exists.
 *
 * Credentials are never displayed. A one-time password goes to the member's
 * own mailbox; the only thing this panel can reveal is a set-password LINK,
 * which grants nothing on its own and is single-use.
 */
import { useState } from "react";
import {
  AlertTriangle,
  CheckCircle2,
  Clock,
  Copy,
  KeyRound,
  Loader2,
  Mail,
  MailX,
  RefreshCw,
  Send,
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
import { cn } from "@/lib/cn";
import { AlertDialog } from "@/components/ui/alert-dialog";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { SectionLabel } from "@/components/ui/section-label";
import {
  Sheet,
  SheetBody,
  SheetContent,
  SheetDescription,
  SheetHeader,
  SheetTitle,
  SheetTrigger,
} from "@/components/ui/sheet";

type Phase =
  | "none"          // no account at all
  | "no_email"      // account exists, nothing to send an invite to
  | "not_sent"      // has an email, invite never delivered
  | "invited"       // invite delivered, not signed in
  | "expired"       // invite delivered, one-time password timed out
  | "active"        // signed in / has chosen a password
  | "disabled";

const fmtDate = (iso: string) =>
  new Date(iso).toLocaleDateString(undefined, {
    day: "numeric",
    month: "short",
    year: "numeric",
  });

/** The panel's whole information architecture: one state, one sentence, one
 *  obvious next action. Derived here rather than inline so the badge, the
 *  status line and the buttons can never describe different states. */
function phaseOf(account: MemberAccount | undefined): Phase {
  if (!account) return "none";
  if (account.status === "disabled") return "disabled";
  // `status` flips to active on the first sign-in / set-password. `has_password`
  // does NOT mean onboarded — an outstanding invite also leaves a hash on the
  // row (the mailed one-time value).
  if (account.status === "active" || account.last_sign_in_at) return "active";
  if (account.invite_sent_at) {
    const expiry = account.invite_expires_at;
    return expiry && new Date(expiry) <= new Date() ? "expired" : "invited";
  }
  return account.email ? "not_sent" : "no_email";
}

const PHASE_BADGE: Record<
  Phase,
  {
    variant: "good" | "warn" | "error" | "default";
    label: string;
    /** Text tone for the trigger button, which states the phase in words
     * rather than in a lozenge — a badge inside a button is two controls'
     * worth of chrome for one control. */
    tone: string;
  }
> = {
  none: { variant: "default", label: "No account", tone: "text-muted-foreground" },
  no_email: { variant: "warn", label: "Needs a link", tone: "text-warn" },
  not_sent: { variant: "warn", label: "Not invited", tone: "text-warn" },
  invited: { variant: "warn", label: "Invited", tone: "text-warn" },
  expired: { variant: "error", label: "Invite expired", tone: "text-error" },
  active: { variant: "good", label: "Active", tone: "text-good" },
  disabled: { variant: "error", label: "Disabled", tone: "text-error" },
};

function StatusLine({ account, phase }: { account?: MemberAccount; phase: Phase }) {
  const Icon =
    phase === "active"
      ? CheckCircle2
      : phase === "expired" || phase === "disabled"
        ? AlertTriangle
        : phase === "invited"
          ? Clock
          : phase === "no_email"
            ? MailX
            : Mail;
  const tone =
    phase === "active"
      ? "text-good"
      : phase === "expired" || phase === "disabled"
        ? "text-error"
        : "text-muted-foreground";

  let headline: string;
  let detail: string | null = null;
  switch (phase) {
    case "none":
      headline = "No portal account yet";
      detail = account?.email ?? null;
      break;
    case "no_email":
      headline = "No email address on file";
      detail = "Give them a set-password link to open in person.";
      break;
    case "not_sent":
      headline = "Account created, invite not sent";
      detail = "Send it now, or it goes out with the next company-wide send.";
      break;
    case "invited":
      headline = `Invite sent ${account?.invite_sent_at ? fmtDate(account.invite_sent_at) : ""}`.trim();
      detail = account?.invite_expires_at
        ? `Not signed in yet. The one-time password works until ${fmtDate(account.invite_expires_at)}.`
        : "Not signed in yet.";
      break;
    case "expired":
      headline = "The one-time password expired";
      detail = "Send a fresh invite, or hand over a set-password link.";
      break;
    case "active":
      headline = account?.last_sign_in_at
        ? `Last signed in ${fmtDate(account.last_sign_in_at)}`
        : "Password set";
      break;
    case "disabled":
      headline = "Portal access revoked";
      detail = "They cannot sign in until this is re-enabled.";
      break;
  }

  return (
    <div className="flex items-start gap-2">
      <Icon className={`mt-0.5 size-4 shrink-0 ${tone}`} />
      <div className="min-w-0">
        <div className="text-sm font-medium text-foreground">{headline}</div>
        {detail && <p className="text-xs text-muted-foreground">{detail}</p>}
      </div>
    </div>
  );
}

/** Single-use set-password link. This — not a password — is the only credential
 *  material the UI ever reveals: it expires, it can be used once, and it lets
 *  the member choose a password nobody else learns. */
function LinkReveal({
  link,
  ttlHours,
  onCopy,
}: {
  link: string;
  ttlHours: number;
  onCopy: () => void;
}) {
  return (
    <div className="rounded-md border border-border bg-muted/50 p-2.5">
      <p className="mb-1.5 text-xs text-muted-foreground">
        One-time link — opens on their portal
        {ttlHours > 0 ? `, expires in ${ttlHours} hours` : ""}. They choose
        their own password; you never see it.
      </p>
      <div className="flex items-center gap-2">
        <code className="min-w-0 flex-1 truncate rounded bg-background px-2 py-1 text-xs">
          {link}
        </code>
        <Button size="sm" variant="outline" onClick={onCopy}>
          <Copy className="size-3.5" /> Copy
        </Button>
      </div>
    </div>
  );
}

/** Username + how they reach the portal. Shown for every provisioned member,
 *  because "what do I type and where" is the question a broker is on the phone
 *  answering — and for an email-less member the system login id IS the answer. */
function SignInDetails({ account }: { account: MemberAccount }) {
  const regenerate = useRegenerateMemberLoginId();
  const url = tenantSurfaceUrl("portal", account.tenant_slug, "/portal/sign-in");
  return (
    <div className="space-y-1.5 border-t border-border pt-2.5">
      <div className="flex items-center justify-between gap-2">
        <div className="min-w-0 text-sm">
          <SectionLabel>Username</SectionLabel>
          <div className="truncate font-mono text-xs text-foreground">
            {/* Resolved server-side from the company's "Login username"
              * setting — printing the email here regardless is what made a
              * company set to "System-generated ID" still show an address. */}
            {account.login_username ??
              account.email ??
              account.system_login_id ??
              account.staff_id}
          </div>
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
      <p className="truncate text-xs text-subtle" title={url}>
        {url}
      </p>
    </div>
  );
}

/** The controls themselves. Rendered inside the sheet, so it has no frame,
 * no heading and no card of its own. */
function AccountPanel({
  employeeId,
  account,
  minLength,
  linkTtlHours,
  phase,
}: {
  employeeId: string;
  account: MemberAccount | undefined;
  minLength: number;
  linkTtlHours: number;
  phase: Phase;
}) {
  const createAccount = useCreateMemberAccount();
  const resendInvite = useResendMemberInvite();
  const setStatus = useSetMemberAccountStatus();
  const makeLink = useMemberPasswordSetupLink();
  const setPassword = useSetMemberPassword();

  const [emailOverride, setEmailOverride] = useState("");
  const [needsEmail, setNeedsEmail] = useState(false);
  const [link, setLink] = useState<string | null>(null);
  const [showSet, setShowSet] = useState(false);
  const [password, setPasswordValue] = useState("");
  const [passwordError, setPasswordError] = useState<string | null>(null);
  const [confirmDisable, setConfirmDisable] = useState(false);
  const [confirmResend, setConfirmResend] = useState(false);

  const badge = PHASE_BADGE[phase];
  const busy =
    createAccount.isPending || resendInvite.isPending || makeLink.isPending;

  const invite = async () => {
    try {
      const created = await createAccount.mutateAsync({
        employeeId,
        email: emailOverride.trim() || undefined,
      });
      if (!created.email) {
        toast.success(
          "Account created — use “Set-password link” to give them access",
        );
      } else if (created.mail_sent === false) {
        toast.warning(
          `Account created for ${created.email}, but the invite email couldn't be sent. Nothing was delivered — try again, or hand over a set-password link.`,
        );
      } else {
        toast.success(`Invite sent to ${created.email}`);
      }
      setNeedsEmail(false);
      setEmailOverride("");
    } catch (err) {
      const message = formatError(err);
      if (message.toLowerCase().includes("no email")) setNeedsEmail(true);
      toast.error(message);
    }
  };

  const resend = async () => {
    try {
      const res = await resendInvite.mutateAsync(account!.id);
      setConfirmResend(false);
      if (res.mail_sent === false) {
        toast.warning(
          "The email couldn't be sent, so nothing changed — their previous password still works.",
        );
      } else {
        toast.success(`New one-time password sent to ${res.email}`);
      }
    } catch (err) {
      toast.error(formatError(err));
    }
  };

  const createLink = async () => {
    try {
      const res = await makeLink.mutateAsync(account!.id);
      if (res.set_password_token) {
        // ABSOLUTE url — the portal is a different host from the broker app, so
        // a bare path is unclickable once pasted anywhere.
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
    setPasswordError(null);
    try {
      await setPassword.mutateAsync({ accountId: account!.id, password });
      toast.success("Password set — tell them to change it after signing in");
      setShowSet(false);
      setPasswordValue("");
    } catch (err) {
      setPasswordError(formatError(err));
    }
  };

  const setAccountStatus = async (next: "active" | "disabled") => {
    try {
      await setStatus.mutateAsync({ accountId: account!.id, status: next });
      toast.success(
        next === "disabled" ? "Portal access disabled" : "Portal access re-enabled",
      );
    } catch (err) {
      toast.error(formatError(err));
    }
  };

  const linkButton = (label: string, variant: "default" | "outline") => (
    <Button size="sm" variant={variant} disabled={busy} onClick={createLink}>
      {makeLink.isPending ? (
        <Loader2 className="size-4 animate-spin" />
      ) : (
        <KeyRound className="size-4" />
      )}
      {label}
    </Button>
  );

  const resendButton = (label: string, variant: "default" | "outline") => (
    <Button
      size="sm"
      variant={variant}
      disabled={busy}
      onClick={() => setConfirmResend(true)}
    >
      {resendInvite.isPending ? (
        <Loader2 className="size-4 animate-spin" />
      ) : (
        <Send className="size-4" />
      )}
      {label}
    </Button>
  );

  return (
    <div className="space-y-3">
        <div className="flex items-start justify-between gap-3">
          <StatusLine account={account} phase={phase} />
          <Badge variant={badge.variant}>{badge.label}</Badge>
        </div>

        <div className="flex flex-wrap gap-2">
          {phase === "none" && (
            <Button size="sm" disabled={busy} onClick={invite}>
              {createAccount.isPending ? (
                <Loader2 className="size-4 animate-spin" />
              ) : (
                <Mail className="size-4" />
              )}
              Invite to portal
            </Button>
          )}
          {phase === "not_sent" && resendButton("Send invite", "default")}
          {phase === "expired" && resendButton("Send a new invite", "default")}
          {phase === "invited" && resendButton("Resend invite", "outline")}
          {phase === "no_email" && linkButton("Set-password link", "default")}
          {(phase === "not_sent" || phase === "invited" || phase === "expired") &&
            linkButton("Set-password link", "outline")}
          {phase === "active" && (
            <>
              {account?.email && resendButton("Reset password", "outline")}
              {linkButton("Set-password link", "outline")}
            </>
          )}
          {account && phase !== "disabled" && (
            <Button
              size="sm"
              variant="ghost"
              disabled={setStatus.isPending}
              className="text-error hover:text-error"
              onClick={() => setConfirmDisable(true)}
            >
              <ShieldOff className="size-4" /> Disable
            </Button>
          )}
          {phase === "disabled" && (
            <Button
              size="sm"
              variant="outline"
              disabled={setStatus.isPending}
              onClick={() => void setAccountStatus("active")}
            >
              <ShieldCheck className="size-4" /> Re-enable
            </Button>
          )}
        </div>

        {phase === "none" && needsEmail && (
          <Input
            placeholder="employee@company.com"
            value={emailOverride}
            onChange={(e) => setEmailOverride(e.target.value)}
            className="h-8"
          />
        )}

        {link && (
          <LinkReveal link={link} ttlHours={linkTtlHours} onCopy={copyLink} />
        )}

        {account && phase !== "disabled" && (
          <>
            {!showSet ? (
              <button
                type="button"
                // Underlined at rest, not only on hover: it is the sole
                // affordance for this action, and as unadorned subtle text
                // beside three real buttons it read as a caption.
                className="text-xs text-subtle underline underline-offset-2 hover:text-foreground"
                onClick={() => {
                  setShowSet(true);
                  setPasswordError(null);
                }}
              >
                Set a password manually instead
              </button>
            ) : (
              <div className="space-y-1.5">
                {/* Deliberately secondary: doing this means YOU know their
                 * password, which the invite and link flows both avoid. It
                 * stays available for the member who can use neither. */}
                <p className="text-xs text-muted-foreground">
                  You'll have to tell them this password, so prefer a
                  set-password link where you can.
                </p>
                <Input
                  type="password"
                  autoComplete="new-password"
                  placeholder={
                    minLength > 0
                      ? `New password (min ${minLength} characters)`
                      : "New password"
                  }
                  value={password}
                  onChange={(e) => setPasswordValue(e.target.value)}
                  className="h-8"
                />
                {passwordError && (
                  <p className="text-xs text-error">{passwordError}</p>
                )}
                <div className="flex gap-2">
                  <Button
                    size="sm"
                    disabled={
                      setPassword.isPending || password.length < minLength
                    }
                    onClick={doSetPassword}
                  >
                    {setPassword.isPending && (
                      <Loader2 className="size-4 animate-spin" />
                    )}
                    Save password
                  </Button>
                  <Button
                    size="sm"
                    variant="ghost"
                    onClick={() => {
                      setShowSet(false);
                      setPasswordValue("");
                      setPasswordError(null);
                    }}
                  >
                    Cancel
                  </Button>
                </div>
              </div>
            )}
          </>
        )}

        {account && <SignInDetails account={account} />}

        {/* No "preview what this employee sees" link here. The page this panel
         * opens from carries a Broker view / Employee view toggle in its
         * toolbar, and the same jump offered twice in one screen reads as two
         * different destinations. */}

      {account && (
        <>
          <AlertDialog
            open={confirmResend}
            onOpenChange={setConfirmResend}
            title={
              phase === "active" ? "Reset this password?" : "Send a new invite?"
            }
            description={
              phase === "active" ? (
                <>
                  A new one-time password is emailed to{" "}
                  <strong>{account.email}</strong> and{" "}
                  <strong>their current password stops working immediately</strong>
                  . They'll choose a new one when they next sign in.
                </>
              ) : (
                <>
                  A new one-time password is emailed to{" "}
                  <strong>{account.email}</strong>, replacing any previous invite.
                  Only use this if they never received or can no longer use the
                  first one.
                </>
              )
            }
            confirmLabel={phase === "active" ? "Reset password" : "Send invite"}
            confirmVariant={phase === "active" ? "destructive" : "default"}
            tone={phase === "active" ? "danger" : "info"}
            loading={resendInvite.isPending}
            onConfirm={() => void resend()}
          />
          <AlertDialog
            open={confirmDisable}
            onOpenChange={setConfirmDisable}
            title="Disable portal access?"
            description={
              <>
                <strong>
                  {account.email ?? account.system_login_id ?? account.staff_id}
                </strong>{" "}
                will no longer be able to sign in to view benefits or submit
                claims. You can re-enable access at any time.
              </>
            }
            confirmLabel="Disable access"
            confirmVariant="destructive"
            loading={setStatus.isPending}
            onConfirm={() => {
              void setAccountStatus("disabled").then(() =>
                setConfirmDisable(false),
              );
            }}
          />
        </>
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
  const [open, setOpen] = useState(false);
  const { data } = useMemberAccounts();
  const account = data?.items.find((a) => a.staff_id === staffId);
  const phase = phaseOf(account);
  const badge = PHASE_BADGE[phase];
  // Server-owned rules, not constants duplicated here: the password floor the
  // API will actually enforce, and the real lifetime of a set-password link.
  const minLength = data?.password_min_length ?? 0;
  const linkTtlHours = data?.set_password_ttl_hours ?? 0;

  return (
    <Sheet open={open} onOpenChange={setOpen}>
      <SheetTrigger asChild>
        <Button
          variant="outline"
          size="sm"
          className="gap-1.5"
          aria-label={`Portal access — ${badge.label}`}
        >
          <KeyRound className="size-4" aria-hidden />
          Portal access
          <span aria-hidden className="text-subtle">
            ·
          </span>
          <span className={cn("font-medium", badge.tone)}>{badge.label}</span>
        </Button>
      </SheetTrigger>
      <SheetContent className="sm:max-w-lg">
        <SheetHeader>
          <SheetTitle>Portal access</SheetTitle>
          <SheetDescription>
            The member is emailed a one-time password and chooses their own at
            first sign-in — nobody else ever sees it. Employees with no email
            address are given a single-use set-password link instead.
          </SheetDescription>
        </SheetHeader>
        <SheetBody>
          <AccountPanel
            employeeId={employeeId}
            account={account}
            phase={phase}
            minLength={minLength}
            linkTtlHours={linkTtlHours}
          />
        </SheetBody>
      </SheetContent>
    </Sheet>
  );
}
