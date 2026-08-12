/** Specialist claims: the referral letter (upload / reuse).
 *
 * A referral letter is a MEMBER-level document, not a claim's — it is uploaded
 * once and reused across claims, which is why "select an existing letter" is
 * offered at all. A follow-up visit auto-selects the latest letter on file; when
 * none is tracked the member is prompted to attach one. */
import { useRef } from "react";
import { AlertTriangle, Paperclip } from "lucide-react";
import { FieldGroup, leafControl } from "@/components/portal/leaf/Field";
import { Action } from "@/components/portal/leaf/Action";
import { formatDay } from "@/components/portal/leaf/date";
import { ACCEPT, type ReferralMode } from "./claimForm";
import type { NewClaimForm } from "./useNewClaimForm";

export function ReferralField({ form }: { form: NewClaimForm }) {
  const input = useRef<HTMLInputElement>(null);
  const letters = form.referralLetters.data ?? [];
  if (!form.needsReferral || !form.visitType) return null;

  return (
    <FieldGroup
      label="Upload or select referral letter (required)"
      error={form.fieldErrors.referral}
    >
      {form.visitType === "follow_up" && letters.length === 0 && (
        <p className="flex items-start gap-1.5 text-row text-strike-pending">
          <AlertTriangle className="mt-0.5 size-3.5 shrink-0" aria-hidden />
          We couldn't find a referral letter on file for you — please attach the
          referral letter for this treatment.
        </p>
      )}

      <select
        className={leafControl}
        aria-label="How to provide the referral letter"
        value={form.referralMode}
        onChange={(e) => form.setReferralMode(e.target.value as ReferralMode)}
      >
        <option value="">Select an option</option>
        <option value="upload">Upload referral letter</option>
        <option value="existing" disabled={letters.length === 0}>
          Select existing referral letter
        </option>
      </select>

      {form.referralMode === "upload" && (
        <div className="space-y-1.5">
          <input
            ref={input}
            type="file"
            accept={ACCEPT}
            className="hidden"
            onChange={(e) => form.setReferralFile(e.target.files?.[0] ?? null)}
          />
          <Action
            type="button"
            className="max-w-full"
            onClick={() => input.current?.click()}
          >
            <Paperclip className="size-4 shrink-0" aria-hidden />
            <span className="truncate">
              {form.referralFile?.name ?? "Attach referral letter"}
            </span>
          </Action>
          {/* The letter's OWN date, and optional on purpose: a referral is
              usually valid for about a year, measured from when it was
              written — but a member who can't find a date on their letter must
              still be able to attach it. Left blank, nothing checks the age.
              Only offered on an upload: a letter already on file was dated (or
              not) when it was first attached. */}
          {form.referralFile && (
            <label className="block space-y-1">
              <span className="leaf-label block">
                Date on the letter (optional)
              </span>
              <input
                type="date"
                className={leafControl}
                max={form.maxIncurred || undefined}
                value={form.referralIssuedOn}
                onChange={(e) => form.setReferralIssuedOn(e.target.value)}
              />
            </label>
          )}
        </div>
      )}

      {form.referralMode === "existing" && (
        <select
          className={leafControl}
          aria-label="Referral letter on file"
          value={form.referralExistingId}
          onChange={(e) => form.setReferralExistingId(e.target.value)}
        >
          <option value="">Select a letter</option>
          {letters.map((d) => (
            <option key={d.id} value={d.id}>
              {d.file_name} ({formatDay(d.created_at)})
            </option>
          ))}
        </select>
      )}
    </FieldGroup>
  );
}
