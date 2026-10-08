import { useRef, useState, type FormEvent } from "react";
import { ArrowRight } from "lucide-react";
import { Button } from "@/components/ui/button";
import { acceptInviteWithPassword } from "@/auth/brokerSession";
import { PASSWORD_MAX_LENGTH } from "@/lib/loginValidation";
import type { BrokerSession } from "@/stores/brokerSession";
import { BrokerField } from "./BrokerField";
import { inviteError } from "./signInMessages";

const PASSWORD_MIN_LENGTH = 12;
const NAME_MAX_LENGTH = 255;

type FieldErrors = { name?: string; password?: string; confirm?: string };

function fieldErrorsFor(name: string, password: string, confirm: string): FieldErrors {
  const errors: FieldErrors = {};
  if (name.trim().length > NAME_MAX_LENGTH) errors.name = "Use no more than 255 characters.";
  if (password.length < PASSWORD_MIN_LENGTH) {
    errors.password = `Use at least ${PASSWORD_MIN_LENGTH} characters.`;
  } else if (password.length > PASSWORD_MAX_LENGTH) {
    errors.password = "Use no more than 256 characters.";
  }
  if (!confirm) errors.confirm = "Enter the password again.";
  else if (confirm !== password) errors.confirm = "The passwords don't match.";
  return errors;
}

/** Accept an invitation by choosing a password. The session that comes back
 *  can only set up two-factor verification, which is mandatory for password
 *  sign-in; the caller moves on to `/broker/security`. */
export function BrokerInviteSetPassword({
  inviteToken,
  onAccepted,
  onInvalid,
}: {
  inviteToken: string;
  onAccepted: (session: BrokerSession) => void;
  /** The invitation itself was refused (expired, revoked or used). */
  onInvalid: (message: string) => void;
}) {
  const [name, setName] = useState("");
  const [password, setPassword] = useState("");
  const [confirm, setConfirm] = useState("");
  const [fieldErrors, setFieldErrors] = useState<FieldErrors>({});
  const [error, setError] = useState<string | null>(null);
  const [pending, setPending] = useState(false);
  const pendingRef = useRef(false);

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    if (pendingRef.current) return;
    setError(null);
    const errors = fieldErrorsFor(name, password, confirm);
    setFieldErrors(errors);
    if (errors.name || errors.password || errors.confirm) return;
    pendingRef.current = true;
    setPending(true);
    try {
      const displayName = name.trim();
      const session = await acceptInviteWithPassword({
        invite_token: inviteToken,
        password,
        ...(displayName ? { display_name: displayName } : {}),
      });
      onAccepted(session);
    } catch (err) {
      const refusal = inviteError(err);
      if (refusal.invalid) onInvalid(refusal.message);
      else if (refusal.field) setFieldErrors({ password: refusal.message });
      else setError(refusal.message);
    } finally {
      pendingRef.current = false;
      setPending(false);
    }
  };

  return (
    <form noValidate onSubmit={(event) => void submit(event)} className="broker-login__fields" aria-label="Set a password">
      <BrokerField
        id="broker-invite-name"
        label="Your name"
        optional
        name="name"
        autoComplete="name"
        maxLength={NAME_MAX_LENGTH}
        value={name}
        onChange={setName}
        hint="How your name appears to colleagues. Your administrator may already have added it."
        error={fieldErrors.name}
      />
      <BrokerField
        id="broker-invite-password"
        label="New password"
        type="password"
        name="new-password"
        autoComplete="new-password"
        maxLength={PASSWORD_MAX_LENGTH}
        required
        value={password}
        onChange={setPassword}
        hint="At least 12 characters. A passphrase of several unrelated words is strong and easy to remember. Don't reuse a password from another site."
        error={fieldErrors.password}
      />
      <BrokerField
        id="broker-invite-confirm"
        label="Confirm new password"
        type="password"
        name="confirm-password"
        autoComplete="new-password"
        maxLength={PASSWORD_MAX_LENGTH}
        required
        value={confirm}
        onChange={setConfirm}
        error={fieldErrors.confirm}
      />
      {error && <p role="alert" className="broker-login__alert">{error}</p>}
      <Button type="submit" className="broker-login__submit" loading={pending} disabled={pending}>
        <ArrowRight size={18} className="broker-login__arrow" aria-hidden="true" />
        {pending ? "Setting password…" : "Set password and continue"}
      </Button>
      <p className="broker-login__hint">
        Next, you&apos;ll set up two-factor verification with an authenticator app. It is required for password sign-in.
      </p>
    </form>
  );
}
