import { usePortalTranslation } from "@/i18n/portal";
/** HR account security — TOTP two-factor enrolment / disable. */
import { useState } from "react";
import { QRCodeSVG } from "qrcode.react";
import { toast } from "sonner";
import { Check, Copy, Loader2, ShieldCheck, ShieldOff } from "lucide-react";
import {
  useHrMe,
  useHrMfaDisable,
  useHrMfaEnrollConfirm,
  useHrMfaEnrollStart,
  type MfaStart,
} from "@/api/hr";
import { errorCode, errorStatus, formatError } from "@/lib/errors";
import { PASSWORD_MAX_LENGTH } from "@/lib/loginValidation";
import { useNavigate } from "@tanstack/react-router";
import { refreshHrSession } from "@/api/hrClient";
import { useHrSession } from "@/stores/hrSession";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
} from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Skeleton } from "@/components/ui/skeleton";

function RecoveryCodes({ codes, onDone }: { codes: string[]; onDone: () => void }) {
  const pt = usePortalTranslation();
  const copy = async () => {
    try {
      await navigator.clipboard.writeText(codes.join("\n"));
      toast.success(pt("Recovery codes copied"));
    } catch {
      toast.error(pt("Couldn't copy — select and copy manually."));
    }
  };
  return (
    <div className="space-y-3">
      <div className="rounded-md border border-good/40 bg-good/5 p-3">
        <p className="flex items-center gap-2 text-sm font-medium text-foreground">
          <Check className="size-4 text-good" />  {pt("Two-factor authentication is on.")} </p>
      </div>
      <div>
        <p className="mb-1 text-sm font-medium text-foreground">
          {pt("Save your recovery codes")} </p>
        <p className="mb-2 text-xs text-muted-foreground">
          {pt("Each code works once if you lose your authenticator. Store them somewhere safe — they won't be shown again.")} </p>
        <div className="grid grid-cols-1 gap-2 rounded-md border border-border bg-muted p-3 font-mono text-sm sm:grid-cols-2">
          {codes.map((c) => (
            <span className="break-all" key={c}>{c}</span>
          ))}
        </div>
        <div className="mt-2 flex gap-2">
          <Button size="sm" variant="outline" onClick={copy}>
            <Copy className="size-3.5" />  {pt("Copy codes")} </Button>
          <Button size="sm" onClick={onDone}>
            {pt("I've saved them")} </Button>
        </div>
      </div>
    </div>
  );
}

/** Asked only when the server wants it (`reauth_required`): a sign-in that is
 * no longer recent must prove the password before an authenticator is bound.
 * The mandatory setup straight after sign-in never reaches this. */
function ReauthForm({
  pending,
  error,
  onSubmit,
  onCancel,
}: {
  pending: boolean;
  error: string | null;
  onSubmit: (password: string) => void;
  onCancel: () => void;
}) {
  const pt = usePortalTranslation();
  const [password, setPassword] = useState("");
  return (
    <form
      className="space-y-3 sm:max-w-xs"
      onSubmit={(e) => {
        e.preventDefault();
        if (password && !pending) onSubmit(password);
      }}
    >
      <div className="space-y-1.5">
        <Label htmlFor="hr-reauth-pw">{pt("Confirm your password to set up two-factor")}</Label>
        <Input
          id="hr-reauth-pw"
          type="password"
          autoComplete="current-password"
          maxLength={PASSWORD_MAX_LENGTH}
          value={password}
          onChange={(e) => setPassword(e.target.value)}
          aria-invalid={!!error}
          aria-describedby={error ? "hr-reauth-error" : undefined}
          autoFocus
        />
        {error && <p id="hr-reauth-error" role="alert" className="text-sm text-error">{pt(error)}</p>}
      </div>
      <div className="flex gap-2">
        <Button type="submit" disabled={pending || !password}>
          {pending ? <Loader2 className="size-4 animate-spin" /> : null}
          {pt("Continue")} </Button>
        <Button type="button" variant="ghost" onClick={onCancel} disabled={pending}>
          {pt("Cancel")} </Button>
      </div>
    </form>
  );
}

