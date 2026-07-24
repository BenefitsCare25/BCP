import { useState } from "react";
import { LogIn } from "lucide-react";
import { Button } from "@/components/ui/button";
import { AuthScene } from "@/components/auth/AuthScene";
import { ENTRA_ENABLED, signIn } from "@/auth/msal";
import { formatError } from "@/lib/errors";

/**
 * Visible sign-in page. The root guard sends users here when Entra is enabled
 * and no account is active; a sibling `beforeLoad` in the route definition
 * bounces signed-in users back to / so this page never flashes.
 */
export function SignInPage() {
  // signIn() triggers a full-page redirect; the local flag exists only to
  // disable the button between click and navigate. If the redirect never
  // happens (signIn rejected), re-enable the button and show why.
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const handleSignIn = async () => {
    setSubmitting(true);
    setError(null);
    try {
      await signIn();
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
      <Button
        onClick={() => void handleSignIn()}
        disabled={!ENTRA_ENABLED || submitting}
        className="h-12 w-full text-[15px] transition-transform duration-150 active:scale-[0.99]"
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
