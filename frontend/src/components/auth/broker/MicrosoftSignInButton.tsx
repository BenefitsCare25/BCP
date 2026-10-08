import { useRef, useState } from "react";
import { ArrowRight } from "lucide-react";
import { Button } from "@/components/ui/button";
import { signIn } from "@/auth/msal";
import { formatError } from "@/lib/errors";

/** Starts the Microsoft redirect. `signIn()` navigates the page away, so the
 *  pending state only covers the gap before it does; if the redirect never
 *  starts, the button comes back with the reason. A second primary action on
 *  the page (the password form) makes this one secondary. */
export function MicrosoftSignInButton({
  label = "Sign in with Microsoft",
  selectAccount = false,
  secondary = false,
}: {
  label?: string;
  /** Show Microsoft's account picker instead of reusing the browser's session. */
  selectAccount?: boolean;
  secondary?: boolean;
}) {
  const [submitting, setSubmitting] = useState(false);
  const submittingRef = useRef(false);
  const [error, setError] = useState<string | null>(null);

  const start = async () => {
    if (submittingRef.current) return;
    submittingRef.current = true;
    setSubmitting(true);
    setError(null);
    try {
      await signIn({ selectAccount });
    } catch (err) {
      submittingRef.current = false;
      setSubmitting(false);
      setError(formatError(err));
    }
  };

  return (
    <>
      <Button
        type="button"
        onClick={() => void start()}
        disabled={submitting}
        loading={submitting}
        className={secondary ? "broker-login__submit broker-login__submit--secondary" : "broker-login__submit"}
      >
        <ArrowRight size={18} className="broker-login__arrow" aria-hidden="true" />
        {submitting ? "Redirecting…" : label}
      </Button>
      {error && (
        <p role="alert" className="broker-login__alert broker-login__error">
          Sign-in failed: {error} — try again.
        </p>
      )}
    </>
  );
}
