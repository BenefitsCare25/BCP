/** Employee-portal sign-in: email → one-time code (magic links land here with
 * ?email=&code= and auto-verify). No passwords, no MSAL. */
import { useEffect, useRef, useState } from "react";
import { useNavigate } from "@tanstack/react-router";
import { Mail, ShieldCheck } from "lucide-react";
import { useRequestOtp, useVerifyOtp } from "@/api/portal";
import { formatError } from "@/lib/errors";
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
    <div className="flex min-h-screen items-center justify-center bg-background p-4">
      <div className="w-full max-w-sm rounded-lg border border-border bg-card p-5">
        <div className="mb-5 flex items-center gap-2">
          <ShieldCheck className="size-6 text-primary" />
          <div>
            <h1 className="text-base font-semibold text-foreground">
              My Benefits Portal
            </h1>
            <p className="text-xs text-muted-foreground">
              Sign in with your work email
            </p>
          </div>
        </div>

        {step === "email" ? (
          <form onSubmit={submitEmail} className="space-y-3">
            <div className="space-y-1.5">
              <Label htmlFor="portal-email">Work email</Label>
              <Input
                id="portal-email"
                type="email"
                autoComplete="email"
                placeholder="you@company.com"
                value={email}
                onChange={(e) => setEmail(e.target.value)}
                autoFocus
              />
            </div>
            {error && <p className="text-xs text-error">{error}</p>}
            <Button type="submit" className="w-full" disabled={requestOtp.isPending}>
              <Mail className="size-4" />
              <span className="ml-1.5">
                {requestOtp.isPending ? "Sending…" : "Email me a sign-in code"}
              </span>
            </Button>
          </form>
        ) : (
          <form onSubmit={submitCode} className="space-y-3">
            <p className="text-xs text-muted-foreground">
              If <span className="font-medium text-foreground">{email}</span> has
              portal access, a 6-digit code is on its way. Enter it below.
            </p>
            <div className="space-y-1.5">
              <Label htmlFor="portal-code">Sign-in code</Label>
              <Input
                id="portal-code"
                inputMode="numeric"
                autoComplete="one-time-code"
                placeholder="123456"
                maxLength={6}
                value={code}
                onChange={(e) => setCode(e.target.value.replace(/\D/g, ""))}
                autoFocus
              />
            </div>
            {error && <p className="text-xs text-error">{error}</p>}
            <Button
              type="submit"
              className="w-full"
              disabled={verifyOtp.isPending || code.length < 6}
            >
              {verifyOtp.isPending ? "Verifying…" : "Sign in"}
            </Button>
            <button
              type="button"
              className="w-full text-center text-xs text-muted-foreground hover:text-foreground"
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
      </div>
    </div>
  );
}
