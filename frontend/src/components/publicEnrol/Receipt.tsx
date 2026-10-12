import { useEffect, useState } from "react";
import { CheckCircle2, Download } from "lucide-react";
import { usePortalLocale, usePortalTranslation } from "@/i18n/portal";
import { fetchOwnCopy, LinkError, type PublicForm, type PublicReceipt } from "@/api/publicLinks";
import { Action } from "@/components/portal/leaf/Action";
import { FormAlert } from "@/components/portal/leaf/Field";
import { downloadResponseAsFile } from "@/lib/download";
import { parseServerDate } from "@/lib/format";
import { LinkNotice } from "@/components/publicLinks/PublicLinkFrame";

/** The confirmation an employee keeps: the reference to quote, what happens
 *  next, and one download of their own form while this page is still open. */
export function Receipt({
  token,
  form,
  receipt,
  onAnother,
}: {
  token: string;
  form: PublicForm;
  receipt: PublicReceipt;
  onAnother: () => void;
}) {
  const pt = usePortalTranslation();
  const { locale } = usePortalLocale();
  const [state, setState] = useState<"ready" | "busy" | "done" | "gone">("ready");
  const [error, setError] = useState<string | null>(null);
  const expiry = parseServerDate(receipt.copy_expires_at).getTime();
  useEffect(() => {
    const left = expiry - Date.now();
    if (left <= 0) { setState(s => (s === "ready" ? "gone" : s)); return; }
    const timer = window.setTimeout(() => setState(s => (s === "ready" ? "gone" : s)), left);
    return () => window.clearTimeout(timer);
  }, [expiry]);
  const copy = async () => {
    setState("busy");
    setError(null);
    try {
      const res = await fetchOwnCopy(token, receipt.reference_no, receipt.copy_key);
      await downloadResponseAsFile(res, `Enrolment form ${receipt.reference_no}.pdf`);
      setState("done");
    } catch (e) {
      setState(e instanceof LinkError && e.status === 404 ? "gone" : "ready");
      setError(e instanceof Error ? e.message : "Something went wrong. Please try again.");
    }
  };
  const submitted = parseServerDate(receipt.submitted_at);
  return (
    <LinkNotice icon={<CheckCircle2 className="size-6" />} title="Form received" tone="good">
      <p>{pt("Your reference")}</p>
      <p className="text-2xl font-bold tracking-title text-record tabular-nums" aria-live="polite">{receipt.reference_no}</p>
      <p>
        {pt("Submitted {0} for {1}.", [
          submitted.toLocaleString(locale, { dateStyle: "medium", timeStyle: "short" }),
          form.company_name,
        ])}
      </p>
      <p className="text-record">
        {pt("Your company's broker checks your form against HR records before any change to your cover. Quote your reference if you contact them.")}
      </p>
      {form.submission_note && <p>{pt(form.submission_note)}</p>}
      <div className="flex flex-col items-center gap-2 pt-2">
        {state === "gone" ? (
          <p>{pt("Your copy is no longer available from this page. Ask your HR team if you need one.")}</p>
        ) : state === "done" ? (
          <p className="font-semibold text-strike-approved">{pt("Your copy has been downloaded.")}</p>
        ) : (
          <>
            <Action tone="primary" type="button" onClick={() => void copy()} disabled={state === "busy"} aria-busy={state === "busy"}>
              <Download className="size-4" aria-hidden /> {pt("Download my copy (PDF)")}
            </Action>
            <p className="text-xs">{pt("Available once, for 15 minutes, on this page only.")}</p>
          </>
        )}
        {error && state !== "done" && <FormAlert>{pt(error)}</FormAlert>}
        <button type="button" onClick={onAnother}
          className="leaf-focus mt-2 min-h-11 rounded-control px-3 text-row font-semibold text-action-ink hover:bg-shade/50">
          {pt("Fill in another form")}
        </button>
      </div>
    </LinkNotice>
  );
}
