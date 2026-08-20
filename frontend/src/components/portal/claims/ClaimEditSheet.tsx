/** Correcting a claim the broker has not yet decided.
 *
 * Deliberately NOT a reuse of the create form's field components. Those six
 * (`VisitFields`, `ClaimTypeFields`, …) are each bound to the whole
 * `useNewClaimForm` contract — coverage options, the claimant filter, doc slots
 * by hospital sector, referral resolution — so "sharing" them from here would
 * mean satisfying that entire hook from an edit context. That is more coupling,
 * not less. The truth the two surfaces genuinely share is the VALIDATION, and
 * that already lives in one place on the server (`claims.validate_claim_facts`,
 * which submit and both amendment endpoints all run).
 *
 * Scope is the visit detail — the fields a member actually gets wrong on a
 * receipt they are copying. Changing the claim TYPE, the product or the
 * claimant is supported by the API but not offered here: each of those changes
 * which documents the claim requires, so the honest UI for it is the create
 * flow, not a patch sheet. A broker can make those corrections.
 *
 * Every value is re-validated server-side over the merged claim, so this form
 * does not duplicate the rules — it collects, and it reports what came back.
 */
import { useState } from "react";
import { Loader2 } from "lucide-react";
import type { PortalClaim } from "@/api/portal";
import { type ClaimAmendInput } from "@/api/portal";
import { DiagnosisPicker } from "@/components/portal/DiagnosisPicker";
import { Action } from "@/components/portal/leaf/Action";
import {
  Field,
  FieldGroup,
  FormAlert,
  leafControl,
} from "@/components/portal/leaf/Field";
import { MountRule } from "@/components/portal/leaf/Mount";

const MAX_REMARKS = 500;

/** What the sheet edits. Kept as strings (what inputs hold) and converted on
 * submit — a number input bound to a number fights the user mid-typing. */
interface Draft {
  incurred_date: string;
  admission_date: string;
  discharge_date: string;
  provider_name: string;
  invoice_number: string;
  doctor_name: string;
  diagnosis: string;
  remarks: string;
  amount_claimed: string;
}

/** The BASELINE is trimmed to match how the diff below compares — `create_claim`
 * stores `provider_name` / `invoice_number` verbatim, so a claim filed as
 * `"Raffles Clinic "` opened the sheet already dirty against an untrimmed
 * baseline. Saving then sent a phantom change: a bumped revision, an audit row,
 * and the member told "You changed the clinic or hospital" for an edit nobody
 * made. */
function draftFrom(claim: PortalClaim): Draft {
  return {
    incurred_date: claim.incurred_date,
    admission_date: claim.admission_date ?? "",
    discharge_date: claim.discharge_date ?? "",
    provider_name: (claim.provider_name ?? "").trim(),
    invoice_number: (claim.invoice_number ?? "").trim(),
    doctor_name: (claim.doctor_name ?? "").trim(),
    diagnosis: claim.diagnosis ?? "",
    remarks: claim.remarks ?? "",
    amount_claimed: String(claim.amount_claimed),
  };
}

/** Only what actually MOVED. The endpoint is a partial update keyed on the
 * fields present in the body, so sending the whole draft would mark every field
 * as touched — which shows up in the member's own thread notice as "you changed
 * the amount, the invoice number, the diagnosis…" for a one-field correction,
 * and writes an audit row implying the same. */
function changedFields(
  before: Draft,
  now: Draft,
  supportsStayDates: boolean,
): ClaimAmendInput {
  const patch: ClaimAmendInput = {};
  if (now.incurred_date !== before.incurred_date) {
    patch.incurred_date = now.incurred_date;
  }
  if (supportsStayDates) {
    if (now.admission_date !== before.admission_date)
      patch.admission_date = now.admission_date || null;
    if (now.discharge_date !== before.discharge_date)
      patch.discharge_date = now.discharge_date || null;
  }
  if (now.provider_name.trim() !== before.provider_name)
    patch.provider_name = now.provider_name.trim();
  if (now.invoice_number.trim() !== before.invoice_number)
    patch.invoice_number = now.invoice_number.trim();
  if (now.doctor_name.trim() !== before.doctor_name)
    // Cleared means cleared — `null`, not `""`. An empty string would store a
    // blank where the claim should hold nothing, and the review would then have
    // a blank to reason about.
    patch.doctor_name = now.doctor_name.trim() || null;
  if (now.diagnosis !== before.diagnosis)
    patch.diagnosis = now.diagnosis || null;
  if (now.remarks !== before.remarks) patch.remarks = now.remarks || null;
  if (now.amount_claimed !== before.amount_claimed)
    patch.amount_claimed = Number(now.amount_claimed);
  return patch;
}

