/** Which earlier visit this claim continues.
 *
 * Two claim types are continuations by definition: a pre-/post-hospitalisation
 * consult belongs to a hospital ADMISSION, and a specialist follow-up continues
 * the first visit of a course. Naming which one is what lets the claim be
 * assessed against it — and, as a side effect, what fills in the diagnosis and
 * the doctor the member would otherwise re-type every visit.
 *
 * Three rules this control has to keep:
 *
 * 1. **It is never required.** "This is for a new condition" is a complete
 *    answer, and so is finding nothing — a hospital stay settled by Letter of
 *    Guarantee may not be findable at all. Nothing here can block a claim.
 * 2. **No options, no question.** An empty picker with a lone "new condition"
 *    row is a step that wastes the member's time and teaches them the feature
 *    is broken.
 * 3. **It says where a visit came from.** A stay the broker recorded on the
 *    member's behalf is labelled as such, because "your visit" would be a claim
 *    about something they never filed. */
import { FieldGroup, leafControl } from "@/components/portal/leaf/Field";
import { formatDay } from "@/components/portal/leaf/date";
import type { ClaimAnchor } from "@/api/portal";
import type { NewClaimForm } from "./useNewClaimForm";

/** "Mount Elizabeth · 3–7 Mar 2026" — the stay when we hold both ends of it,
 * otherwise the one date we have. The range is what a member recognises: they
 * remember being in hospital for a week, not the date a bill was raised. */
function anchorLabel(a: ClaimAnchor): string {
  const where = a.provider_name?.trim() || "Hospital not recorded";
  const when =
    a.admission_date && a.discharge_date
      ? `${formatDay(a.admission_date)} – ${formatDay(a.discharge_date)}`
      : formatDay(a.admission_date ?? a.incurred_date);
  const parts = [where, when];
  if (a.diagnosis) parts.push(a.diagnosis);
  return parts.join(" · ");
}

export function AnchorField({ form }: { form: NewClaimForm }) {
  const { anchorMode, anchorOptions } = form;
  // Rule 2. Also covers the load: a picker that appears a beat late is better
  // than one that appears empty and then rearranges under a tapping finger.
  if (anchorMode === null || anchorOptions.length === 0) return null;

  const admission = anchorMode === "admission";
  return (
    <FieldGroup
      label={
        admission
          ? "Which hospital stay is this consultation for?"
          : "Which visit is this a follow-up to?"
      }
      hint={
        admission
          ? "Your insurer pays a pre- or post-hospitalisation consultation against the stay it belongs to, so telling us which one keeps them together."
          : "We'll carry over the condition and reuse the referral letter from that visit, so you don't have to enter them again."
      }
    >
      <select
        className={leafControl}
        aria-label={
          admission ? "Related hospital stay" : "Related specialist visit"
        }
        value={form.anchorId}
        onChange={(e) => form.changeAnchor(e.target.value)}
      >
        {anchorOptions.map((a) => (
          <option key={a.id} value={a.id}>
            {anchorLabel(a)}
            {a.from_records ? " (from our records)" : ""}
          </option>
        ))}
        {/* Last, and phrased as an answer rather than an absence — "None"
            reads as a failure to find something, which is exactly what this
            option is not. */}
        <option value="">
          {admission
            ? "Not related to a hospital stay"
            : "This is for a new condition"}
        </option>
      </select>
    </FieldGroup>
  );
}