function EnrollFlow({ onEnrolled }: { onEnrolled: (codes: string[]) => void }) {
  const pt = usePortalTranslation();
  const start = useHrMfaEnrollStart();
  const confirm = useHrMfaEnrollConfirm();
  const [setup, setSetup] = useState<MfaStart | null>(null);
  const [code, setCode] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [reauth, setReauth] = useState(false);

  const begin = (currentPassword?: string) => {
    setError(null);
    start.mutate(currentPassword, {
      onSuccess: (data) => {
        setReauth(false);
        setSetup(data);
      },
      onError: (e) => {
        const reauthRequired = errorCode(e) === "reauth_required";
        if (reauthRequired && !currentPassword) {
          setReauth(true);
          return;
        }
        // With a password: a 401, or being asked again, means it was wrong.
        // Anything else (lockout, outage) keeps the server's own words.
        const wrongPassword =
          !!currentPassword && (reauthRequired || errorStatus(e) === 401);
        setError(wrongPassword ? "That password wasn't right." : formatError(e));
      },
    });
  };

  const doConfirm = () => {
    setError(null);
    confirm.mutate(code.trim(), {
      // Hand the codes UP: they are shown once and stored hashed, so they must
      // not live in this component's state — see the note in HrSecurityPage.
      onSuccess: (data) => onEnrolled(data.recovery_codes),
      onError: (e) => setError(errorStatus(e) === 401
        ? "That code didn't match - check your app and try again."
        : formatError(e)),
    });
  };

  if (reauth && !setup) {
    return (
      <ReauthForm
        pending={start.isPending}
        error={pt(error)}
        onSubmit={(password) => begin(password)}
        onCancel={() => {
          setReauth(false);
          setError(null);
        }}
      />
    );
  }

  if (!setup) {
    return (
      <div className="space-y-3">
        <p className="text-sm text-muted-foreground">
          {pt("Protect your account with a time-based code from an authenticator app (Google Authenticator, 1Password, Authy…).")} </p>
        {error && <p className="text-sm text-error">{pt(error)}</p>}
        <Button onClick={() => begin()} disabled={start.isPending}>
          {start.isPending ? <Loader2 className="size-4 animate-spin" /> : null}
          {pt("Begin setup")} </Button>
      </div>
    );
  }

  return (
    <div className="space-y-4">
      <div className="flex flex-col gap-4 sm:flex-row sm:items-start">
        <div className="rounded-lg border border-border bg-white p-3">
          <QRCodeSVG value={setup.otpauth_uri} size={148} />
        </div>
        <div className="space-y-2 text-sm">
          <p className="text-muted-foreground">
            {pt("Scan this QR code with your authenticator app, or enter the key manually:")} </p>
          <code className="block break-all rounded bg-muted px-2 py-1 text-xs">
            {setup.secret}
          </code>
        </div>
      </div>
      <div className="space-y-1.5 sm:max-w-xs">
        <Label htmlFor="hr-enroll-code">{pt("Enter the 6-digit code")}</Label>
        <Input
          id="hr-enroll-code"
          inputMode="numeric"
          autoComplete="one-time-code"
          placeholder="123456"
          maxLength={6}
          value={code}
          onChange={(e) => setCode(e.target.value.replace(/\D/g, ""))}
          className="h-11 text-center text-lg font-semibold tracking-[0.4em]"
        />
      </div>
      {error && <p className="text-sm text-error">{pt(error)}</p>}
      <Button onClick={doConfirm} disabled={confirm.isPending || code.length < 6}>
        {confirm.isPending ? <Loader2 className="size-4 animate-spin" /> : null}
        {pt("Confirm & turn on")} </Button>
    </div>
  );
}

