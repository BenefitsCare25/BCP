import { AlertCircle, CalendarRange, CheckCircle2, Cloud } from "lucide-react";
import { OTHER_HOSPITAL } from "./claimForm";
import type { NewClaimForm } from "./useNewClaimForm";

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
  const status =
    form.draftStatus === "saving"
      ? "Saving draft…"
      : form.draftStatus === "error"
        ? "Draft could not be saved"
        : form.draftStatus === "saved"
          ? "Draft saved"
          : "Draft saves automatically";

  return (
    <header className="space-y-4">
      <div className="space-y-1">
        <p className="leaf-label">Claim submission</p>
        <h1 className="text-xl font-semibold text-record">Make a claim</h1>
        <p className="flex items-center gap-2 text-row text-label">
          <CalendarRange className="size-4 shrink-0" aria-hidden />
          Benefit period {form.options.data?.policy_year_start} to{" "}
          {form.options.data?.policy_year_end}
        </p>
      </div>

      <div className="space-y-2" aria-label={`${completed} of 3 sections ready`}>
        <div className="flex items-center justify-between gap-3 text-row">
          <span className="font-medium text-record">Submission readiness</span>
          <span className="tabular-nums text-label">{completed} of 3 ready</span>
        </div>
        <div className="grid grid-cols-3 gap-2" aria-hidden>
          {steps.map((ready, index) => (
            <span
              key={index}
              className={`h-2 rounded-pill ${ready ? "bg-action" : "bg-shade"}`}
            />
          ))}
        </div>
        <p className="text-row text-label">
          Claim type · Visit details · Documents
        </p>
      </div>

      <div className="rounded-control border border-leaf-input bg-bar/55 px-3 py-2.5">
        <p
          className={`flex items-center gap-2 text-row font-medium ${
            form.draftStatus === "error" ? "text-strike-rejected" : "text-record"
          }`}
          aria-live="polite"
        >
          {form.draftStatus === "error" ? (
            <AlertCircle className="size-4 shrink-0" aria-hidden />
          ) : form.draftStatus === "saved" ? (
            <CheckCircle2 className="size-4 shrink-0" aria-hidden />
          ) : (
            <Cloud className="size-4 shrink-0" aria-hidden />
          )}
          {status}
        </p>
        <p className="mt-1 text-row text-label">
          Your answers can be resumed on this benefit period. For privacy,
          attachments remain on this device until you submit.
        </p>
        {form.draftRestored && (
          <p className="mt-1 text-row font-medium text-record">
            We restored your saved answers. Reattach the supporting files before sending.
          </p>
        )}
      </div>
    </header>
  );
}
