import { useEffect, useRef, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { useNavigate } from "@tanstack/react-router";
import { QRCodeSVG } from "qrcode.react";
import { brokerAccessToken, brokerAuthRequest, refreshBrokerSession } from "@/auth/brokerSession";
import { clearLocalSession, signOut } from "@/auth/msal";
import { useBrokerSession, type BrokerSession } from "@/stores/brokerSession";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";

async function requestMfa<T>(path: string, body?: unknown): Promise<T> {
  const token = await brokerAccessToken();
  if (!token) throw new Error("Session ended. Sign in again.");
  try {
    return await brokerAuthRequest<T>(path, body, token);
  } catch (error) {
    const status = (error as { status?: number }).status;
    // A wrong code also returns 401. Confirm session validity without replaying
    // the code: a valid session keeps inline feedback; an ended one is cleared.
    if (status === 401 || status === 403) await refreshBrokerSession();
    throw error;
  }
}

export function BrokerSecurityPage() {
  const navigate = useNavigate();
  const session = useBrokerSession((state) => state.session);
  const [setup, setSetup] = useState<{ secret: string; otpauth_uri: string } | null>(null);
  const [code, setCode] = useState("");
  const [codes, setCodes] = useState<string[] | null>(null);
  const [pending, setPending] = useState(false);
  const pendingRef = useRef(false);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    if (session) return;
    void clearLocalSession().then(() => navigate({ to: "/sign-in", replace: true }));
  }, [session, navigate]);
  const status = useQuery({
    queryKey: ["broker-mfa", session?.user.id],
    queryFn: () => requestMfa<{ status: string; verified: boolean }>("/mfa"),
    enabled: Boolean(session),
    meta: { localErrorHandling: true }, retry: false,
  });

  const run = async (operation: "start" | "confirm" | "verify" | "logout") => {
    if (pendingRef.current) return;
    pendingRef.current = true; setPending(true); setError(null);
    try {
      if (operation === "logout") { await signOut(); return; }
      if (operation === "start") {
        setSetup(await requestMfa("/mfa/start", {}));
      } else {
        const result = await requestMfa<BrokerSession & { recovery_codes?: string[] }>(
          `/mfa/${operation}`, { code },
        );
        const { recovery_codes, ...authenticated } = result;
        useBrokerSession.getState().set(authenticated);
        if (recovery_codes) setCodes(recovery_codes);
        setSetup(null); setCode("");
        if (!recovery_codes) await navigate({ to: "/", replace: true });
      }
    } catch (error) {
      setError(error instanceof Error ? error.message : "Verification failed. Try again.");
    } finally { pendingRef.current = false; setPending(false); }
  };

  const valid = setup ? /^\d{6}$/.test(code) : /^(?:\d{6}|[a-fA-F0-9]{4}-[a-fA-F0-9]{4}-[a-fA-F0-9]{4})$/.test(code);
  return <main className="flex min-h-dvh items-center justify-center bg-background p-4">
    <Card className="w-full max-w-lg">
      <CardHeader><h1 className="text-xl font-semibold">Secure your broker account</h1></CardHeader>
      <CardContent className="space-y-5">
        <p className="break-words text-sm text-muted-foreground">{session?.user.email}</p>
        <p className="text-sm">Two-factor verification is required to access the broker platform.</p>
        {status.isPending && <p role="status">Checking account security…</p>}
        {status.isError && <div role="alert"><p>Couldn't check account security.</p><Button variant="outline" onClick={() => void status.refetch()}>Try again</Button></div>}
        {codes ? <>
          <h2 className="font-semibold">Save your recovery codes</h2>
          <p className="text-sm">Each code works once if you lose your authenticator. Store them safely; they won't be shown again.</p>
          <div className="grid grid-cols-1 gap-2 rounded-md bg-muted p-3 font-mono sm:grid-cols-2">{codes.map(value => <span key={value}>{value}</span>)}</div>
          <Button onClick={() => void navigate({ to: "/", replace: true })}>I've saved them — continue</Button>
        </> : session?.mfa_verified ? <>
          <p role="status">Two-factor verification is complete.</p>
          <Button onClick={() => void navigate({ to: "/", replace: true })}>Continue to broker platform</Button>
        </> : status.data && <>
          {status.data.status !== "confirmed" && !setup && <Button disabled={pending} onClick={() => void run("start")}>Set up authenticator</Button>}
          {setup && <div className="space-y-3">
            <p className="text-sm">Scan this code in your authenticator app, or enter the key manually.</p>
            <div className="w-fit rounded-md bg-white p-3"><QRCodeSVG value={setup.otpauth_uri} size={148} title="Scan to set up your broker authenticator" /></div>
            <code className="block break-all rounded-md bg-muted p-2">{setup.secret}</code>
          </div>}
          {(setup || status.data.status === "confirmed") && <form className="space-y-3" onSubmit={event => { event.preventDefault(); if (valid) void run(setup ? "confirm" : "verify"); }}>
            <Label htmlFor="broker-code">{setup ? "Six-digit authenticator code" : "Authenticator or recovery code"}</Label>
            <Input id="broker-code" value={code} onChange={event => setCode(event.target.value.trim())} inputMode={setup ? "numeric" : "text"} autoComplete="one-time-code" maxLength={14} spellCheck={false} />
            <Button type="submit" disabled={!valid || pending}>{pending ? "Verifying…" : "Verify and continue"}</Button>
          </form>}
        </>}
        {error && <p role="alert" className="text-sm text-error">{error}</p>}
        <Button variant="outline" disabled={pending} onClick={() => void run("logout")}>Sign out</Button>
      </CardContent>
    </Card>
  </main>;
}
