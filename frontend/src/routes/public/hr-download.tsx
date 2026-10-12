import { useEffect, useMemo, useRef, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Ban, Clock, Copy, Download, FileArchive, KeyRound, Lock, ShieldCheck, Smartphone } from "lucide-react";
import { toast } from "sonner";
import { usePortalTranslation } from "@/i18n/portal";
import {
  enterHrPin,
  fetchHrDownload,
  HR_DOWNLOAD_FILE_URL,
  LinkError,
  retryLinkDelay,
  retryLinkQuery,
  startHrDownload,
  useLinkToken,
  type HrDownload,
} from "@/api/publicLinks";
import { Action, actionClass } from "@/components/portal/leaf/Action";
import { Field, FormAlert, leafControl } from "@/components/portal/leaf/Field";
import { LeafSkeleton } from "@/components/portal/leaf/LeafSkeleton";
import { PortalErrorState } from "@/components/portal/PortalErrorState";
import { LinkNotice, PublicLinkFrame } from "@/components/publicLinks/PublicLinkFrame";
import { parseServerDate } from "@/lib/format";
import { useDocumentTitle } from "@/lib/useDocumentTitle";

const TOKEN_KEY = "inspro.hrDownloadLink";
/** In-app browsers that commonly block file downloads or keep their own cookies. */
const IN_APP = /MicroMessenger|WhatsApp|Line\/|FBAN|FBAV|Instagram|Teams\//i;

function size(bytes: number): string {
  if (bytes >= 1024 * 1024) return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
  return `${Math.max(1, Math.round(bytes / 1024))} KB`;
}

export function HrDownloadPage() {
  const pt = usePortalTranslation();
  useDocumentTitle(pt("Download enrolment forms"));
  const token = useLinkToken(TOKEN_KEY);
  const query = useQuery({
    queryKey: ["public-hr-download", token],
    queryFn: () => fetchHrDownload(token!),
    enabled: !!token,
    retry: retryLinkQuery,
    retryDelay: retryLinkDelay,
    refetchOnWindowFocus: true,
    meta: { localErrorHandling: true },
  });
  if (!token || (query.error instanceof LinkError && query.error.status === 404)) {
    return (
      <PublicLinkFrame>
        <LinkNotice icon={<Ban className="size-6" />} title="This link is not valid">
          <p>{pt("Open the download link exactly as your broker sent it, or ask them for a new link and PIN.")}</p>
        </LinkNotice>
      </PublicLinkFrame>
    );
  }
  if (query.isPending) return <PublicLinkFrame><LeafSkeleton /></PublicLinkFrame>;
  if (query.isError) {
    return <PublicLinkFrame><PortalErrorState onRetry={() => void query.refetch()} /></PublicLinkFrame>;
  }
  return <DownloadStates key={token} token={token} info={query.data} />;
}

function Ended({ info, state }: { info: HrDownload; state: HrDownload["state"] }) {
  const pt = usePortalTranslation();
  const copy = {
    expired: { icon: <Clock className="size-6" />, title: "This link has expired", body: "The download window has ended or the link was not used in time. Files you already saved stay on your device." },
    locked: { icon: <Lock className="size-6" />, title: "Too many wrong PINs", body: "This link is locked to protect the forms." },
    revoked: { icon: <Ban className="size-6" />, title: "This link is no longer active", body: "Your broker has replaced or closed this link." },
    in_use: { icon: <Smartphone className="size-6" />, title: "Opened on another device", body: "This link was already started in another browser. It works only in the browser where Start download was tapped." },
  }[state as "expired" | "locked" | "revoked" | "in_use"];
  return (
    <PublicLinkFrame company={info.company_name}>
      <LinkNotice icon={copy.icon} title={copy.title} tone="warn">
        <p>{pt(copy.body)}</p>
        <p className="font-semibold text-record">{pt("Ask your broker for a new link and PIN.")}</p>
      </LinkNotice>
    </PublicLinkFrame>
  );
}

