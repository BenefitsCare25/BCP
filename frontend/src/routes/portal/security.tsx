/** Member account security — self-service TOTP two-factor enrolment / disable.
 * Mirrors the HR security page (routes/hr/security.tsx) on the portal surface. */
import { useState } from "react";
import { QRCodeSVG } from "qrcode.react";
import { toast } from "sonner";
import { Check, Copy, Loader2, ShieldCheck, ShieldOff } from "lucide-react";
import {
  useMemberMfaDisable,
  useMemberMfaEnrollConfirm,
  useMemberMfaEnrollStart,
  useMemberSecurityStatus,
  type MemberMfaStart,
} from "@/api/portal";
import { formatError } from "@/lib/errors";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";

function RecoveryCodes({ codes, onDone }: { codes: string[]; onDone: () => void }) {
  const copy = async () => {
    try {
      await navigator.clipboard.writeText(codes.join("\n"));
      toast.success("Recovery codes copied");
    } catch {
      toast.error("Couldn't copy — select and copy manually.");
    }
  };
  return (
    <div className="space-y-3">
      <div className="rounded-md border border-good/40 bg-good/5 p-3">
        <p className="flex items-center gap-2 text-sm font-medium text-foreground">
          <Check className="size-4 text-good" /> Two-factor authentication is on.
        </p>
      </div>
      <div>
        <p className="mb-1 text-sm font-medium text-foreground">
          Save your recovery codes
        </p>
        <p className="mb-2 text-xs text-muted-foreground">
          Each code works once if you lose your authenticator. Store them somewhere
          safe — they won't be shown again.
        </p>
        <div className="grid grid-cols-2 gap-2 rounded-md border border-border bg-muted p-3 font-mono text-sm">
          {codes.map((c) => (
            <span key={c}>{c}</span>
          ))}
        </div>
        <div className="mt-2 flex gap-2">
          <Button size="sm" variant="outline" onClick={copy}>
            <Copy className="size-3.5" /> Copy codes
          </Button>
          <Button size="sm" onClick={onDone}>
            I've saved them
          </Button>
        </div>
      </div>
    </div>
  );
}

