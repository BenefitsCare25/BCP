import { usePortalTranslation } from "@/i18n/portal";
/** HR set / reset password: redeem a single-use token (from the emailed link
 * or a forced-rotation redirect) and choose a password. */
import { useRef, useState } from "react";
import { useNavigate } from "@tanstack/react-router";
import { Lock, ShieldCheck } from "lucide-react";
import { adoptSession, isTokenResult, useHrMfa, useHrSetPassword } from "@/api/hr";
import { errorCode, formatError } from "@/lib/errors";
import { PASSWORD_MAX_LENGTH } from "@/lib/loginValidation";
import { useSetPasswordToken } from "@/lib/setPasswordToken";
import { hrPath } from "@/lib/tenant";
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

export function HrSetPasswordPage() {
  const pt = usePortalTranslation();
  const navigate = useNavigate();
  const setPw = useHrSetPassword();
  const mfa = useHrMfa();

  // From the link's `#token=` (or a legacy `?token=`), else the sign-in
  // hand-over; scrubbed from the address bar once read.
  const token = useSetPasswordToken("hr");
  const [step, setStep] = useState<"password" | "mfa">("password");
  const [password, setPassword] = useState("");
  const [confirm, setConfirm] = useState("");
  const [code, setCode] = useState("");
  const [challenge, setChallenge] = useState("");
  const [error, setError] = useState<string | null>(null);
  const submitting = useRef(false);
  const [expired, setExpired] = useState(false);

  const st = strength(password);
  const match = password.length > 0 && password === confirm;
  const canSubmit = st.ok && match && !!token && password.length <= PASSWORD_MAX_LENGTH;

  const finish = () => void navigate({ to: "/hr/dashboard" });

  const submit = (e: React.FormEvent) => {
    e.preventDefault();
    if (submitting.current || setPw.isPending) return;
    setError(null);
    if (!token) {
      setError("This link is missing its token. Ask your administrator to resend it.");
      return;
    }
    if (!canSubmit) { setError("Choose a strong password and enter the same password twice."); return; }
    submitting.current = true;
    setPw.mutate(
      { token, password },
      {
        onSuccess: (data) => {
          setPassword(""); setConfirm("");
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
        onSettled: () => { submitting.current = false; },
      },
    );
  };

  const submitMfa = (e: React.FormEvent) => {
    e.preventDefault();
    if (submitting.current || mfa.isPending || expired || !challenge || !canSubmitMfaCode(code)) return;
    setError(null);
    submitting.current = true;
    mfa.mutate(
      { challenge_token: challenge, code: code.trim() },
      {
        onSuccess: (data) => {
          adoptSession(data);
          finish();
        },
        onError: (err) => {
          if (errorCode(err) === "challenge_expired") { setExpired(true); setCode(""); setChallenge(""); }
          setError(formatError(err));
        },
        onSettled: () => { submitting.current = false; },
      },
    );
  };

  if (step === "mfa") {
    return (
      <AuthScene
        portalTheme="hr"
        eyebrow={pt("HR administration")}
        title={pt("Two-factor authentication")}
        subtitle={pt("Enter the 6-digit code from your authenticator app, or one of your recovery codes.")}
      >
        <form onSubmit={submitMfa} className="space-y-4">
          <div className="space-y-1.5">
            <Label
              htmlFor="hr-setpw-totp"
              className="text-2xs font-semibold uppercase tracking-wide text-muted-foreground"
            >
              {pt("Authentication code")} </Label>
            <Input
              id="hr-setpw-totp"
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
          {error && <p className="text-sm text-error" role="alert">{pt(error)}</p>}
          {expired && <a className="block text-sm underline" href={hrPath("/hr/sign-in")}>{pt("Sign in with your new password")}</a>}
          <Button
            type="submit"
            className="h-12 w-full text-md transition-transform duration-150 active:scale-[0.99]"
            disabled={expired || mfa.isPending || !canSubmitMfaCode(code)}
          >
            {mfa.isPending ? pt("Verifying…") : pt("Verify & sign in")}
          </Button>
        </form>
      </AuthScene>
    );
  }

  return (
    <AuthScene
      portalTheme="hr"
      eyebrow={pt("HR administration")}
      title={pt("Set your password")}
      subtitle={pt("Choose a strong password to finish setting up your HR account.")}
    >
      <form onSubmit={submit} className="space-y-4">
        {!token && <p role="alert" className="text-sm text-error">{pt("This link is missing its token. Ask your administrator to resend it.")}</p>}
        <div className="space-y-1.5">
          <Label
            htmlFor="hr-new-password"
            className="text-2xs font-semibold uppercase tracking-wide text-muted-foreground"
          >
            {pt("New password")} </Label>
          <div className="relative">
            <Lock className="pointer-events-none absolute left-3.5 top-1/2 size-[18px] -translate-y-1/2 text-muted-foreground" />
            <Input
              id="hr-new-password"
              required
              minLength={MIN_LENGTH}
              maxLength={PASSWORD_MAX_LENGTH}
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
              {pt(st.label)}
            </p>
          )}
        </div>
        <div className="space-y-1.5">
          <Label
            htmlFor="hr-confirm-password"
            className="text-2xs font-semibold uppercase tracking-wide text-muted-foreground"
          >
            {pt("Confirm password")} </Label>
          <div className="relative">
            <Lock className="pointer-events-none absolute left-3.5 top-1/2 size-[18px] -translate-y-1/2 text-muted-foreground" />
            <Input
              id="hr-confirm-password"
              required
              maxLength={PASSWORD_MAX_LENGTH}
              type="password"
              autoComplete="new-password"
              placeholder="••••••••••••"
              value={confirm}
              onChange={(e) => setConfirm(e.target.value)}
              className="h-12 pl-11"
            />
          </div>
          {confirm.length > 0 && !match && (
            <p className="text-xs text-error">{pt("Passwords don't match")}</p>
          )}
        </div>
        {error && <p className="text-sm text-error" role="alert">{pt(error)}</p>}
        <Button
          type="submit"
          className="h-12 w-full text-md transition-transform duration-150 active:scale-[0.99]"
          disabled={setPw.isPending || !canSubmit}
        >
          <ShieldCheck className="size-[18px]" />
          {setPw.isPending ? pt("Saving…") : pt("Set password & sign in")}
        </Button>
      </form>
    </AuthScene>
  );
}