function DisablePanel() {
  const pt = usePortalTranslation();
  const disable = useHrMfaDisable();
  const [password, setPassword] = useState("");
  const [open, setOpen] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const submit = () => {
    setError(null);
    disable.mutate(password, {
      onSuccess: () => {
        toast.success(pt("Two-factor authentication disabled"));
        setPassword("");
        setOpen(false);
      },
      onError: () => setError("Password incorrect."),
    });
  };

  return (
    <div className="space-y-3">
      <div className="flex items-center gap-2">
        <Badge variant="good">{pt("On")}</Badge>
        <span className="text-sm text-muted-foreground">
          {pt("Your account is protected with an authenticator app.")} </span>
      </div>
      {!open ? (
        <Button variant="outline" onClick={() => setOpen(true)}>
          <ShieldOff className="size-4" />  {pt("Turn off two-factor")} </Button>
      ) : (
        <div className="space-y-2 sm:max-w-xs">
          <Label htmlFor="hr-disable-pw">{pt("Confirm your password to turn it off")}</Label>
          <Input
            id="hr-disable-pw"
            type="password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            autoFocus
          />
          {error && <p className="text-sm text-error">{pt(error)}</p>}
          <div className="flex gap-2">
            <Button
              variant="outline"
              onClick={submit}
              disabled={disable.isPending || !password}
            >
              {disable.isPending ? <Loader2 className="size-4 animate-spin" /> : null}
              {pt("Turn off")} </Button>
            <Button variant="ghost" onClick={() => setOpen(false)}>
              {pt("Cancel")} </Button>
          </div>
        </div>
      )}
    </div>
  );
}

export function HrSecurityPage() {
  const pt = usePortalTranslation();
  const navigate = useNavigate();
  const { data: me, refetch, isLoading, isError, error } = useHrMe();
  const enrolled = me?.mfa_status === "confirmed";
  const available = me?.mfa_available ?? false;
  // Held HERE, not inside EnrollFlow. Recovery codes are shown exactly once
  // (stored hashed), and `useHrMe` refetches on window focus — so the moment the
  // user alt-tabbed to their password manager to save them, `enrolled` flipped
  // true, EnrollFlow unmounted and the codes were destroyed. At page level they
  // survive that refetch and are cleared only when the user confirms.
  const [recovery, setRecovery] = useState<string[] | null>(null);

  return (
    <div className="max-w-3xl">
      <h1 className="sr-only">{pt("Account security")}</h1>
      <Card>
        <CardHeader className="p-5 pb-3">
          <h2 className="flex items-center gap-2 text-xl font-bold">
            {enrolled ? (
              <ShieldCheck className="size-5 text-good" />
            ) : (
              <ShieldCheck className="size-5 text-muted-foreground" />
            )}
            {pt("Two-factor authentication")} </h2>
          <CardDescription>
            {pt("Use an authenticator app to add a code at sign-in.")} </CardDescription>
        </CardHeader>
        <CardContent className="px-5 pb-5">
          {recovery ? (
            <RecoveryCodes
              codes={recovery}
              onDone={() => {
                void refreshHrSession().then(async restored => {
                  if (!restored) { toast.error(pt("Sign in again to continue.")); return; }
                  setRecovery(null);
                  await refetch();
                  useHrSession.setState({ mfaRecoveryPending: false, mfaEnrollmentRequired: false });
                  void navigate({ to: "/hr/dashboard" });
                });
              }}
            />
          ) : isLoading ? (
            <div aria-label={pt("Loading account security")} role="status"><Skeleton className="h-16" /></div>
          ) : isError ? (
            <div role="alert" className="space-y-3">
              <p className="text-sm text-error">{pt(formatError(error))}</p>
              <Button variant="outline" onClick={() => void refetch()}>{pt("Try again")}</Button>
            </div>
          ) : enrolled ? (
            me?.mfa_required ? <p className="text-sm text-muted-foreground">{pt("Two-factor authentication is required by your company.")}</p> : <DisablePanel />
          ) : available ? (
            <EnrollFlow onEnrolled={codes => {
              useHrSession.setState({ mfaRecoveryPending: useHrSession.getState().mfaEnrollmentRequired });
              setRecovery(codes);
            }} />
          ) : (
            <p className="text-sm text-muted-foreground">
              {pt("Your company hasn't enabled two-factor authentication for the HR platform. Contact your broker if you'd like it turned on.")} </p>
          )}
        </CardContent>
      </Card>
    </div>
  );
}
