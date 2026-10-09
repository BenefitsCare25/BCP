import { usePortalTranslation } from "@/i18n/portal";
/** The last step: what the member will pay, anything that stops them sending,
 * and the signature.
 *
 * The signature is a typed full name plus an explicit confirmation (user
 * decision: no drawn signature). Both are required, and the button stays off
 * until every problem listed above it is cleared — the server repeats every
 * check, so this is for the member's benefit, not the record's. */
import { Loader2, PenLine } from "lucide-react";
import { useId } from "react";
import type { FormSubmissionSummary } from "@/api/enrollmentForms";
import { Action } from "@/components/portal/leaf/Action";
import { Field, leafControl } from "@/components/portal/leaf/Field";
import { Money } from "@/components/portal/leaf/Figure";
import { Mount, MountRow, MountRule } from "@/components/portal/leaf/Mount";
import { choiceControl, choiceRowClass } from "../choiceRow";
import { SignedFormLine } from "./SignedFormLine";

export interface ShareLine {
  code: string;
  name: string;
  amount: number;
}

export function SignMount({
  shares,
  gstIncluded,
  problems,
  expectedName,
  signature,
  confirmed,
  signing,
  disabled,
  latest,
  submissionNote,
  helpline,
  onSignatureChange,
  onConfirmedChange,
  onSign,
}: {
  shares: ShareLine[];
  gstIncluded: boolean;
  problems: string[];
  expectedName: string | null;
  signature: string;
  confirmed: boolean;
  signing: boolean;
  disabled: boolean;
  latest: FormSubmissionSummary | null;
  submissionNote: string | null;
  helpline: string | null;
  onSignatureChange: (value: string) => void;
  onConfirmedChange: (value: boolean) => void;
  onSign: () => void;
}) {
  const pt = usePortalTranslation();
  const problemsId = useId();
  const total = shares.reduce((sum, s) => sum + s.amount, 0);
  const ready = !disabled && problems.length === 0 && signature.trim().length >= 2 && confirmed;

  return (
    <Mount
      as="article"
      rise={false}
      label={pt("Sign and send")}
      gloss={disabled
        ? "Signing is currently unavailable. This page does not submit a form automatically."
        : "Sign below to send your form to your broker and save a copy for download."}
    >
      {shares.length > 0 && (
        <>
          <dl>
            {shares.map((s) => (
              <MountRow key={s.code} term={s.name}>
                <Money value={s.amount} />
              </MountRow>
            ))}
            {shares.length > 1 && (
              <MountRow term={<span className="font-semibold">{pt("Your share a year")}</span>}>
                <Money value={total} emphasis="strong" />
              </MountRow>
            )}
          </dl>
          <p className="text-row text-label">
            {pt("Your share of the annual premium")}{gstIncluded ? pt(", including GST") : pt(", before GST")}{pt(". It is pro-rated if your cover starts part-way through the year.")} </p>
          <MountRule />
        </>
      )}

      {latest && <SignedFormLine form={latest} lead="Last signed" />}

      {problems.length > 0 && (
        <div id={problemsId} role="alert" className="flex flex-col gap-1">
          <p className="text-row font-medium text-strike-pending">{pt("Before you can sign:")}</p>
          <ul className="list-disc space-y-1 pl-5 text-row text-strike-pending">
            {problems.map((p) => (
              <li key={p}>{pt(p)}</li>
            ))}
          </ul>
        </div>
      )}

      {!disabled && (
        <>
          <Field
            label={pt("Type your full name to sign")}
            hint={expectedName ? pt("As on your records: {0}", [expectedName]) : undefined}
          >
            {(p) => (
              <input
                {...p}
                className={leafControl}
                autoComplete="name"
                value={signature}
                maxLength={255}
                onChange={(e) => onSignatureChange(e.target.value)}
              />
            )}
          </Field>
          <label className={choiceRowClass(confirmed, "items-start")}>
            <input
              type="checkbox"
              className={`${choiceControl} mt-0.5`}
              checked={confirmed}
              onChange={(e) => onConfirmedChange(e.target.checked)}
            />
            <span className="text-row text-record">
              {pt("I confirm the details in this form are correct, and I agree that typing my name above is my electronic signature.")} </span>
          </label>
          <Action
            tone="primary"
            block="phone"
            disabled={!ready || signing}
            aria-describedby={problems.length ? problemsId : undefined}
            onClick={onSign}
          >
            {signing ? (
              <Loader2 className="size-4 animate-spin" aria-hidden />
            ) : (
              <PenLine className="size-4" aria-hidden />
            )}
            {latest ? pt("Sign and send again") : pt("Sign and send")}
          </Action>
        </>
      )}

      {(submissionNote || helpline) && (
        <div className="flex flex-col gap-1">
          {submissionNote && <p className="text-row text-label">{pt(submissionNote)}</p>}
          {helpline && <p className="text-row text-label">{pt(helpline)}</p>}
        </div>
      )}
    </Mount>
  );
}