function DownloadStates({ token, info }: { token: string; info: HrDownload }) {
  const pt = usePortalTranslation();
  const queryClient = useQueryClient();
  const set = (next: HrDownload) => queryClient.setQueryData(["public-hr-download", token], next);
  if (!["ready", "verified", "active"].includes(info.state)) return <Ended info={info} state={info.state} />;
  const steps = [
    { key: "ready", label: "Enter PIN" },
    { key: "verified", label: "Start download" },
    { key: "active", label: "Save the files" },
  ];
  const at = steps.findIndex(s => s.key === info.state);
  return (
    <PublicLinkFrame company={info.company_name}>
      <section className="leaf-mount space-y-5 rounded-tile border border-glass-edge bg-bar p-5 shadow-mount sm:p-6"
        aria-labelledby="hr-download-title">
        <div className="flex items-start gap-3">
          <span aria-hidden className="grid size-11 shrink-0 place-items-center rounded-control bg-action-wash text-action-ink">
            <FileArchive className="size-5" />
          </span>
          <div className="min-w-0">
            <h1 id="hr-download-title" className="text-xl font-bold tracking-title text-record">{pt("Enrolment forms")}</h1>
            <p className="text-row text-label">{info.label}</p>
            <p className="text-row text-record">
              {info.file_count === 1
                ? pt("1 form · {0} ZIP", [size(info.size_bytes)])
                : pt("{0} forms · {1} ZIP", [info.file_count, size(info.size_bytes)])}
            </p>
          </div>
        </div>
        <ol className="grid grid-cols-3 gap-2" aria-label={pt("Steps")}>
          {steps.map((s, i) => (
            <li key={s.key} aria-current={i === at ? "step" : undefined}
              className={`rounded-control px-2 py-2 text-center text-xs font-semibold sm:text-row ${
                i < at ? "bg-shade/60 text-label" : i === at ? "bg-action-wash text-action-ink" : "text-label"}`}>
              <span className="tabular-nums">{i + 1}.</span> {pt(s.label)}
            </li>
          ))}
        </ol>
        {info.state === "ready" && <PinStep token={token} info={info} onDone={set} />}
        {info.state === "verified" && <StartStep token={token} info={info} onDone={set} />}
        {info.state === "active" && (
          <ActiveStep info={info} onRefresh={() => void queryClient.invalidateQueries({ queryKey: ["public-hr-download", token] })} />
        )}
      </section>
      <InAppWarning token={token} />
    </PublicLinkFrame>
  );
}

function PinStep({ token, info, onDone }: { token: string; info: HrDownload; onDone: (d: HrDownload) => void }) {
  const pt = usePortalTranslation();
  const [pin, setPin] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!/^\d{6}$/.test(pin)) { setError("Enter the 6-digit PIN."); return; }
    setBusy(true);
    setError(null);
    try {
      onDone(await enterHrPin(token, pin));
    } catch (err) {
      if (err instanceof LinkError && err.status === 401 && err.attemptsLeft !== undefined) {
        setError(pt("That PIN is not correct. {0} attempts left.", [err.attemptsLeft]));
        setPin("");
      } else if (err instanceof LinkError && err.state) {
        onDone({ ...info, state: err.state as HrDownload["state"] });
      } else {
        setError(err instanceof Error ? err.message : "Something went wrong. Please try again.");
      }
    } finally {
      setBusy(false);
    }
  };
  return (
    <form className="space-y-4" onSubmit={e => void submit(e)} noValidate>
      <p className="text-row text-record">
        {pt("Your broker gave you a 6-digit PIN separately from this link. Opening this page does not start anything.")}
      </p>
      <Field label="PIN" required error={error}>
        {props => (
          <input {...props} className={`${leafControl} max-w-[12rem] text-center text-xl font-semibold tracking-[0.3em] tabular-nums`}
            inputMode="numeric" autoComplete="one-time-code" maxLength={6} value={pin}
            onChange={e => setPin(e.target.value.replace(/\D/g, "").slice(0, 6))} />
        )}
      </Field>
      <Action tone="primary" type="submit" block="phone" disabled={busy || pin.length !== 6} aria-busy={busy}>
        <KeyRound className="size-4" aria-hidden /> {busy ? pt("Checking…") : pt("Continue")}
      </Action>
    </form>
  );
}

function StartStep({ token, info, onDone }: { token: string; info: HrDownload; onDone: (d: HrDownload) => void }) {
  const pt = usePortalTranslation();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const minutes = Math.round(info.access_seconds / 60);
  const start = async () => {
    setBusy(true);
    setError(null);
    try {
      const next = await startHrDownload(token);
      onDone(next);
      window.location.assign(HR_DOWNLOAD_FILE_URL);
    } catch (err) {
      if (err instanceof LinkError && err.state) onDone({ ...info, state: err.state as HrDownload["state"] });
      else setError(err instanceof Error ? err.message : "Something went wrong. Please try again.");
    } finally {
      setBusy(false);
    }
  };
  return (
    <div className="space-y-4">
      <p className="flex items-start gap-2 text-row text-record">
        <ShieldCheck className="mt-0.5 size-4 shrink-0 text-strike-approved" aria-hidden />
        {pt("PIN accepted.")}
      </p>
      <ul className="list-disc space-y-1 pl-5 text-row text-record">
        <li>{pt("You get {0} minutes from the moment you tap Start download.", [minutes])}</li>
        <li>{pt("Use this device: the download works only in this browser once started.")}</li>
        <li>{pt("You can download again within the {0} minutes if it fails.", [minutes])}</li>
      </ul>
      {error && <FormAlert>{pt(error)}</FormAlert>}
      <Action tone="primary" type="button" block="phone" onClick={() => void start()} disabled={busy} aria-busy={busy}>
        <Download className="size-4" aria-hidden /> {busy ? pt("Starting…") : pt("Start download")}
      </Action>
    </div>
  );
}

