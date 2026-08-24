import { OTHER_HOSPITAL } from "./claimForm";
import type { NewClaimForm } from "./useNewClaimForm";

const STEP_LABELS = ["Claim type", "Visit details", "Documents"] as const;

export function ClaimSubmissionHeader({ form }: { form: NewClaimForm }) {
  const providerComplete = form.isHospitalisation
    ? Boolean(
        form.hospital &&
          (form.hospital !== OTHER_HOSPITAL || form.provider.trim().length >= 2),
      )
    : form.provider.trim().length >= 2;
  const visitComplete = Boolean(
    form.incurredDate &&
      providerComplete &&
      form.invoiceNumber.trim() &&
      Number(form.amount) > 0 &&
      (!form.needsReferral || form.visitType) &&
      (!form.requiresDoctorName || form.doctorName.trim()) &&
      (!form.selectedProduct?.diagnosis_required || form.diagnosis.trim()),
  );
  const referralComplete =
    !form.needsReferral ||
    (form.referralMode === "upload" && Boolean(form.referralFile)) ||
    (form.referralMode === "existing" && Boolean(form.referralExistingId));
  const documentsComplete = form.docSlots.every(
    (slot) => form.slotFiles[slot.key],
  ) && referralComplete;
  const steps = [Boolean(form.selection), visitComplete, documentsComplete];
  const completed = steps.filter(Boolean).length;

  return (
    <section
      className="space-y-2"
      aria-label={`${completed} of 3 sections ready`}
    >
      <div className="flex items-center justify-between gap-3 text-row">
        <span className="font-medium text-record">Submission readiness</span>
        <span className="tabular-nums text-label">{completed} of 3 ready</span>
      </div>
      <ol className="grid grid-cols-3 gap-2">
        {steps.map((ready, index) => (
          <li
            key={STEP_LABELS[index]}
            className="min-w-0 space-y-1.5"
            aria-label={`${STEP_LABELS[index]}: ${ready ? "ready" : "not ready"}`}
          >
            <span
              className={`block h-2 rounded-pill ${ready ? "bg-action" : "bg-shade"}`}
              aria-hidden
            />
            <span
              className={`block text-center text-2xs leading-tight sm:text-row ${
                ready ? "font-medium text-record" : "text-label"
              }`}
              aria-hidden
            >
              {STEP_LABELS[index]}
            </span>
          </li>
        ))}
      </ol>
    </section>
  );
}
