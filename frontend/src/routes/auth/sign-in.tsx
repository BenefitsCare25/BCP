import { useState } from "react";
import { LogIn } from "lucide-react";
import { Button } from "@/components/ui/button";
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
    <div className="flex h-screen w-screen items-center justify-center bg-background">
      <div className="flex w-[360px] flex-col items-center gap-4 rounded-xl border border-border bg-card p-8 text-center shadow-sm">
        <div className="text-lg font-semibold text-foreground">Inspro</div>
        <p className="text-sm text-muted-foreground">
          {ENTRA_ENABLED
            ? "Sign in with your Microsoft work account to continue."
            : "Authentication is not configured for this build."}
        </p>
        <Button
          onClick={() => void handleSignIn()}
          disabled={!ENTRA_ENABLED || submitting}
          className="w-full"
        >
          <LogIn className="size-4" />
          {submitting ? "Redirecting…" : "Sign in with Microsoft"}
        </Button>
        {error && (
          <p className="text-xs text-error">
            Sign-in failed: {error} — try again.
          </p>
        )}
      </div>
    </div>
  );
}
