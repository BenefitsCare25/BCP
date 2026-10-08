/** HR credential sign-in: email OR HR ID + password, with an optional TOTP
 * step. Lives on `{slug}.hr.<base>`, where the subdomain scopes the tenant; on
 * a single-host deployment the company field does instead. */
import { useRef, useState } from "react";
import { useNavigate } from "@tanstack/react-router";
import { ArrowRight } from "lucide-react";
import { adoptSession, isTokenResult, useHrLogin, useHrMfa } from "@/api/hr";
import { errorCode, formatError } from "@/lib/errors";
import { loginError, validateCredentials, type LoginFieldErrors } from "@/lib/loginValidation";
import { handOverSetPasswordChallenge } from "@/lib/setPasswordToken";
import { useSessionEndNotice } from "@/lib/surfaceSession";
import { useHrSession } from "@/stores/hrSession";
import { hrPath } from "@/lib/tenant";
import { MFA_CODE_MAX_LENGTH, canSubmitMfaCode, normalizeMfaCode } from "@/lib/mfa";
import { PortalLoginScene } from "@/components/auth/PortalLoginScene";
import { LoginPasswordField } from "@/components/auth/LoginPasswordField";
import {
  CompanyField,
  commitCompany,
  useCompanyRequired,
} from "@/components/auth/CompanyField";
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
  // Why the last session ended when the company's HR access was switched off
  // mid-session (`hrClient.ts`); replaced by the next submit's outcome.
  const endNotice = useSessionEndNotice("hr");
  const [error, setError] = useState<string | null>(endNotice);
  const [company, setCompany] = useState("");
  const companyRequired = useCompanyRequired();
  const [fieldErrors, setFieldErrors] = useState<LoginFieldErrors>({});
  const submitting = useRef(false);

  const finish = () => void navigate({ to: useHrSession.getState().mfaEnrollmentRequired ? "/hr/security" : "/hr/dashboard" });

  const submitCredentials = (e: React.FormEvent) => {
    e.preventDefault();
    if (submitting.current || login.isPending) return;
    setError(null);
    const errors = validateCredentials(identifier, password, companyRequired ? company : undefined);
    setFieldErrors(errors);
    if (Object.keys(errors).length) return;
    // Settle the tenant BEFORE the request — the API client reads it
    // synchronously to build the X-Inspro-Tenant-Slug header.
    if (companyRequired && !commitCompany(company)) {
      setError("Enter your company code — it's in your invitation email.");
      return;
    }
    submitting.current = true;
    login.mutate(
      { identifier: identifier.trim(), password },
      {
        onSuccess: (data) => {
          setPassword("");
          if (isTokenResult(data)) {
            adoptSession(data);
            finish();
          } else if (data.status === "mfa_required") {
            setChallenge(data.challenge_token);
            setStep("mfa");
          } else if (data.status === "password_reset_required") {
            // Through tab storage, never the address bar (history, logs,
            // Referer); an in-app move keeps the memory fallback alive when
            // storage is blocked.
            handOverSetPasswordChallenge("hr", data.challenge_token);
            void navigate({ href: hrPath("/hr/set-password") });
          }
        },
        // 423 (locked out) and 429 (rate limited) must reach the user —
        // retrying against either only extends the backoff. 401 stays generic
        // so it can't confirm whether an account exists.
        onError: (err) => setError(loginError(err)),
        onSettled: () => { submitting.current = false; },
      },
    );
  };

  const submitMfa = (e: React.FormEvent) => {
    e.preventDefault();
    if (submitting.current || mfa.isPending) return;
    if (!canSubmitMfaCode(code) || !challenge) {
      setError("Enter a 6-digit authentication code or a recovery code such as a1b2-c3d4-e5f6.");
      return;
    }
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
          if (errorCode(err) === "challenge_expired") {
            setStep("credentials"); setCode(""); setChallenge(""); setPassword("");
          }
          setError(formatError(err));
        },
        onSettled: () => { submitting.current = false; },
      },
    );
  };

  return (
    <PortalLoginScene
      role="hr"
      company={company}
      title={step === "credentials" ? "Welcome back" : "Two-factor authentication"}
      subtitle={
        step === "credentials"
          ? "Sign in to your HR portal"
          : "Enter the 6-digit code from your authenticator app, or one of your recovery codes."
      }
    >
      {step === "credentials" ? (
        <form noValidate onSubmit={submitCredentials} className="space-y-4">
          <CompanyField id="hr-company" value={company} onChange={setCompany} error={fieldErrors.company} />
          <IdentifierField value={identifier} onChange={setIdentifier} placeholder="Email or HR ID" error={fieldErrors.identifier} />
          <LoginPasswordField id="hr-password" value={password} onChange={setPassword} error={fieldErrors.password} />
          {error && <p className="text-sm text-error" role="alert">{error}</p>}
          <Button
            type="submit"
            loading={login.isPending}
            className="h-12 w-full text-md transition-transform duration-150 active:scale-[0.99]"
            disabled={
              login.isPending ||
              !identifier.trim() ||
              !password ||
              (companyRequired && !company.trim())
            }
          >
            <ArrowRight className="portal-login__arrow size-5" aria-hidden="true" />
            {login.isPending ? "Signing in…" : "Sign in"}
          </Button>
        </form>
      ) : (
        <form onSubmit={submitMfa} className="space-y-4">
          <div className="space-y-1.5">
            <Label
              htmlFor="hr-totp"
              className="text-2xs font-semibold uppercase tracking-wide text-muted-foreground"
            >
              Authentication code
            </Label>
            <Input
              id="hr-totp"
              required
              name="one-time-code"
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
          {error && <p className="text-sm text-error" role="alert">{error}</p>}
          <Button
            type="submit"
            loading={mfa.isPending}
            className="h-12 w-full text-md transition-transform duration-150 active:scale-[0.99]"
            disabled={mfa.isPending || !canSubmitMfaCode(code)}
          >
            {mfa.isPending ? "Verifying…" : "Verify"}
          </Button>
          <button
            type="button"
            disabled={mfa.isPending}
            className="w-full text-center text-xs text-muted-foreground transition-colors hover:text-foreground"
            onClick={() => {
              setStep("credentials");
              setCode("");
              setChallenge("");
              setPassword("");
              setError(null);
            }}
          >
            Back to sign in
          </button>
        </form>
      )}
    </PortalLoginScene>
  );
}
