/** Employee-portal set / reset password: redeem a single-use token and choose
 * a password, then land in the portal. */
import { useMemo, useState } from "react";
import { useNavigate } from "@tanstack/react-router";
import { Lock, ShieldCheck } from "lucide-react";
import { isMemberToken, useMemberMfa, useMemberSetPassword } from "@/api/portal";
import { formatError } from "@/lib/errors";
import { MFA_CODE_MAX_LENGTH, canSubmitMfaCode, normalizeMfaCode } from "@/lib/mfa";
import { AuthScene } from "@/components/auth/AuthScene";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";

const MIN_LENGTH = 12;

function strength(pw: string): { label: string; ok: boolean } {
  if (pw.length < MIN_LENGTH) return { label: `At least ${MIN_LENGTH} characters`, ok: false };
  let classes = 0;
  if (/[a-z]/.test(pw)) classes++;
  if (/[A-Z]/.test(pw)) classes++;
  if (/\d/.test(pw)) classes++;
  if (/[^A-Za-z0-9]/.test(pw)) classes++;
  if (classes < 3) return { label: "Mix letters, numbers & symbols", ok: false };
  return { label: "Looks good", ok: true };
}

export function PortalSetPasswordPage() {
  const navigate = useNavigate();
  const setPw = useMemberSetPassword();
  const mfa = useMemberMfa();

  const token = useMemo(
    () => new URLSearchParams(window.location.search).get("token") ?? "",
    [],
  );
  const [step, setStep] = useState<"password" | "mfa">("password");
  const [password, setPassword] = useState("");
  const [confirm, setConfirm] = useState("");
  const [code, setCode] = useState("");
  const [challenge, setChallenge] = useState("");
  const [error, setError] = useState<string | null>(null);

  const st = strength(password);
  const match = password.length > 0 && password === confirm;
  const canSubmit = st.ok && match && !!token;

  const finish = () => void navigate({ to: "/portal/coverage" });

  const submit = (e: React.FormEvent) => {
    e.preventDefault();
    setError(null);
    if (!token) {
      setError("This link is missing its token. Ask your HR team to resend it.");
      return;
    }
    setPw.mutate(
      { token, password },
      {
        onSuccess: (out) => {
          if (isMemberToken(out)) {
            finish();
          } else {
            // 2FA is required — verify the TOTP code before the session lands.
            setChallenge(out.challenge_token);
            setStep("mfa");
          }
        },
        onError: (err) => setError(formatError(err)),
      },
    );
  };

  const submitMfa = (e: React.FormEvent) => {
    e.preventDefault();
    setError(null);
    mfa.mutate(
      { challenge_token: challenge, code: code.trim() },
      {
        onSuccess: finish,
        onError: (err) => setError(formatError(err)),
      },
    );
  };

  if (step === "mfa") {
    return (
      <AuthScene
        eyebrow="Employee benefits portal"
        title="Two-factor authentication"
        subtitle="Enter the 6-digit code from your authenticator app, or one of your recovery codes."
      >
        <form onSubmit={submitMfa} className="space-y-4">
          <div className="space-y-1.5">
            <Label
              htmlFor="portal-setpw-totp"
              className="text-2xs font-semibold uppercase tracking-wide text-muted-foreground"
            >
              Authentication code
            </Label>
            <Input
              id="portal-setpw-totp"
              inputMode="text"
              autoComplete="one-time-code"
              placeholder="123456"
              maxLength={MFA_CODE_MAX_LENGTH}
              value={code}
              onChange={(e) => setCode(normalizeMfaCode(e.target.value))}
              autoFocus
              className="h-12 text-center text-lg font-semibold tracking-[0.5em]"
            />
          </div>
          {error && <p className="text-sm text-error">{error}</p>}
          <Button
            type="submit"
            className="h-12 w-full text-md transition-transform duration-150 active:scale-[0.99]"
            disabled={mfa.isPending || !canSubmitMfaCode(code)}
          >
            {mfa.isPending ? "Verifying…" : "Verify & sign in"}
          </Button>
        </form>
      </AuthScene>
    );
  }

  return (
    <AuthScene
      eyebrow="Employee benefits portal"
      title="Set your password"
      subtitle="Choose a strong password to finish setting up your account."
    >
      <form onSubmit={submit} className="space-y-4">
        <div className="space-y-1.5">
          <Label
            htmlFor="portal-new-password"
            className="text-2xs font-semibold uppercase tracking-wide text-muted-foreground"
          >
            New password
          </Label>
          <div className="relative">
            <Lock className="pointer-events-none absolute left-3.5 top-1/2 size-[18px] -translate-y-1/2 text-muted-foreground" />
            <Input
              id="portal-new-password"
              type="password"
              autoComplete="new-password"
              placeholder="••••••••••••"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              autoFocus
              className="h-12 pl-11"
            />
          </div>
          {password.length > 0 && (
            <p className={st.ok ? "text-xs text-good" : "text-xs text-muted-foreground"}>
              {st.label}
            </p>
          )}
        </div>
        <div className="space-y-1.5">
          <Label
            htmlFor="portal-confirm-password"
            className="text-2xs font-semibold uppercase tracking-wide text-muted-foreground"
          >
            Confirm password
          </Label>
          <div className="relative">
            <Lock className="pointer-events-none absolute left-3.5 top-1/2 size-[18px] -translate-y-1/2 text-muted-foreground" />
            <Input
              id="portal-confirm-password"
              type="password"
              autoComplete="new-password"
              placeholder="••••••••••••"
              value={confirm}
              onChange={(e) => setConfirm(e.target.value)}
              className="h-12 pl-11"
            />
          </div>
          {confirm.length > 0 && !match && (
            <p className="text-xs text-error">Passwords don't match</p>
          )}
        </div>
        {error && <p className="text-sm text-error">{error}</p>}
        <Button
          type="submit"
          className="h-12 w-full text-md transition-transform duration-150 active:scale-[0.99]"
          disabled={setPw.isPending || !canSubmit}
        >
          <ShieldCheck className="size-[18px]" />
          {setPw.isPending ? "Saving…" : "Set password & sign in"}
        </Button>
      </form>
    </AuthScene>
  );
}
