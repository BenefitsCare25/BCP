/** One signed form as a line: reference, when, where it stands, and the PDF.
 * Shared by the sign step ("Last signed") and the member's forms list. */
import { FileDown, Loader2 } from "lucide-react";
import { useState } from "react";
import { toast } from "sonner";
import type { FormSubmissionSummary } from "@/api/enrollmentForms";
import { downloadMyEnrollmentForm } from "@/api/portalEnrollmentForms";
import { Strike } from "@/components/portal/leaf/Strike";
import { formatDay } from "@/components/portal/leaf/date";
import { formatError } from "@/lib/errors";

function standing(form: FormSubmissionSummary): {
  label: string;
  tone: "approved" | "review" | "pending";
} {
  if (form.status === "superseded") return { label: "Replaced", tone: "pending" };
  if (form.status === "cancelled") return { label: "Cancelled", tone: "pending" };
  if (form.status === "returned") return { label: "Needs correction", tone: "review" };
  if (form.source === "portal" && ["not_started", "in_progress", "returned"].includes(form.enrollment_status ?? "")) {
    return { label: "No longer current", tone: "pending" };
  }
  if (form.enrollment_status === "confirmed" || form.enrollment_status === "deemed") {
    return { label: "Confirmed", tone: "approved" };
  }
  if (form.status === "acknowledged") return { label: "Received", tone: "approved" };
  return { label: "Sent", tone: "review" };
}

export function SignedFormLine({
  form,
  lead,
}: {
  form: FormSubmissionSummary;
  lead?: string;
}) {
  const [busy, setBusy] = useState(false);
  const state = standing(form);

  async function download() {
    setBusy(true);
    try {
      await downloadMyEnrollmentForm(form);
    } catch (e) {
      toast.error(formatError(e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="flex flex-col gap-2 sm:flex-row sm:items-center sm:justify-between">
      <div className="min-w-0">
        <p className="text-row text-record">
          {lead ? `${lead}: ` : ""}
          <span className="font-medium">{form.reference_no}</span>
          {form.version > 1 ? ` (version ${form.version})` : ""}
        </p>
        <p className="text-row text-label">Signed {formatDay(form.submitted_at)}</p>
      </div>
      <div className="flex items-center gap-3">
        <Strike tone={state.tone}>{state.label}</Strike>
        {form.has_pdf && (
          <button
            type="button"
            onClick={() => void download()}
            disabled={busy}
            className="leaf-focus inline-flex min-h-11 items-center gap-1.5 text-row font-medium text-action-ink"
          >
            {busy ? (
              <Loader2 className="size-4 animate-spin" aria-hidden />
            ) : (
              <FileDown className="size-4" aria-hidden />
            )}
            PDF
          </button>
        )}
      </div>
    </div>
  );
}
