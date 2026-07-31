import { useState } from "react";
import { useRouterState } from "@tanstack/react-router";
import { LogIn, ShieldAlert } from "lucide-react";
import { Button } from "@/components/ui/button";
import { AuthScene } from "@/components/auth/AuthScene";
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
  const [error, setError] = useState<string | null>(null);
  const denied = useRouterState({
    select: (s) => Boolean((s.location.search as { denied?: unknown }).denied),
  });

  const handleSignIn = async () => {
    setSubmitting(true);
    setError(null);
    try {
      // After a refusal, force the account picker: the browser still holds a
      // Microsoft session, so the default flow would silently sign the SAME
      // rejected account back in and they could never switch.
      await signIn({ selectAccount: denied });
    } catch (err) {
      setSubmitting(false);
      setError(formatError(err));
    }
  };

  return (
    <AuthScene
      eyebrow="Broker workspace"
      title="Sign in"
      subtitle={
        ENTRA_ENABLED
          ? "Sign in with your Microsoft work account."
          : "Authentication is not configured for this build."
      }
    >
      {denied && (
        <div
          role="alert"
          className="mb-4 flex items-start gap-2.5 rounded-md border border-error/30 bg-error-soft px-3 py-2.5"
        >
          <ShieldAlert className="mt-0.5 size-4 shrink-0 text-error" />
          <p className="text-sm leading-relaxed text-foreground">
            Account no access. Contact your administrator.
          </p>
        </div>
      )}
      <Button
        onClick={() => void handleSignIn()}
        disabled={!ENTRA_ENABLED || submitting}
        className="h-12 w-full text-md transition-transform duration-150 active:scale-[0.99]"
      >
        <LogIn className="size-[18px]" />
        {submitting ? "Redirecting…" : "Sign in with Microsoft"}
      </Button>
      {error && (
        <p className="mt-3 text-center text-sm text-error">
          Sign-in failed: {error} — try again.
        </p>
      )}
    </AuthScene>
  );
}
