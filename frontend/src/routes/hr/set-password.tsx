/** HR set / reset password: redeem a single-use token (from the emailed link
 * or a forced-rotation redirect) and choose a password. */
import { useMemo, useState } from "react";
import { useNavigate } from "@tanstack/react-router";
import { Lock, ShieldCheck } from "lucide-react";
import { adoptSession, useHrSetPassword } from "@/api/hr";
import { formatError } from "@/lib/errors";
import { AuthScene } from "@/components/auth/AuthScene";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";

const MIN_LENGTH = 12;

function strength(pw: string): { label: string; ok: boolean } {
  if (pw.length < MIN_LENGTH) return { label: `At least ${MIN_LENGTH} characters`, ok: false };
  let classes = 0;
  if (/[a-z]/.test(pw)) classes++;
  if (/[A-Z]/.test(pw)) classes++;
  if (/\d/.test(pw)) classes++;
  if (/[^A-Za-z0-9]/.test(pw)) classes++;
  if (classes < 3) return { label: "Mix letters, numbers & symbols", ok: false };
  return { label: "Looks good", ok: true };
}

export function HrSetPasswordPage() {
  const navigate = useNavigate();
  const setPw = useHrSetPassword();

  const token = useMemo(
    () => new URLSearchParams(window.location.search).get("token") ?? "",
    [],
  );
  const [password, setPassword] = useState("");
  const [confirm, setConfirm] = useState("");
  const [error, setError] = useState<string | null>(null);

  const st = strength(password);
  const match = password.length > 0 && password === confirm;
  const canSubmit = st.ok && match && !!token;

  const submit = (e: React.FormEvent) => {
    e.preventDefault();
    setError(null);
    if (!token) {
      setError("This link is missing its token. Ask your administrator to resend it.");
      return;
    }
    setPw.mutate(
      { token, password },
      {
        onSuccess: (data) => {
          adoptSession(data);
          void navigate({ to: "/hr/dashboard" });
        },
        onError: (err) => setError(formatError(err)),
      },
    );
  };

  return (
    <AuthScene
      eyebrow="HR administration"
      title="Set your password"
      subtitle="Choose a strong password to finish setting up your HR account."
    >
      <form onSubmit={submit} className="space-y-4">
        <div className="space-y-1.5">
          <Label
            htmlFor="hr-new-password"
            className="text-xs font-semibold uppercase tracking-wide text-muted-foreground"
          >
            New password
          </Label>
          <div className="relative">
            <Lock className="pointer-events-none absolute left-3.5 top-1/2 size-[18px] -translate-y-1/2 text-muted-foreground" />
            <Input
              id="hr-new-password"
              type="password"
              autoComplete="new-password"
              placeholder="••••••••••••"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              autoFocus
              className="h-12 pl-11"
            />
          </div>
          {password.length > 0 && (
            <p className={st.ok ? "text-xs text-good" : "text-xs text-muted-foreground"}>
              {st.label}
            </p>
          )}
        </div>
        <div className="space-y-1.5">
          <Label
            htmlFor="hr-confirm-password"
            className="text-xs font-semibold uppercase tracking-wide text-muted-foreground"
          >
            Confirm password
          </Label>
          <div className="relative">
            <Lock className="pointer-events-none absolute left-3.5 top-1/2 size-[18px] -translate-y-1/2 text-muted-foreground" />
            <Input
              id="hr-confirm-password"
              type="password"
              autoComplete="new-password"
              placeholder="••••••••••••"
              value={confirm}
              onChange={(e) => setConfirm(e.target.value)}
              className="h-12 pl-11"
            />
          </div>
          {confirm.length > 0 && !match && (
            <p className="text-xs text-error">Passwords don't match</p>
          )}
        </div>
        {error && <p className="text-sm text-error">{error}</p>}
        <Button
          type="submit"
          className="h-12 w-full text-[15px] transition-transform duration-150 active:scale-[0.99]"
          disabled={setPw.isPending || !canSubmit}
        >
          <ShieldCheck className="size-[18px]" />
          {setPw.isPending ? "Saving…" : "Set password & sign in"}
        </Button>
      </form>
    </AuthScene>
  );
}
