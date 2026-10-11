import { useEffect } from "react";
import { useNavigate } from "@tanstack/react-router";
import { needsTwoFactor } from "@/auth/brokerSession";
import { initializeBrokerSignIn } from "@/auth/msal";
import { takeReturnPath } from "@/auth/returnPath";
import { useBrokerSession } from "@/stores/brokerSession";

/**
 * `initializeBrokerSignIn()` runs at app boot (see main.tsx) so by the time this
 * component renders the redirect response has typically been consumed. We
 * still await it here as a safety net for direct navigations to /auth/callback.
 */
export function AuthCallbackPage() {
  const navigate = useNavigate();

  useEffect(() => {
    initializeBrokerSignIn()
      .then(() => {
        // The page the visitor was heading for waits behind two-factor setup.
        const session = useBrokerSession.getState().session;
        return session && needsTwoFactor(session)
          ? navigate({ to: "/broker/security", replace: true })
          : navigate({ href: takeReturnPath(), replace: true });
      })
      .catch(() => {
        // A failed redirect handshake would otherwise strand the user on a
        // permanent "Signing you in…" — send them back to sign-in to retry.
        void navigate({ to: "/sign-in", replace: true });
      });
  }, [navigate]);

  return (
    <div className="flex h-screen w-screen items-center justify-center bg-background">
      <div className="text-center">
        <div className="text-base font-medium text-foreground">Signing you in…</div>
        <div className="mt-1 text-sm text-muted-foreground">
          Completing Microsoft Entra sign-in
        </div>
      </div>
    </div>
  );
}
