/** HR set / reset password: redeem a single-use token (from the emailed link
 * or a forced-rotation redirect) and choose a password. */
import { useMemo, useState } from "react";
import { useNavigate } from "@tanstack/react-router";
import { Lock, ShieldCheck } from "lucide-react";
import { adoptSession, isTokenResult, useHrMfa, useHrSetPassword } from "@/api/hr";
import { formatError } from "@/lib/errors";
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

export function HrSetPasswordPage() {
  const navigate = useNavigate();
  const setPw = useHrSetPassword();
  const mfa = useHrMfa();

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

  const finish = () => void navigate({ to: "/hr/dashboard" });

  const submit = (e: React.FormEvent) => {
    e.preventDefault();
    setError(null);
    if (!token) {
      setError("This link is missing its token. Ask your administrator to resend it.");
      return;
    }
    setPw.mutate(
      { token, password },
      {
        onSuccess: (data) => {
          if (isTokenResult(data)) {
            adoptSession(data);
            finish();
          } else {
            // 2FA is required — verify the TOTP code before a session is issued.
            setChallenge(data.challenge_token);
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
        onSuccess: (data) => {
          adoptSession(data);
          finish();
        },
        onError: (err) => setError(formatError(err)),
      },
    );
  };

  if (step === "mfa") {
    return (
      <AuthScene
        eyebrow="HR administration"
        title="Two-factor authentication"
        subtitle="Enter the 6-digit code from your authenticator app to finish."
      >
        <form onSubmit={submitMfa} className="space-y-4">
          <div className="space-y-1.5">
            <Label
              htmlFor="hr-setpw-totp"
              className="text-xs font-semibold uppercase tracking-wide text-muted-foreground"
            >
              Authentication code
            </Label>
            <Input
              id="hr-setpw-totp"
              inputMode="numeric"
              autoComplete="one-time-code"
              placeholder="123456"
              maxLength={6}
              value={code}
              onChange={(e) => setCode(e.target.value.replace(/\D/g, ""))}
              autoFocus
              className="h-12 text-center text-lg font-semibold tracking-[0.5em]"
            />
          </div>
          {error && <p className="text-sm text-error">{error}</p>}
          <Button
            type="submit"
            className="h-12 w-full text-[15px] transition-transform duration-150 active:scale-[0.99]"
            disabled={mfa.isPending || code.length < 6}
          >
            {mfa.isPending ? "Verifying…" : "Verify & sign in"}
          </Button>
        </form>
      </AuthScene>
    );
  }

  return (
    <AuthScene
      eyebrow="HR administration"
      title="Set your password"
      subtitle="Choose a strong password to finish setting up your HR account."
    >
      <form onSubmit={submit} className="space-y-4">
        <div className="space-y-1.5">
          <Label
            htmlFor="hr-new-password"
            className="text-xs font-semibold uppercase tracking-wide text-muted-foreground"
          >
            New password
          </Label>
          <div className="relative">
            <Lock className="pointer-events-none absolute left-3.5 top-1/2 size-[18px] -translate-y-1/2 text-muted-foreground" />
            <Input
              id="hr-new-password"
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
            htmlFor="hr-confirm-password"
            className="text-xs font-semibold uppercase tracking-wide text-muted-foreground"
          >
            Confirm password
          </Label>
          <div className="relative">
            <Lock className="pointer-events-none absolute left-3.5 top-1/2 size-[18px] -translate-y-1/2 text-muted-foreground" />
            <Input
              id="hr-confirm-password"
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
          className="h-12 w-full text-[15px] transition-transform duration-150 active:scale-[0.99]"
          disabled={setPw.isPending || !canSubmit}
        >
          <ShieldCheck className="size-[18px]" />
          {setPw.isPending ? "Saving…" : "Set password & sign in"}
        </Button>
      </form>
    </AuthScene>
  );
}
