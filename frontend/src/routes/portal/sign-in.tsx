/** Employee-portal sign-in: username (email / member ID / employee ID) +
 * password, with an optional two-factor step. */
import { useState } from "react";
import { useNavigate } from "@tanstack/react-router";
import { KeyRound, Lock, User } from "lucide-react";
import { isMemberToken, useMemberLogin, useMemberMfa } from "@/api/portal";
import { takeAccessEndedMessage } from "@/api/portalClient";
import { errorStatus, formatError } from "@/lib/errors";
import { MFA_CODE_MAX_LENGTH, canSubmitMfaCode, normalizeMfaCode } from "@/lib/mfa";
import { AuthScene } from "@/components/auth/AuthScene";
import {
  CompanyField,
  commitCompany,
  useCompanyRequired,
} from "@/components/auth/CompanyField";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { useCompany } from "@/components/portal/useCompany";
import { portalPath } from "@/lib/tenant";

export function PortalSignInPage() {
  const navigate = useNavigate();
  const routeCompany = useCompany();
  const login = useMemberLogin();
  const mfa = useMemberMfa();

  const [step, setStep] = useState<"credentials" | "mfa">("credentials");
  const [identifier, setIdentifier] = useState("");
  const [password, setPassword] = useState("");
  const [code, setCode] = useState("");
  const [challenge, setChallenge] = useState("");
  // Landed here because their access ended mid-session (`portalClient.ts`
  // appends `?ended` before it clears the token). Read off the URL rather than
  // through the router's typed search: the redirect is a raw
  // `window.location.assign`, and routing it through `navigate({search})` is
  // how a `"1"` reaches the address bar as `%221%22`.
  //
  // The SERVER's sentence when we have it — it names the date their access
  // ended, which is the one fact a member cannot look up and the reason they
  // would otherwise have to ask their HR team. The undated line below is the
  // fallback for a blocked `sessionStorage` (private mode) or a refusal that
  // carried no message.
  //
  // Seeded into the same error slot the form already renders, and cleared by
  // the next submit — a member who then signs in as someone else must not keep
  // reading a sentence about the account they were just signed out of. Signing
  // in as THEMSELVES simply hits the same refusal again, from the server.
  const [error, setError] = useState<string | null>(() =>
    typeof window !== "undefined" &&
    new URLSearchParams(window.location.search).has("ended")
      ? takeAccessEndedMessage() ??
        "Your access to this portal has ended. Contact your HR team if you still need something from your record."
      : null,
  );
  const [company, setCompany] = useState("");
  const companyRequired = useCompanyRequired();

  // The company in the PATH when there is one; otherwise the code the member
  // just typed and `commitCompany` stored. Both are needed: the pathless
  // sign-in is still reachable from an old emailed link, and landing back on it
  // after a successful sign-in would be a loop.
  const finish = () =>
    void navigate({
      to: "/portal/$company/coverage",
      params: { company: routeCompany || company.trim().toLowerCase() },
    });

  const submitCredentials = (e: React.FormEvent) => {
    e.preventDefault();
    setError(null);
    // The tenant must be settled BEFORE the request — the API client reads it
    // synchronously to build the X-Inspro-Tenant-Slug header.
    if (companyRequired && !commitCompany(company)) {
      setError("Enter your company code — it's in your invitation email.");
      return;
    }
    login.mutate(
      { identifier: identifier.trim(), password },
      {
        onSuccess: (out) => {
          if (isMemberToken(out)) {
            finish();
          } else if (out.status === "password_reset_required") {
            // The COMPANY-scoped path. This is the first-sign-in flow for every
            // invited member (the mailed one-time password stamps the account
            // rotation-due), so emitting the pathless URL stranded exactly the
            // people the invite was for.
            window.location.assign(
              portalPath(
                routeCompany || company.trim().toLowerCase(),
                `/set-password?token=${encodeURIComponent(out.challenge_token)}`,
              ),
            );
          } else {
            setChallenge(out.challenge_token);
            setStep("mfa");
          }
        },
        // A blanket "not recognised" hid the two states the user MUST see:
        // 423 (locked out) and 429 (rate limited). Retrying against those just
        // extends the backoff, so surface the server's own message. 401 keeps
        // the generic wording — it must not confirm whether an account exists.
        onError: (err) => {
          const status = errorStatus(err);
          setError(
            status === 423 || status === 429
              ? formatError(err)
              : "Those details weren't recognised. Check and try again.",
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
          : "Enter the 6-digit code from your authenticator app, or one of your recovery codes."
      }
    >
      {step === "credentials" ? (
        <form onSubmit={submitCredentials} className="space-y-4">
          <CompanyField id="portal-company" value={company} onChange={setCompany} />
          <div className="space-y-1.5">
            <Label
              htmlFor="portal-identifier"
              className="text-2xs font-semibold uppercase tracking-wide text-muted-foreground"
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
              className="text-2xs font-semibold uppercase tracking-wide text-muted-foreground"
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
            className="h-12 w-full text-md transition-transform duration-150 active:scale-[0.99]"
            disabled={
              login.isPending ||
              !identifier.trim() ||
              !password ||
              (companyRequired && !company.trim())
            }
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
              className="text-2xs font-semibold uppercase tracking-wide text-muted-foreground"
            >
              Authentication code
            </Label>
            <Input
              id="portal-totp"
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
