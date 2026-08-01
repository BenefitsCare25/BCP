/** "Two-step sign-in" — self-service TOTP enrolment and removal.
 *
 * Three things here are deliberate:
 *
 * 1. **No page heading.** The shell owns the h1 (whose record this is); this
 *    page's one mount is an h2 beneath it. It previously opened a second h1 and
 *    then skipped a level under it.
 * 2. **A wrong code and a broken server are different failures.** Both branches
 *    used to discard the error and print "that code didn't match", so a 500, a
 *    rate limit or a dropped connection told the member to check their
 *    authenticator app. Only a 401 is a credential answer; everything else
 *    surfaces the server's own words.
 * 3. **The recovery codes are held by the PAGE, not the flow that produced
 *    them.** They are shown exactly once (stored hashed) and this query
 *    refetches on window focus — so alt-tabbing to a password manager to save
 *    them flipped `enrolled`, unmounted the enrol flow and destroyed the codes. */
import { useState } from "react";
import { QRCodeSVG } from "qrcode.react";
import { toast } from "sonner";
import { Copy, Loader2 } from "lucide-react";
import {
  useMemberMfaDisable,
  useMemberMfaEnrollConfirm,
  useMemberMfaEnrollStart,
  useMemberSecurityStatus,
  type MemberMfaStart,
} from "@/api/portal";
import { Field, FormAlert, leafControl } from "@/components/portal/leaf/Field";
import { Mount, MountRule } from "@/components/portal/leaf/Mount";
import { Strike } from "@/components/portal/leaf/Strike";
import { actionClass } from "@/components/portal/leaf/Action";
import { errorStatus, formatError } from "@/lib/errors";
import { cn } from "@/lib/cn";
import { useDocumentTitle } from "@/lib/useDocumentTitle";

/** The shared leaf actions — this page used to carry its own copy of both class
 * strings. `primaryAction` is the ONE brand fill on whichever step is on
 * screen: turning two-step sign-in on, confirming it, or changing a password. */
const action = actionClass("quiet", { className: "px-4" });
const primaryAction = actionClass("primary", { className: "px-4" });

/** A credential answer (401) keeps the specific, reassuring wording. Anything
 * else is the server or the network failing, and saying "wrong code" to that
 * sends the member off to re-check an app that was never the problem. */
function credentialError(error: unknown, wrongCredential: string): string {
  return errorStatus(error) === 401 ? wrongCredential : formatError(error);
}

