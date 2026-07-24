/** Employee-portal sign-in: username (email / member ID / employee ID) +
 * password, with an optional two-factor step. */
import { useState } from "react";
import { useNavigate } from "@tanstack/react-router";
import { KeyRound, Lock, User } from "lucide-react";
import { isMemberToken, useMemberLogin, useMemberMfa } from "@/api/portal";
import { formatError } from "@/lib/errors";
import { AuthScene } from "@/components/auth/AuthScene";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";

export function PortalSignInPage() {
  const navigate = useNavigate();
  const login = useMemberLogin();
  const mfa = useMemberMfa();

  const [step, setStep] = useState<"credentials" | "mfa">("credentials");
  const [identifier, setIdentifier] = useState("");
  const [password, setPassword] = useState("");
  const [code, setCode] = useState("");
  const [challenge, setChallenge] = useState("");
  const [error, setError] = useState<string | null>(null);

  const finish = () => void navigate({ to: "/portal/coverage" });

  const submitCredentials = (e: React.FormEvent) => {
    e.preventDefault();
    setError(null);
    login.mutate(
      { identifier: identifier.trim(), password },
      {
        onSuccess: (out) => {
          if (isMemberToken(out)) {
            finish();
          } else if (out.status === "password_reset_required") {
            window.location.assign(
              `/portal/set-password?token=${encodeURIComponent(out.challenge_token)}`,
            );
          } else {
            setChallenge(out.challenge_token);
            setStep("mfa");
          }
        },
        onError: () =>
          setError("Those details weren't recognised. Check and try again."),
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

  return (
    <AuthScene
      eyebrow="Employee benefits portal"
      title={step === "credentials" ? "Sign in" : "Two-factor authentication"}
      subtitle={
        step === "credentials"
          ? "Sign in to access your benefits, claims and coverage."
          : "Enter the 6-digit code from your authenticator app."
      }
    >
      {step === "credentials" ? (
        <form onSubmit={submitCredentials} className="space-y-4">
          <div className="space-y-1.5">
            <Label
              htmlFor="portal-identifier"
              className="text-xs font-semibold uppercase tracking-wide text-muted-foreground"
            >
              Email, member ID or employee ID
            </Label>
            <div className="relative">
              <User className="pointer-events-none absolute left-3.5 top-1/2 size-[18px] -translate-y-1/2 text-muted-foreground" />
              <Input
                id="portal-identifier"
                type="text"
                autoComplete="username"
                spellCheck={false}
                placeholder="you@company.com  or  EM-7Q2M8K"
                value={identifier}
                onChange={(e) => setIdentifier(e.target.value)}
                autoFocus
                className="h-12 pl-11"
              />
            </div>
          </div>
          <div className="space-y-1.5">
            <Label
              htmlFor="portal-password"
              className="text-xs font-semibold uppercase tracking-wide text-muted-foreground"
            >
              Password
            </Label>
            <div className="relative">
              <Lock className="pointer-events-none absolute left-3.5 top-1/2 size-[18px] -translate-y-1/2 text-muted-foreground" />
              <Input
                id="portal-password"
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
              htmlFor="portal-totp"
              className="text-xs font-semibold uppercase tracking-wide text-muted-foreground"
            >
              Authentication code
            </Label>
            <Input
              id="portal-totp"
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