export function ClaimEditSheet({
  claim,
  expectedRevision,
  saving = false,
  error,
  onSave,
  onCancel,
}: {
  claim: PortalClaim;
  /** The revision the member has actually SEEN — owned by the page, because
   *  only it knows which bumps were the member's own doing (see the caller).
   *  Sent with the save so a change nobody showed them is refused rather than
   *  silently overwritten. */
  expectedRevision: number;
  saving?: boolean;
  /** Whatever the server said — this form does not second-guess it. */
  error?: string | null;
  onSave: (patch: ClaimAmendInput) => void;
  onCancel: () => void;
}) {
  // Captured ONCE, when the sheet opens: it is both the form's starting values
  // and the baseline the diff is taken against. Recomputing it from `claim` on
  // every render would make each keystroke look unchanged as soon as a refetch
  // landed.
  const [original] = useState(() => draftFrom(claim));
  const [draft, setDraft] = useState(original);

  const set = <K extends keyof Draft>(key: K, value: Draft[K]) =>
    setDraft((d) => ({ ...d, [key]: value }));

  const patch = changedFields(original, draft, claim.supports_stay_dates);
  const dirty = Object.keys(patch).length > 0;
  const amountValid = Number(draft.amount_claimed) > 0;
  const stayDatesValid =
    !claim.supports_stay_dates ||
    !draft.admission_date ||
    !draft.discharge_date ||
    draft.discharge_date >= draft.admission_date;

  return (
    <form
      className="space-y-4"
      onSubmit={(e) => {
        e.preventDefault();
        if (!dirty || !amountValid || !stayDatesValid) return;
        onSave({ ...patch, expected_revision: expectedRevision });
      }}
    >
      {error && <FormAlert>{error}</FormAlert>}

      <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
        <Field label="Date of treatment" required>
          {(p) => (
            <input
              {...p}
              type="date"
              className={leafControl}
              value={draft.incurred_date}
              onChange={(e) => set("incurred_date", e.target.value)}
            />
          )}
        </Field>

        {claim.supports_stay_dates && (
          <>
            <Field label="Admission date (optional)">
              {(p) => (
                <input
                  {...p}
                  type="date"
                  className={leafControl}
                  value={draft.admission_date}
                  onChange={(e) => set("admission_date", e.target.value)}
                />
              )}
            </Field>
            <Field
              label="Discharge date (optional)"
              error={
                draft.admission_date &&
                draft.discharge_date &&
                draft.discharge_date < draft.admission_date
                  ? "The discharge date can't be before the admission date."
                  : undefined
              }
            >
              {(p) => (
                <input
                  {...p}
                  type="date"
                  className={leafControl}
                  min={draft.admission_date || undefined}
                  value={draft.discharge_date}
                  onChange={(e) => set("discharge_date", e.target.value)}
                />
              )}
            </Field>
          </>
        )}

        <Field label="Clinic or hospital" required>
          {(p) => (
            <input
              {...p}
              className={leafControl}
              value={draft.provider_name}
              maxLength={255}
              onChange={(e) => set("provider_name", e.target.value)}
            />
          )}
        </Field>

        <Field label="Invoice number" required>
          {(p) => (
            <input
              {...p}
              className={leafControl}
              value={draft.invoice_number}
              maxLength={128}
              onChange={(e) => set("invoice_number", e.target.value)}
            />
          )}
        </Field>

        <Field
          label="Amount claimed"
          required
          error={
            draft.amount_claimed !== "" && !amountValid
              ? "Enter an amount greater than zero."
              : undefined
          }
        >
          {(p) => (
            <input
              {...p}
              type="number"
              className={leafControl}
              min="0.01"
              step="0.01"
              value={draft.amount_claimed}
              onChange={(e) => set("amount_claimed", e.target.value)}
            />
          )}
        </Field>

        {/* Gated on the SERVED `requires_doctor_name`, not on whether the claim
            already holds one — which is exactly backwards. A pre/post claim
            recorded before the field existed has `doctor_name: null`, so keying
            off that hid the control on the one claim that needs it, while the
            amendment kept requiring it: every save 422'd with nothing on screen
            the member could use to satisfy it. `||` so a claim that carries a
            doctor can still have it corrected even if its type no longer asks. */}
        {(claim.requires_doctor_name || claim.doctor_name !== null) && (
          <Field label="Doctor seen" required>
            {(p) => (
              <input
                {...p}
                className={leafControl}
                value={draft.doctor_name}
                maxLength={255}
                onChange={(e) => set("doctor_name", e.target.value)}
              />
            )}
          </Field>
        )}
      </div>

      {/* Same picker the create form uses, scoped to this claim's own product
          so the catalog matches the setting. Insured claims only — a flex claim
          has no diagnosis group. */}
      {claim.claim_kind === "insured" && claim.product_code && (
        <FieldGroup label="Diagnosis">
          <DiagnosisPicker
            productCode={claim.product_code}
            value={draft.diagnosis}
            onChange={(v) => set("diagnosis", v)}
          />
        </FieldGroup>
      )}

      <Field label="Your note" hint="Anything we should know about this claim.">
        {(p) => (
          <textarea
            {...p}
            className={leafControl}
            rows={3}
            maxLength={MAX_REMARKS}
            value={draft.remarks}
            onChange={(e) => set("remarks", e.target.value)}
          />
        )}
      </Field>

      <MountRule />

      <div className="flex flex-col gap-2 sm:flex-row sm:items-center">
        <Action
          type="submit"
          tone="primary"
          block="phone"
          disabled={saving || !dirty || !amountValid || !stayDatesValid}
        >
          {saving && <Loader2 className="size-4 animate-spin" aria-hidden />}
          Save changes
        </Action>
        <Action type="button" block="phone" className="h-12" onClick={onCancel}>
          Cancel
        </Action>
        {/* A disabled primary with nothing explaining it reads as broken. */}
        {!dirty && (
          <p className="text-row text-label">Nothing changed yet.</p>
        )}
      </div>
    </form>
  );
}