function EnrollFlow({ onEnrolled }: { onEnrolled: (codes: string[]) => void }) {
  const start = useMemberMfaEnrollStart();
  const confirm = useMemberMfaEnrollConfirm();
  const [setup, setSetup] = useState<MemberMfaStart | null>(null);
  const [code, setCode] = useState("");
  const [error, setError] = useState<string | null>(null);

  const begin = () => {
    setError(null);
    start.mutate(undefined, {
      onSuccess: (data) => setSetup(data),
      onError: (e) => setError(formatError(e)),
    });
  };

  const doConfirm = () => {
    setError(null);
    confirm.mutate(code.trim(), {
      // Hand the codes UP — they must outlive this component. See the note in
      // the page component below.
      onSuccess: (data) => onEnrolled(data.recovery_codes),
      onError: () => setError("That code didn't match — check your app and try again."),
    });
  };

  if (!setup) {
    return (
      <div className="space-y-3">
        <p className="text-sm text-muted-foreground">
          Protect your account with a time-based code from an authenticator app
          (Google Authenticator, 1Password, Authy…).
        </p>
        {error && <p className="text-sm text-error">{error}</p>}
        <Button onClick={begin} disabled={start.isPending}>
          {start.isPending ? <Loader2 className="size-4 animate-spin" /> : null}
          Begin setup
        </Button>
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
            Scan this QR code with your authenticator app, or enter the key
            manually:
          </p>
          <code className="block break-all rounded bg-muted px-2 py-1 text-xs">
            {setup.secret}
          </code>
        </div>
      </div>
      <div className="space-y-1.5 sm:max-w-xs">
        <Label htmlFor="portal-enroll-code">Enter the 6-digit code</Label>
        <Input
          id="portal-enroll-code"
          inputMode="numeric"
          autoComplete="one-time-code"
          placeholder="123456"
          maxLength={6}
          value={code}
          onChange={(e) => setCode(e.target.value.replace(/\D/g, ""))}
          className="h-11 text-center text-lg font-semibold tracking-[0.4em]"
        />
      </div>
      {error && <p className="text-sm text-error">{error}</p>}
      <Button onClick={doConfirm} disabled={confirm.isPending || code.length < 6}>
        {confirm.isPending ? <Loader2 className="size-4 animate-spin" /> : null}
        Confirm & turn on
      </Button>
    </div>
  );
}

function DisablePanel({ onDisabled }: { onDisabled: () => void }) {
  const disable = useMemberMfaDisable();
  const [password, setPassword] = useState("");
  const [open, setOpen] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const submit = () => {
    setError(null);
    disable.mutate(password, {
      onSuccess: () => {
        toast.success("Two-factor authentication disabled");
        setPassword("");
        setOpen(false);
        onDisabled();
      },
      onError: () => setError("Password incorrect."),
    });
  };

  return (
    <div className="space-y-3">
      <div className="flex items-center gap-2">
        <Badge variant="good">On</Badge>
        <span className="text-sm text-muted-foreground">
          Your account is protected with an authenticator app.
        </span>
      </div>
      {!open ? (
        <Button variant="outline" onClick={() => setOpen(true)}>
          <ShieldOff className="size-4" /> Turn off two-factor
        </Button>
      ) : (
        <div className="space-y-2 sm:max-w-xs">
          <Label htmlFor="portal-disable-pw">
            Confirm your password to turn it off
          </Label>
          <Input
            id="portal-disable-pw"
            type="password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            autoFocus
          />
          {error && <p className="text-sm text-error">{error}</p>}
          <div className="flex gap-2">
            <Button
              variant="outline"
              onClick={submit}
              disabled={disable.isPending || !password}
            >
              {disable.isPending ? <Loader2 className="size-4 animate-spin" /> : null}
              Turn off
            </Button>
            <Button variant="ghost" onClick={() => setOpen(false)}>
              Cancel
            </Button>
          </div>
        </div>
      )}
    </div>
  );
}

export function PortalSecurityPage() {
  const { data, refetch } = useMemberSecurityStatus();
  const enrolled = data?.mfa_status === "confirmed";
  const available = data?.mfa_available ?? false;
  // Held HERE, not inside EnrollFlow. Recovery codes are shown exactly once
  // (stored hashed) and this query refetches on window focus — so alt-tabbing to
  // a password manager to save them flipped `enrolled`, unmounted EnrollFlow and
  // destroyed the codes. At page level they survive the refetch.
  const [recovery, setRecovery] = useState<string[] | null>(null);

  return (
    <div className="mx-auto max-w-2xl space-y-6">
      <div>
        <h1 className="text-2xl font-semibold tracking-tight text-foreground">
          Security
        </h1>
        <p className="mt-1 text-sm text-muted-foreground">
          Manage two-factor authentication for your account.
        </p>
      </div>
      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2 text-base">
            <ShieldCheck
              className={enrolled ? "size-5 text-good" : "size-5 text-muted-foreground"}
            />
            Two-factor authentication
          </CardTitle>
          <CardDescription>
            A one-time code from your phone, required at each sign-in.
          </CardDescription>
        </CardHeader>
        <CardContent>
          {recovery ? (
            <RecoveryCodes
              codes={recovery}
              onDone={() => {
                setRecovery(null);
                void refetch();
              }}
            />
          ) : enrolled ? (
            <DisablePanel onDisabled={() => void refetch()} />
          ) : available ? (
            <EnrollFlow onEnrolled={setRecovery} />
          ) : (
            <p className="text-sm text-muted-foreground">
              Your company hasn't enabled two-factor authentication for the
              employee portal. Contact your HR team if you'd like it turned on.
            </p>
          )}
        </CardContent>
      </Card>
    </div>
  );
}
