import { useState } from "react";
import { useNavigate } from "@tanstack/react-router";
import { LogOut, RefreshCw } from "lucide-react";
import { Button } from "@/components/ui/button";
import { AuthScene } from "@/components/auth/AuthScene";
import { getActiveAccount, signOut } from "@/auth/msal";
import { queryClient } from "@/lib/queryClient";
import { formatError } from "@/lib/errors";

/**
 * Terminal state for an account Microsoft authenticated but the platform does
 * not recognise. Deliberately makes NO API calls — it is the destination for a
 * 403, so a request here would just bounce back. The only ways forward are
 * signing out (to try another account) or re-checking after an administrator
 * has added the user.
 */
export function NoAccessPage() {
  const navigate = useNavigate();
  const [busy, setBusy] = useState<"out" | "retry" | null>(null);
  const [error, setError] = useState<string | null>(null);
  const email = getActiveAccount()?.username ?? null;

  const handleSignOut = async () => {
    setBusy("out");
    setError(null);
    try {
      await signOut();
    } catch (err) {
      setBusy(null);
      setError(formatError(err));
    }
  };

  const handleRetry = async () => {
    setBusy("retry");
    // Drop the cached identity so the app-shell guard asks the server again.
    queryClient.removeQueries({ queryKey: ["me"] });
    await navigate({ to: "/" });
    setBusy(null);
  };

  return (
    <AuthScene
      eyebrow="Access required"
      title="You don't have access yet"
      subtitle={
        email
          ? `${email} signed in successfully, but it isn't on this platform's user list. An administrator has to add the account before you can continue.`
          : "This account isn't on the platform's user list. An administrator has to add it before you can continue."
      }
    >
      <div className="flex flex-col gap-2.5">
        <Button
          onClick={() => void handleRetry()}
          disabled={busy !== null}
          variant="outline"
          className="h-12 w-full text-[15px]"
        >
          <RefreshCw className="size-[18px]" />
          {busy === "retry" ? "Checking…" : "I've been added — check again"}
        </Button>
        <Button
          onClick={() => void handleSignOut()}
          disabled={busy !== null}
          className="h-12 w-full text-[15px]"
        >
          <LogOut className="size-[18px]" />
          {busy === "out" ? "Signing out…" : "Sign out and use another account"}
        </Button>
      </div>
      {error && (
        <p className="mt-3 text-center text-sm text-error">
          Sign-out failed: {error} — try again.
        </p>
      )}
    </AuthScene>
  );
}