function RecoveryCodes({
  codes,
  onDone,
}: {
  codes: string[];
  onDone: () => void;
}) {
  const copy = async () => {
    try {
      await navigator.clipboard.writeText(codes.join("\n"));
      toast.success("Recovery codes copied");
    } catch {
      toast.error("Couldn't copy — select and copy them manually.");
    }
  };
  return (
    <div className="space-y-3">
      <p className="text-row text-record">
        Two-step sign-in is on. Save these codes somewhere safe — each one works
        once if you lose your phone, and they won't be shown again.
      </p>
      {/* One column on a phone. Two columns of monospace codes measured ~150px
          each at 390px, which is where they started wrapping mid-code. */}
      <div className="grid grid-cols-1 gap-x-6 gap-y-1 rounded-control border border-hairline/75 p-3 font-mono text-row text-record sm:grid-cols-2">
        {codes.map((c) => (
          <span key={c} className="select-all">
            {c}
          </span>
        ))}
      </div>
      <div className="flex flex-col gap-2 sm:flex-row">
        <button type="button" onClick={() => void copy()} className={action}>
          <Copy className="size-4" aria-hidden />
          Copy codes
        </button>
        <button type="button" onClick={onDone} className={primaryAction}>
          I've saved them
        </button>
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
      onSuccess: setSetup,
      onError: (e) => setError(formatError(e)),
    });
  };

  const doConfirm = () => {
    setError(null);
    confirm.mutate(code.trim(), {
      // Hand the codes UP — they must outlive this component (see the file
      // header).
      onSuccess: (data) => onEnrolled(data.recovery_codes),
      onError: (e) =>
        setError(
          credentialError(
            e,
            "That code didn't match — check your app and try again.",
          ),
        ),
    });
  };

  if (!setup) {
    return (
      <div className="space-y-3">
        <p className="text-row text-label">
          You'll need an authenticator app on your phone — Google
          Authenticator, 1Password, Authy or similar.
        </p>
        {error && <FormAlert>{error}</FormAlert>}
        <button
          type="button"
          onClick={begin}
          disabled={start.isPending}
          className={primaryAction}
        >
          {start.isPending && (
            <Loader2 className="size-4 animate-spin" aria-hidden />
          )}
          Start setup
        </button>
      </div>
    );
  }

  return (
    <div className="space-y-4">
      <div className="flex flex-col gap-4 sm:flex-row sm:items-start">
        {/* SOLID `bg-bar`, and the one place on this surface that refuses the
            glass: a QR code needs an opaque light quiet zone with real contrast
            to scan, and translucency over a coloured ground is exactly what
            makes a phone camera fail on it. Square corners for the same reason
            — the quiet zone is part of the code. */}
        <div className="shrink-0 rounded-control border border-hairline/75 bg-bar p-3">
          <QRCodeSVG value={setup.otpauth_uri} size={148} />
        </div>
        <div className="min-w-0 space-y-2">
          <p className="text-row text-label">
            Scan this with your authenticator app, or type this key into it:
          </p>
          <code className="block select-all break-all rounded-control border border-hairline/75 px-2 py-1 font-mono text-row text-record">
            {setup.secret}
          </code>
        </div>
      </div>
      <Field label="The 6-digit code from your app" required error={error}>
        {(props) => (
          <input
            {...props}
            inputMode="numeric"
            autoComplete="one-time-code"
            placeholder="123456"
            maxLength={6}
            value={code}
            onChange={(e) => setCode(e.target.value.replace(/\D/g, ""))}
            className={cn(
              leafControl,
              "text-center text-lg font-semibold tracking-[0.4em] sm:max-w-56 sm:text-lg",
            )}
          />
        )}
      </Field>
      <button
        type="button"
        onClick={doConfirm}
        disabled={confirm.isPending || code.length < 6}
        className={primaryAction}
      >
        {confirm.isPending && (
          <Loader2 className="size-4 animate-spin" aria-hidden />
        )}
        Turn on two-step sign-in
      </button>
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
        toast.success("Two-step sign-in turned off");
        setPassword("");
        setOpen(false);
        onDisabled();
      },
      onError: (e) =>
        setError(credentialError(e, "That password wasn't right.")),
    });
  };

  if (!open) {
    return (
      <div className="space-y-3">
        <p className="text-row text-label">
          Your account asks for a code from your authenticator app each time you
          sign in.
        </p>
        <button
          type="button"
          onClick={() => setOpen(true)}
          className={action}
        >
          Turn it off
        </button>
      </div>
    );
  }

  return (
    <div className="space-y-3">
      <Field
        label="Confirm your password to turn it off"
        required
        error={error}
      >
        {(props) => (
          <input
            {...props}
            type="password"
            autoComplete="current-password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            autoFocus
            className={cn(leafControl, "sm:max-w-72")}
          />
        )}
      </Field>
      <div className="flex flex-col gap-2 sm:flex-row">
        <button
          type="button"
          onClick={submit}
          disabled={disable.isPending || !password}
          className={action}
        >
          {disable.isPending && (
            <Loader2 className="size-4 animate-spin" aria-hidden />
          )}
          Turn it off
        </button>
        <button
          type="button"
          onClick={() => {
            setOpen(false);
            setError(null);
          }}
          className={action}
        >
          Keep it on
        </button>
      </div>
    </div>
  );
}

export function PortalSecurityPage() {
  useDocumentTitle("Two-step sign-in");
  const { data, refetch } = useMemberSecurityStatus();
  const enrolled = data?.mfa_status === "confirmed";
  const available = data?.mfa_available ?? false;
  const [recovery, setRecovery] = useState<string[] | null>(null);

  return (
    <Mount
      label="Two-step sign-in"
      gloss="A code from your phone, asked for alongside your password."
      aside={
        enrolled && !recovery ? <Strike tone="approved">On</Strike> : undefined
      }
    >
      <MountRule className="mb-4" />
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
        <p className="text-row text-label">
          Your company hasn't switched this on for the employee portal. Your HR
          team can turn it on if you'd like it.
        </p>
      )}
    </Mount>
  );
}
