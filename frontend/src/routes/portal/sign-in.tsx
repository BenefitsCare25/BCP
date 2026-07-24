/** Employee-portal sign-in: email → one-time code (magic links land here with
 * ?email=&code= and auto-verify). No passwords, no MSAL. */
import { useEffect, useRef, useState } from "react";
import { useNavigate } from "@tanstack/react-router";
import { Mail } from "lucide-react";
import { useRequestOtp, useVerifyOtp } from "@/api/portal";
import { formatError } from "@/lib/errors";
import { AuthScene } from "@/components/auth/AuthScene";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";

function validEmail(value: string): boolean {
  const at = value.indexOf("@");
  return at > 0 && value.slice(at + 1).includes(".");
}

export function PortalSignInPage() {
  const navigate = useNavigate();
  const requestOtp = useRequestOtp();
  const verifyOtp = useVerifyOtp();

  const [step, setStep] = useState<"email" | "code">("email");
  const [email, setEmail] = useState("");
  const [code, setCode] = useState("");
  const [error, setError] = useState<string | null>(null);
  const autoTried = useRef(false);

  const finishSignIn = () => void navigate({ to: "/portal/coverage" });

  // Magic-link path: /portal/sign-in?email=…&code=… verifies immediately.
  useEffect(() => {
    if (autoTried.current) return;
    const params = new URLSearchParams(window.location.search);
    const linkEmail = params.get("email");
    const linkCode = params.get("code");
    if (!linkEmail || !linkCode) return;
    autoTried.current = true;
    setEmail(linkEmail);
    setStep("code");
    verifyOtp.mutate(
      { email: linkEmail, code: linkCode },
      {
        onSuccess: finishSignIn,
        onError: (err) => setError(formatError(err)),
      },
    );
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const submitEmail = (e: React.FormEvent) => {
    e.preventDefault();
    setError(null);
    const trimmed = email.trim();
    if (!validEmail(trimmed)) {
      setError("Enter a valid email address.");
      return;
    }
    requestOtp.mutate(trimmed, {
      onSuccess: (out) => {
        setStep("code");
        // Local dev (mock auth) returns the code so sign-in works without email.
        if (out.debug_code) setCode(out.debug_code);
      },
      onError: (err) => setError(formatError(err)),
    });
  };

  const submitCode = (e: React.FormEvent) => {
    e.preventDefault();
    setError(null);
    verifyOtp.mutate(
      { email: email.trim(), code: code.trim() },
      {
        onSuccess: finishSignIn,
        onError: () =>
          setError("That code is invalid or has expired — request a new one."),
      },
    );
  };

  return (
    <AuthScene
      eyebrow="Employee benefits portal"
      title={step === "email" ? "Sign in" : "Check your email"}
      subtitle={
        step === "email"
          ? "Sign in to access your benefits, claims and coverage."
          : "Enter the 6-digit code we just sent you."
      }
    >
      {step === "email" ? (
        <form onSubmit={submitEmail} className="space-y-4">
          <div className="space-y-1.5">
            <Label
              htmlFor="portal-email"
              className="text-xs font-semibold uppercase tracking-wide text-muted-foreground"
            >
              Work email
            </Label>
            <div className="relative">
              <Mail className="pointer-events-none absolute left-3.5 top-1/2 size-[18px] -translate-y-1/2 text-muted-foreground" />
              <Input
                id="portal-email"
                type="email"
                autoComplete="email"
                placeholder="you@company.com"
                value={email}
                onChange={(e) => setEmail(e.target.value)}
                autoFocus
                className="h-12 pl-11"
              />
            </div>
          </div>
          {error && <p className="text-sm text-error">{error}</p>}
          <Button
            type="submit"
            className="h-12 w-full text-[15px] transition-transform duration-150 active:scale-[0.99]"
            disabled={requestOtp.isPending}
          >
            <Mail className="size-[18px]" />
            {requestOtp.isPending ? "Sending…" : "Email me a sign-in code"}
          </Button>
        </form>
      ) : (
        <form onSubmit={submitCode} className="space-y-4">
          <p className="text-center text-sm text-muted-foreground">
            If <span className="font-medium text-foreground">{email}</span> has
            portal access, a 6-digit code is on its way.
          </p>
          <div className="space-y-1.5">
            <Label
              htmlFor="portal-code"
              className="text-xs font-semibold uppercase tracking-wide text-muted-foreground"
            >
              Sign-in code
            </Label>
            <Input
              id="portal-code"
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
            disabled={verifyOtp.isPending || code.length < 6}
          >
            {verifyOtp.isPending ? "Verifying…" : "Sign in"}
          </Button>
          <button
            type="button"
            className="w-full text-center text-xs text-muted-foreground transition-colors hover:text-foreground"
            onClick={() => {
              setStep("email");
              setCode("");
              setError(null);
            }}
          >
            Use a different email
          </button>
        </form>
      )}
    </AuthScene>
  );
}
