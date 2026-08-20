/** Everything the claim form refuses to submit, as a pure function.
 *
 * Pure and outside the hook on purpose: these are the rules, and rules are
 * easier to trust when they can be read (and one day tested) without a React
 * tree around them. Every one of them mirrors a check the backend also makes —
 * the form exists to spare the member a round trip, never to be the only gate.
 */
import type { DocSlot, InsuredClaimOption } from "@/api/portal";
import { formatDay } from "@/components/portal/leaf/date";
import { OTHER_HOSPITAL } from "./claimForm";

export interface ClaimValues {
  effectiveKind: "insured" | "flex" | null;
  selectedProduct: InsuredClaimOption | null;
  diagnosis: string;
  needsReferral: boolean;
  visitType: string;
  /** Letters still loading — never accuse the member of having none on file. */
  referralLoading: boolean;
  referralCount: number;
  referralMode: string;
  referralFile: File | null;
  referralExistingId: string;
  incurredDate: string;
  /** The served claimable window for the selected claim kind — NOT the policy
   *  year: a flex scheme can start mid-year and a leaver's cover ends on their
   *  last day. */
  claimableFrom: string;
  claimableTo: string;
  today: string;
  isHospitalisation: boolean;
  hospital: string;
  provider: string;
  invoiceNumber: string;
  /** Pre-/post-hospitalisation claims must name the treating doctor. */
  requiresDoctorName: boolean;
  doctorName: string;
  amount: string;
  /** A foreign claim carrying a conversion the member has not yet accepted.
   *  False when the claim is already in the policy currency AND when no rate
   *  could be fetched — there is nothing to accept in either case, and a
   *  currency API outage must never stop someone filing a claim. */
  /** Foreign, with an eligible quote request still in flight. A request failure
   *  is fail-open: create/submit computes again server-side and, if it succeeds,
   *  routes the member to the saved claim to confirm the resulting figure. */
  fxBlocked: boolean;
  docSlots: DocSlot[];
  slotFiles: Record<string, File | null>;
}

function referralError(v: ClaimValues): string | null {
  if (!v.visitType) return null;
  if (v.referralLoading) {
    // The follow-up auto-link hasn't run yet, so ask them to wait rather than
    // telling them they have no letter.
    return "Loading your referral letters — try again in a moment.";
  }
  if (!v.referralMode) {
    // Only claim "none on file" once the query has resolved empty.
    return v.visitType === "follow_up" && v.referralCount === 0
      ? "We couldn't find a referral letter on file — attach one."
      : "Attach or select the referral letter.";
  }
  if (v.referralMode === "upload" && !v.referralFile) {
    return "Attach the referral letter.";
  }
  if (v.referralMode === "existing" && !v.referralExistingId) {
    return "Pick one of your previous referral letters.";
  }
  return null;
}

export function validateClaim(v: ClaimValues): Record<string, string> {
  const errs: Record<string, string> = {};

  if (!v.effectiveKind) {
    errs.claim_type = "Select what you're claiming for.";
  } else if (v.effectiveKind === "insured") {
    if (
      v.selectedProduct?.diagnosis_required &&
      !v.diagnosis.trim().replace(/^Other:\s*$/, "")
    ) {
      errs.diagnosis =
        "Select the diagnosis (choose 'Other' if it isn't listed).";
    }
    if (v.needsReferral) {
      if (!v.visitType) {
        errs.visit_type = "Tell us whether this is a first or follow-up visit.";
      }
      const referral = referralError(v);
      if (referral) errs.referral = referral;
    }
  }

  if (!v.incurredDate) {
    errs.incurred_date = "Enter the visit date.";
  } else if (
    // Both bounds or neither: an empty pair means the server served no window
    // for this kind (`claim_block`), and range-checking against "" produced
    // "Pick a date between — and —" for every date the member tried.
    v.claimableFrom &&
    v.claimableTo &&
    (v.incurredDate < v.claimableFrom || v.incurredDate > v.claimableTo)
  ) {
    errs.incurred_date = `Pick a date between ${formatDay(v.claimableFrom)} and ${formatDay(v.claimableTo)} — that's the period your benefits cover.`;
  } else if (v.incurredDate > v.today) {
    errs.incurred_date = "The incurred date can't be in the future.";
  }

  if (v.isHospitalisation) {
    if (!v.hospital) {
      errs.provider = "Select the hospital.";
    } else if (
      v.hospital === OTHER_HOSPITAL &&
      v.provider.trim().length < 2
    ) {
      errs.provider = "Enter the hospital name.";
    }
  } else if (v.provider.trim().length < 2) {
    errs.provider = "Enter the clinic or provider name.";
  }

  if (!v.invoiceNumber.trim()) {
    errs.invoice = "Enter the invoice or receipt number.";
  }

  if (v.requiresDoctorName && !v.doctorName.trim()) {
    errs.doctor_name = "Enter the name of the doctor you saw.";
  }

  const amount = Number(v.amount);
  if (!(amount > 0)) {
    errs.amount = "Enter the amount on the receipt.";
  } else if (amount > 1_000_000) {
    errs.amount = "Amount looks too large — check the receipt.";
  }

  // The ONLY currency block. There is no acceptance tick — sending the claim
  // with the converted figure on screen is the acceptance. But sending while
  // that figure is still UNKNOWN walks into the server's
  // `fx_confirmation_required` 409, so it waits for an answer first. "There is
  // no rate" is a settled answer and does NOT block: an outage must never stop
  // a member filing.
  if (v.fxBlocked) {
    errs.fx = "We're still checking the exchange rate — try again in a moment.";
  }

  for (const slot of v.docSlots) {
    if (!v.slotFiles[slot.key]) {
      errs[`slot_${slot.key}`] = `Attach the ${slot.label.toLowerCase()}.`;
    }
  }

  return errs;
}
