/** Section A of the paper form — the member's particulars.
 *
 * Everything the roster already knows is printed, not asked: retyping an NRIC
 * on every enrolment is how paper forms acquired typos. Only the two fields
 * the member is the authority on (how to reach them) are editable, and a
 * change to either is flagged on the signed PDF for the broker. */
import type { FormParticulars } from "@/api/enrollmentForms";
import { Field, leafControl } from "@/components/portal/leaf/Field";
import { Mount, MountRow, MountRule } from "@/components/portal/leaf/Mount";
import { formatDay } from "@/components/portal/leaf/date";

export interface ContactDraft {
  contactNo: string;
  email: string;
}

export function contactErrors(draft: ContactDraft): Partial<Record<keyof ContactDraft, string>> {
  const errors: Partial<Record<keyof ContactDraft, string>> = {};
  if (draft.contactNo.trim() && !/^[0-9 +()-]{6,32}$/.test(draft.contactNo.trim())) {
    errors.contactNo = "Use digits, spaces and + - ( ) only.";
  }
  const email = draft.email.trim();
  if (email && !/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(email)) {
    errors.email = "Enter a valid email address.";
  }
  return errors;
}

export function ParticularsMount({
  particulars,
  draft,
  disabled,
  onChange,
}: {
  particulars: FormParticulars;
  draft: ContactDraft;
  disabled: boolean;
  onChange: (next: ContactDraft) => void;
}) {
  const errors = contactErrors(draft);
  return (
    <Mount
      as="article"
      rise={false}
      label="Your details"
      gloss="From your company's records. Tell your HR team if any of this is wrong."
    >
      <dl>
        <MountRow term="Name">{particulars.name ?? "—"}</MountRow>
        <MountRow term="NRIC / FIN">{particulars.id_masked || "—"}</MountRow>
        <MountRow term="Staff ID">{particulars.staff_id}</MountRow>
        {particulars.gender && <MountRow term="Sex">{particulars.gender}</MountRow>}
        {particulars.dob && (
          <MountRow term="Date of birth">{formatDay(particulars.dob)}</MountRow>
        )}
        {particulars.job_grade && (
          <MountRow term="Job grade">{particulars.job_grade}</MountRow>
        )}
        {particulars.date_of_hire && (
          <MountRow term="Date joined">{formatDay(particulars.date_of_hire)}</MountRow>
        )}
      </dl>
      <MountRule />
      <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
        <Field label="Contact number" error={errors.contactNo}>
          {(p) => (
            <input
              {...p}
              type="tel"
              autoComplete="tel"
              className={leafControl}
              value={draft.contactNo}
              disabled={disabled}
              onChange={(e) => onChange({ ...draft, contactNo: e.target.value })}
            />
          )}
        </Field>
        <Field label="Email" error={errors.email}>
          {(p) => (
            <input
              {...p}
              type="email"
              autoComplete="email"
              className={leafControl}
              value={draft.email}
              disabled={disabled}
              onChange={(e) => onChange({ ...draft, email: e.target.value })}
            />
          )}
        </Field>
      </div>
    </Mount>
  );
}
