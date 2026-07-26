/** HR credential sign-in: email OR HR ID + password, with an optional TOTP
 * step. Lives on `{slug}.hr.<base>`; the subdomain scopes the tenant. */
import { useState } from "react";
import { useNavigate } from "@tanstack/react-router";
import { KeyRound, Lock } from "lucide-react";
import { adoptSession, isTokenResult, useHrLogin, useHrMfa } from "@/api/hr";
import { errorStatus, formatError } from "@/lib/errors";
import { MFA_CODE_MAX_LENGTH, canSubmitMfaCode, normalizeMfaCode } from "@/lib/mfa";
import { AuthScene } from "@/components/auth/AuthScene";
import { IdentifierField } from "@/components/auth/IdentifierField";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";

export function HrSignInPage() {
  const navigate = useNavigate();
  const login = useHrLogin();
  const mfa = useHrMfa();

  const [step, setStep] = useState<"credentials" | "mfa">("credentials");
  const [identifier, setIdentifier] = useState("");
  const [password, setPassword] = useState("");
  const [code, setCode] = useState("");
  const [challenge, setChallenge] = useState("");
  const [error, setError] = useState<string | null>(null);

  const finish = () => void navigate({ to: "/hr/dashboard" });

  const submitCredentials = (e: React.FormEvent) => {
    e.preventDefault();
    setError(null);
    login.mutate(
      { identifier: identifier.trim(), password },
      {
        onSuccess: (data) => {
          if (isTokenResult(data)) {
            adoptSession(data);
            finish();
          } else if (data.status === "mfa_required") {
            setChallenge(data.challenge_token);
            setStep("mfa");
          } else if (data.status === "password_reset_required") {
            window.location.assign(
              `/hr/set-password?token=${encodeURIComponent(data.challenge_token)}`,
            );
          }
        },
        // 423 (locked out) and 429 (rate limited) must reach the user —
        // retrying against either only extends the backoff. 401 stays generic
        // so it can't confirm whether an account exists.
        onError: (err) => {
          const status = errorStatus(err);
          setError(
            status === 423 || status === 429
              ? formatError(err)
              : "Those credentials weren't recognised. Check and try again.",
          );
        },
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

  return (
    <AuthScene
      eyebrow="HR administration"
      title={step === "credentials" ? "Sign in" : "Two-factor authentication"}
      subtitle={
        step === "credentials"
          ? "Manage your company's employees, policies and claims."
          : "Enter the 6-digit code from your authenticator app, or one of your recovery codes."
      }
    >
      {step === "credentials" ? (
        <form onSubmit={submitCredentials} className="space-y-4">
          <IdentifierField value={identifier} onChange={setIdentifier} autoFocus />
          <div className="space-y-1.5">
            <Label
              htmlFor="hr-password"
              className="text-xs font-semibold uppercase tracking-wide text-muted-foreground"
            >
              Password
            </Label>
            <div className="relative">
              <Lock className="pointer-events-none absolute left-3.5 top-1/2 size-[18px] -translate-y-1/2 text-muted-foreground" />
              <Input
                id="hr-password"
                type="password"
                autoComplete="current-password"
                placeholder="••••••••••••"
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                className="h-12 pl-11"
              />
            </div>
          </div>
          {error && <p className="text-sm text-error">{error}</p>}
          <Button
            type="submit"
            className="h-12 w-full text-[15px] transition-transform duration-150 active:scale-[0.99]"
            disabled={login.isPending || !identifier.trim() || !password}
          >
            <KeyRound className="size-[18px]" />
            {login.isPending ? "Signing in…" : "Sign in"}
          </Button>
        </form>
      ) : (
        <form onSubmit={submitMfa} className="space-y-4">
          <div className="space-y-1.5">
            <Label
              htmlFor="hr-totp"
              className="text-xs font-semibold uppercase tracking-wide text-muted-foreground"
            >
              Authentication code
            </Label>
            <Input
              id="hr-totp"
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
            className="h-12 w-full text-[15px] transition-transform duration-150 active:scale-[0.99]"
            disabled={mfa.isPending || !canSubmitMfaCode(code)}
          >
            {mfa.isPending ? "Verifying…" : "Verify"}
          </Button>
          <button
            type="button"
            className="w-full text-center text-xs text-muted-foreground transition-colors hover:text-foreground"
            onClick={() => {
              setStep("credentials");
              setCode("");
              setError(null);
            }}
          >
            Back to sign in
          </button>
        </form>
      )}
    </AuthScene>
  );
}
