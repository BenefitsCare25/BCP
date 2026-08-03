/** Submit-a-claim form — composition only.
 *
 * The state machine is `components/portal/claims/useNewClaimForm`; each section
 * below is a component in the same folder. The page used to be one 1,493-line
 * component holding ~25 pieces of state, against the house 100-line function
 * and 500-line file ceilings; splitting it changed no behaviour.
 *
 * Flow: who is this claim for → claim type (one grouped dropdown; kind and
 * sub-type are derived from the choice) → the visit → the documents that claim
 * type requires. The claim is created as a draft, its evidence attaches, and
 * submit runs the backend validations (intake profile, coverage/eligibility,
 * in-period, duplicates). */
import { useNavigate } from "@tanstack/react-router";
import { ArrowLeft, Loader2, Send } from "lucide-react";
import { AutofillCard } from "@/components/portal/claims/AutofillCard";
import { ClaimTypeFields } from "@/components/portal/claims/ClaimTypeFields";
import { DocumentFields } from "@/components/portal/claims/DocumentFields";
import { PendingClaimsNotice } from "@/components/portal/claims/PendingClaimsNotice";
import { ReferralField } from "@/components/portal/claims/ReferralField";
import { VisitFields } from "@/components/portal/claims/VisitFields";
import { MAX_REMARKS } from "@/components/portal/claims/claimForm";
import { useNewClaimForm } from "@/components/portal/claims/useNewClaimForm";
import { Action } from "@/components/portal/leaf/Action";
import { Field, FormAlert, leafControl } from "@/components/portal/leaf/Field";
import { LeafSkeleton } from "@/components/portal/leaf/LeafSkeleton";
import { Mount } from "@/components/portal/leaf/Mount";
import { useDocumentTitle } from "@/lib/useDocumentTitle";
import { useCompany } from "@/components/portal/useCompany";

export function PortalNewClaimPage() {
  const navigate = useNavigate();
  const company = useCompany();
  useDocumentTitle("Make a claim");
  const form = useNewClaimForm();
  const { options } = form;

  if (options.isLoading) return <LeafSkeleton label="Loading the claim form" />;
  if (
    options.isError ||
    !options.data ||
    (form.insured.length === 0 && !form.hasFlex)
  ) {
    return (
      <Mount label="No benefits to claim against">
        <p className="text-row text-label">
          We don't have any cover recorded against your name for this period, so
          there's nothing to claim against yet. Your HR team can check your
          record.
        </p>
      </Mount>
    );
  }

  const queued = form.pendingClaims.length;

  return (
    <div className="mx-auto max-w-xl">
      {/* No heading and no preamble. The nav and the document title already say
          what this page is; "your broker reviews every claim" describes our
          process, not the member's task; and the eligible-date window is
          enforced by the date field's own min/max, so stating it up front was a
          rule the member had to hold in their head to use a control that
          already holds it for them.

          The back link rides INSIDE the mount, in the autofill row, rather than
          floating above it on the bare ground. A lone control on the ground
          above a single pane reads as page chrome — a second, quieter header
          under the one the shell already draws — and it left the pane's own top
          edge carrying nothing but a shortcut. */}
      <Mount>
        <form
          className="space-y-4"
          onSubmit={(e) => {
            e.preventDefault();
            void form.submit();
          }}
          // **Enter in a text field must not submit this form.** HTML implicit
          // submission fires the submit button from any single-line input, and
          // `Field` marks required controls with `aria-required` rather than
          // `required` (the visible "(required)" marker is the portal's
          // convention, and native validation bubbles are broker-app chrome) —
          // so nothing intervenes. On an otherwise-complete form, pressing
          // Enter after typing an invoice number created the claim, uploaded
          // every document and submitted it. **A member cannot un-submit**:
          // submit moves the claim to `ai_review_pending` and only a broker can
          // reopen it. Submitting a claim is a decision, so it takes the
          // button. `<textarea>` is exempt — Enter there is a newline, and the
          // remarks box needs it.
          onKeyDown={(e) => {
            if (
              e.key === "Enter" &&
              !e.shiftKey &&
              e.target instanceof HTMLElement &&
              e.target.tagName !== "TEXTAREA" &&
              e.target.tagName !== "BUTTON"
            ) {
              e.preventDefault();
            }
          }}
        >
          <AutofillCard
            form={form}
            leading={
              <button
                type="button"
                onClick={() => void navigate({ to: "/portal/$company/claims", params: { company } })}
                className="leaf-focus -ml-2 inline-flex min-h-11 shrink-0 items-center gap-1.5 px-2 text-row text-label"
              >
                <ArrowLeft className="size-4" aria-hidden /> All claims
              </button>
            }
          />
          <PendingClaimsNotice form={form} />
          <ClaimTypeFields form={form} />
          <VisitFields form={form} />
          <ReferralField form={form} />
          <DocumentFields form={form} />

          <Field label="Remarks">
            {(p) => (
              <>
                <textarea
                  {...p}
                  rows={3}
                  className={`${leafControl} resize-y`}
                  placeholder="Anything your broker should know about this claim (optional)"
                  value={form.remarks}
                  maxLength={MAX_REMARKS}
                  onChange={(e) => form.setRemarks(e.target.value)}
                />
                <p className="text-right text-row text-label">
                  {form.remarks.length}/{MAX_REMARKS}
                </p>
              </>
            )}
          </Field>

          {form.error && <FormAlert>{form.error}</FormAlert>}

          {/* The page's one brand fill. */}
          <Action tone="primary" type="submit" block disabled={form.busy}>
            {form.busy ? (
              <Loader2 className="size-4 animate-spin" aria-hidden />
            ) : (
              <Send className="size-4" aria-hidden />
            )}
            {queued > 0
              ? `Submit claim ${form.multiDone + 1} of ${form.multiDone + 1 + queued}`
              : "Submit claim"}
          </Action>
        </form>
      </Mount>
    </div>
  );
}
