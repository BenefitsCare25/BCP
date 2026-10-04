import { useRef, useState } from "react";
import { useRouterState } from "@tanstack/react-router";
import { ArrowRight, ShieldAlert } from "lucide-react";
import { Button } from "@/components/ui/button";
import { BrokerLoginScene } from "@/components/auth/BrokerLoginScene";
import { ENTRA_ENABLED, signIn } from "@/auth/msal";
import { formatError } from "@/lib/errors";

/**
 * Visible sign-in page. The root guard sends users here when Entra is enabled
 * and no account is active; a sibling `beforeLoad` in the route definition
 * bounces signed-in users back to / so this page never flashes.
 *
 * `?denied=1` means the opposite happened: Microsoft authenticated them fine,
 * but the platform's user list grants them nothing, so they were bounced back
 * here. Saying so is the whole point — a silent return to the login screen
 * right after a successful sign-in reads as a bug, and they just retry.
 */
export function SignInPage() {
  // signIn() triggers a full-page redirect; the local flag exists only to
  // disable the button between click and navigate. If the redirect never
  // happens (signIn rejected), re-enable the button and show why.
  const [submitting, setSubmitting] = useState(false);
  const submittingRef = useRef(false);
  const [error, setError] = useState<string | null>(null);
  const denied = useRouterState({
    select: (s) => Boolean((s.location.search as { denied?: unknown }).denied),
  });

  const handleSignIn = async () => {
    if (submittingRef.current) return;
    submittingRef.current = true;
    setSubmitting(true);
    setError(null);
    try {
      // After a refusal, force the account picker: the browser still holds a
      // Microsoft session, so the default flow would silently sign the SAME
      // rejected account back in and they could never switch.
      await signIn({ selectAccount: denied });
    } catch (err) {
      submittingRef.current = false;
      setSubmitting(false);
      setError(formatError(err));
    }
  };

  return (
    <BrokerLoginScene>
      <p className="broker-login__explanation">
        {ENTRA_ENABLED
          ? "Continue with your Microsoft work account."
          : "Authentication is not configured for this build."}
      </p>
      {denied && (
        <div
          role="alert"
          className="broker-login__alert"
        >
          <ShieldAlert size={18} aria-hidden="true" />
          <p>
            This account does not have access. Contact your administrator or sign in with another Microsoft account.
          </p>
        </div>
      )}
      <Button
        onClick={() => void handleSignIn()}
        disabled={!ENTRA_ENABLED || submitting}
        loading={submitting}
        className="broker-login__submit"
      >
        <ArrowRight size={18} className="broker-login__arrow" aria-hidden="true" />
        {submitting ? "Redirecting…" : "Sign in with Microsoft"}
      </Button>
      {error && (
        <p role="alert" className="broker-login__alert broker-login__error">
          Sign-in failed: {error} — try again.
        </p>
      )}
    </BrokerLoginScene>
  );
}
