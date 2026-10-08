import { useRef, useState, type FormEvent } from "react";
import { ArrowRight } from "lucide-react";
import { Button } from "@/components/ui/button";
import { isLoginChallenge, loginWithPassword, verifyLoginCode } from "@/auth/brokerSession";
import { IDENTIFIER_MAX_LENGTH, PASSWORD_MAX_LENGTH } from "@/lib/loginValidation";
import { MFA_CODE_MAX_LENGTH, canSubmitMfaCode, normalizeMfaCode } from "@/lib/mfa";
import type { BrokerSession } from "@/stores/brokerSession";
import { BrokerField } from "./BrokerField";
import { codeError, passwordSignInError } from "./signInMessages";

export type PasswordStep = "credentials" | "code";

const EMAIL_SHAPE = /^[^\s@]+@[^\s@]+$/;

/** One request at a time per form, with a pending flag for the button. */
function usePending() {
  const [pending, setPending] = useState(false);
  const pendingRef = useRef(false);
  const run = async (operation: () => Promise<void>) => {
    if (pendingRef.current) return;
    pendingRef.current = true;
    setPending(true);
    try {
      await operation();
    } finally {
      pendingRef.current = false;
      setPending(false);
    }
  };
  return { pending, run };
}

/** Email and password sign-in for broker staff. Two-factor verification is
 *  mandatory for this method: an enrolled account answers with a challenge and
 *  a code step follows; an account still to enrol gets a session limited to
 *  setup, which the caller sends to `/broker/security`. */
export function BrokerPasswordSignIn({
  onSignedIn,
  onStepChange,
}: {
  onSignedIn: (session: BrokerSession) => void;
  onStepChange?: (step: PasswordStep) => void;
}) {
  const [challenge, setChallenge] = useState<string | null>(null);
  const [restartReason, setRestartReason] = useState<string | null>(null);

  const showStep = (token: string | null, reason: string | null = null) => {
    setChallenge(token);
    setRestartReason(reason);
    onStepChange?.(token ? "code" : "credentials");
  };

  return challenge ? (
    <CodeStep challenge={challenge} onSignedIn={onSignedIn} onRestart={(reason) => showStep(null, reason)} />
  ) : (
    <CredentialsStep
      initialError={restartReason}
      onChallenge={(token) => showStep(token)}
      onSignedIn={onSignedIn}
    />
  );
}

function CredentialsStep({
  initialError,
  onChallenge,
  onSignedIn,
}: {
  initialError: string | null;
  onChallenge: (challengeToken: string) => void;
  onSignedIn: (session: BrokerSession) => void;
}) {
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [fieldErrors, setFieldErrors] = useState<{ email?: string; password?: string }>({});
  const [error, setError] = useState<string | null>(initialError);
  const { pending, run } = usePending();

  const submit = (event: FormEvent) => {
    event.preventDefault();
    const trimmed = email.trim();
    const errors: { email?: string; password?: string } = {};
    if (!trimmed) errors.email = "Enter your work email address.";
    else if (trimmed.length > IDENTIFIER_MAX_LENGTH || !EMAIL_SHAPE.test(trimmed)) {
      errors.email = "Enter a valid email address, such as name@company.com.";
    }
    if (!password) errors.password = "Enter your password.";
    else if (password.length > PASSWORD_MAX_LENGTH) errors.password = "Use no more than 256 characters.";
    setFieldErrors(errors);
    if (errors.email || errors.password) return;
    void run(async () => {
      setError(null);
      try {
        const result = await loginWithPassword(trimmed, password);
        setPassword("");
        if (isLoginChallenge(result)) onChallenge(result.challenge_token);
        else onSignedIn(result);
      } catch (err) {
        setError(passwordSignInError(err));
      }
    });
  };

  return (
    <form noValidate onSubmit={submit} className="broker-login__fields" aria-label="Sign in with email and password">
      <BrokerField
        id="broker-sign-in-email"
        label="Work email"
        type="email"
        name="username"
        autoComplete="username"
        inputMode="email"
        autoCapitalize="none"
        spellCheck={false}
        maxLength={IDENTIFIER_MAX_LENGTH}
        required
        value={email}
        onChange={setEmail}
        error={fieldErrors.email}
      />
      <BrokerField
        id="broker-sign-in-password"
        label="Password"
        type="password"
        name="password"
        autoComplete="current-password"
        maxLength={PASSWORD_MAX_LENGTH}
        required
        value={password}
        onChange={setPassword}
        error={fieldErrors.password}
      />
      {error && <p role="alert" className="broker-login__alert">{error}</p>}
      <Button type="submit" className="broker-login__submit" loading={pending} disabled={pending}>
        <ArrowRight size={18} className="broker-login__arrow" aria-hidden="true" />
        {pending ? "Signing in…" : "Sign in"}
      </Button>
    </form>
  );
}

/** The authenticator step: a TOTP or a single-use recovery code against the
 *  five-minute challenge. An ended challenge returns to the password step. */
function CodeStep({
  challenge,
  onSignedIn,
  onRestart,
}: {
  challenge: string;
  onSignedIn: (session: BrokerSession) => void;
  onRestart: (reason: string | null) => void;
}) {
  const [code, setCode] = useState("");
  const [fieldError, setFieldError] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const { pending, run } = usePending();

  const submit = (event: FormEvent) => {
    event.preventDefault();
    if (!canSubmitMfaCode(code)) {
      setFieldError("Enter the 6-digit code, or a recovery code such as a1b2-c3d4-e5f6.");
      return;
    }
    setFieldError(null);
    void run(async () => {
      setError(null);
      try {
        onSignedIn(await verifyLoginCode(challenge, code.trim()));
      } catch (err) {
        const refusal = codeError(err);
        if (refusal.restart) onRestart(refusal.message);
        else setError(refusal.message);
      }
    });
  };

  return (
    <form noValidate onSubmit={submit} className="broker-login__fields" aria-label="Two-factor verification">
      <BrokerField
        id="broker-sign-in-code"
        label="Authentication code"
        name="one-time-code"
        autoComplete="one-time-code"
        inputMode="text"
        autoCapitalize="none"
        spellCheck={false}
        maxLength={MFA_CODE_MAX_LENGTH}
        required
        autoFocus
        className="broker-login__code"
        hint="Enter the 6-digit code from your authenticator app, or one of your recovery codes."
        value={code}
        onChange={(value) => setCode(normalizeMfaCode(value))}
        error={fieldError}
      />
      {error && <p role="alert" className="broker-login__alert">{error}</p>}
      <Button type="submit" className="broker-login__submit" loading={pending} disabled={pending}>
        <ArrowRight size={18} className="broker-login__arrow" aria-hidden="true" />
        {pending ? "Verifying…" : "Verify and sign in"}
      </Button>
      <button type="button" className="broker-login__text-button" disabled={pending} onClick={() => onRestart(null)}>
        Back to sign in
      </button>
    </form>
  );
}
