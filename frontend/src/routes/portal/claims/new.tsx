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
import { AnchorField } from "@/components/portal/claims/AnchorField";
import { AutofillCard } from "@/components/portal/claims/AutofillCard";
import { ClaimTypeFields } from "@/components/portal/claims/ClaimTypeFields";
import { ClaimSubmissionHeader } from "@/components/portal/claims/ClaimSubmissionHeader";
import { ClaimLeavePrompt } from "@/components/portal/claims/ClaimLeavePrompt";
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
import { errorStatus, formatError } from "@/lib/errors";
import { useDocumentTitle } from "@/lib/useDocumentTitle";
import { useCompany } from "@/components/portal/useCompany";

function focusFirstInvalidField(form: HTMLFormElement) {
  requestAnimationFrame(() => {
    const group = form.querySelector<HTMLElement>('[data-field-error="true"]');
    if (!group) return;
    const control = group.querySelector<HTMLElement>(
      'input:not([disabled]), select:not([disabled]), textarea:not([disabled]), button:not([disabled]), [tabindex]:not([tabindex="-1"])',
    );
    const reducedMotion = window.matchMedia(
      "(prefers-reduced-motion: reduce)",
    ).matches;
    group.scrollIntoView({
      behavior: reducedMotion ? "auto" : "smooth",
      block: "center",
    });
    control?.focus({ preventScroll: true });
  });
}

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
    /* The server's own sentence, in the two different ways it can arrive.
       `claimBlock` is the 200 that withheld a claim kind (cover that ended
       before the period began); a 403 is the harder case — `coverage-options`
       requires CLAIM, so a settling leaver's request is REFUSED outright and
       there is no body to read a block out of. Both must beat the generic line
       below, which says the member has no cover on file and sends them to HR:
       untrue, contradicted by the access notice on the same screen, and the
       "this app is broken" outcome that notice exists to prevent. */
    const refused =
      options.isError && errorStatus(options.error) === 403
        ? formatError(options.error)
        : null;
    const blocked = form.claimBlock ?? refused;
    return (
      /* The heading has to move with the sentence. "No benefits to claim
         against" is the right noun only for the generic case — a leaver HAS
         benefits, and the thing that ended is the window. */
      <Mount label={blocked ? "This window has closed" : "No benefits to claim against"}>
        <p className="text-row text-label">
          {blocked ??
            "We don't have any cover recorded against your name for this " +
              "period, so there's nothing to claim against yet. Your HR team " +
              "can check your record."}
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
        <ClaimSubmissionHeader form={form} />
        <form
          className="mt-6 space-y-4"
          onSubmit={async (e) => {
            e.preventDefault();
            const formElement = e.currentTarget;
            const valid = await form.submit();
            if (valid === false) focusFirstInvalidField(formElement);
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
          {/* Before the visit details, because it changes what they are
              prefilled with. */}
          <AnchorField form={form} />
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
      <ClaimLeavePrompt form={form} />
    </div>
  );
}
