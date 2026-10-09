import { usePortalTranslation } from "@/i18n/portal";
/** "Your signed forms" — every enrolment form the member signed here, newest
 * first, each downloadable as the PDF their broker and HR received. Shown on
 * the enrolment page whether or not a period is open: the record outlives it. */
import { useMyEnrollmentForms } from "@/api/portalEnrollmentForms";
import { Mount } from "@/components/portal/leaf/Mount";
import { SignedFormLine } from "./SignedFormLine";

export function MyFormsMount() {
  const pt = usePortalTranslation();
  const forms = useMyEnrollmentForms();
  const items = forms.data ?? [];
  if (forms.isPending || forms.isError || items.length === 0) return null;
  return (
    <Mount
      label={pt("Your signed forms")}
      gloss="A copy of each enrolment form you've signed. A newer version replaces the one before."
    >
      <ul className="divide-y divide-hairline/75">
        {items.map((form) => (
          <li key={form.id} className="py-2.5">
            <SignedFormLine form={form} />
          </li>
        ))}
      </ul>
    </Mount>
  );
}