function useRemaining(info: HrDownload): number {
  // The server's clock decides; the offset keeps a wrong device clock from
  // showing time the server will not honour.
  const offset = useMemo(() => parseServerDate(info.server_time).getTime() - Date.now(), [info.server_time]);
  const end = info.expires_at ? parseServerDate(info.expires_at).getTime() : 0;
  const [now, setNow] = useState(() => Date.now() + offset);
  useEffect(() => {
    const timer = window.setInterval(() => setNow(Date.now() + offset), 1000);
    return () => window.clearInterval(timer);
  }, [offset]);
  return Math.max(0, Math.floor((end - now) / 1000));
}

function ActiveStep({ info, onRefresh }: { info: HrDownload; onRefresh: () => void }) {
  const pt = usePortalTranslation();
  const remaining = useRemaining(info);
  const ended = useRef(false);
  useEffect(() => {
    if (remaining === 0 && !ended.current) { ended.current = true; onRefresh(); }
  }, [remaining, onRefresh]);
  const left = info.downloads_left ?? 1;
  const mm = String(Math.floor(remaining / 60)).padStart(2, "0");
  const ss = String(remaining % 60).padStart(2, "0");
  const pct = Math.min(100, (remaining / info.access_seconds) * 100);
  const low = remaining <= 60;
  return (
    <div className="space-y-4">
      <div className="space-y-2" role="timer" aria-label={pt("Time left to download")}>
        <div className="flex items-baseline justify-between">
          <span className="text-row text-label">{pt("Time left")}</span>
          <span className={`text-3xl font-bold tabular-nums ${low ? "text-strike-pending" : "text-record"}`}>{mm}:{ss}</span>
        </div>
        <div className="h-2 overflow-hidden rounded-full bg-shade" aria-hidden>
          <div className={`h-full rounded-full transition-[width] duration-1000 ease-linear ${low ? "bg-strike-pending" : "bg-action"}`}
            style={{ width: `${pct}%` }} />
        </div>
      </div>
      <p className="text-row text-record">
        {pt("Your download should start by itself. If it does not, or it stops, tap Download again.")}
      </p>
      {left > 0 ? (
        <a className={actionClass("primary", { block: "phone" })} href={HR_DOWNLOAD_FILE_URL}
          onClick={() => window.setTimeout(onRefresh, 1500)}>
          <Download className="size-4" aria-hidden /> {pt("Download again")}
        </a>
      ) : (
        <p className="rounded-control bg-action-wash px-3 py-2 text-row text-record">
          {pt("This link has reached its download limit. Ask your broker for a new link and PIN if you need the files again.")}
        </p>
      )}
      {left > 0 && info.downloads_left !== null && (
        <p className="text-row text-label">{pt("Downloads left on this link: {0}", [left])}</p>
      )}
      <p className="text-row text-label">
        {pt("The ZIP holds each form as a PDF and a list of the forms. Store it securely; it contains personal data.")}
      </p>
    </div>
  );
}

function InAppWarning({ token }: { token: string }) {
  const pt = usePortalTranslation();
  if (typeof navigator === "undefined" || !IN_APP.test(navigator.userAgent)) return null;
  const copyLink = async () => {
    try {
      await navigator.clipboard.writeText(`${window.location.origin}${window.location.pathname}#${token}`);
      toast.success(pt("Link copied. Paste it into Safari or Chrome."));
    } catch {
      toast.error(pt("Copy the link from your broker's message instead."));
    }
  };
  return (
    <div className="mt-4 flex flex-col gap-3 rounded-control bg-action-wash p-4 text-row text-record sm:flex-row sm:items-center">
      <p className="flex-1">
        {pt("You opened this inside another app. Downloads often fail there, so open the link in Safari or Chrome before you start.")}
      </p>
      <Action tone="neutral" type="button" onClick={() => void copyLink()}>
        <Copy className="size-4" aria-hidden /> {pt("Copy link")}
      </Action>
    </div>
  );
}
